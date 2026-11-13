"""Source-bound, read-only close-review requests; never stakeholder approvals.

Materiality here is an illustrative triage policy, not accounting materiality.
All amounts remain integer pence and comparisons use exact integer arithmetic.
"""
from __future__ import annotations

import hashlib
import json
import re

MAX_INTEGER = 2**53 - 1
POLICIES = (
    {"id": "every-change", "label": "Every changed penny", "absolute_cents": 1, "relative_basis_points": None},
    {"id": "absolute-25", "label": "At least £25", "absolute_cents": 2500, "relative_basis_points": None},
    {"id": "absolute-25-or-5pct", "label": "At least £25 or 5%", "absolute_cents": 2500, "relative_basis_points": 500},
)
QUESTIONS = [
    "Do the complete-history assertion, knowledge cutoffs and invoice/payment relationships match the intended source contract?",
    "Should a cash refund reopen the invoice obligation, or is a separate credit-note contract needed?",
    "Are invoice credits transferable or refundable? No offset or collections action is authorized by this report.",
    "Which changed months, customer commitments or invoices need source evidence before a corrected close is used?",
    "Is the proposed triage threshold appropriate? A small or unchanged result is not proof of correctness or approval.",
]


def canonical_sha256(value: dict) -> str:
    """Stable content identity, independent of whitespace and dictionary order."""
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def bounded_integer(value, label: str) -> int:
    if type(value) is not int or abs(value) > MAX_INTEGER:
        raise ValueError(f"{label} must be an exact browser-safe integer")
    return value


def materiality_reasons(before: int, after: int, absolute_cents: int, relative_basis_points: int | None = None) -> list[str]:
    """Inclusive absolute OR relative thresholds; no rounded percentage decision.

    A change from zero has an undefined relative percentage and always requests
    review when a relative policy is enabled. An unchanged zero is never flagged.
    """
    before, after = bounded_integer(before, "before"), bounded_integer(after, "after")
    if type(absolute_cents) is not int or not 1 <= absolute_cents <= MAX_INTEGER:
        raise ValueError("absolute threshold must be a positive bounded integer")
    if relative_basis_points is not None and (type(relative_basis_points) is not int or not 1 <= relative_basis_points <= 10000):
        raise ValueError("relative threshold must be 1..10000 basis points, or None")
    delta = bounded_integer(after - before, "delta")
    if delta == 0:
        return []
    reasons = ["absolute threshold"] if abs(delta) >= absolute_cents else []
    if relative_basis_points is not None:
        if before == 0:
            reasons.append("changed from zero; relative percentage undefined")
        elif abs(delta) * 10000 >= abs(before) * relative_basis_points:
            reasons.append("relative threshold")
    return reasons


def invoice_exposure(report: dict) -> dict:
    """Keep positive invoice obligations and credits separate before netting.

    Scope is the report's period end, not the explorer's selected month. Credit
    magnitude is positive; gross unpaid minus credits must equal the cash ledger.
    """
    balances, seen = [], set()
    for row in report["invoice_balances"]:
        if row["invoice_id"] in seen:
            raise ValueError("invoice exposure requires unique invoice grain")
        seen.add(row["invoice_id"])
        balance = bounded_integer(row["balance_cents"], "invoice balance")
        if balance:
            balances.append({"invoice_id": row["invoice_id"], "customer_id": row["customer_id"],
                             "balance_cents": balance, "classification": "unpaid" if balance > 0 else "credit"})
    balances.sort(key=lambda row: row["invoice_id"])
    unpaid = bounded_integer(sum(row["balance_cents"] for row in balances if row["balance_cents"] > 0), "gross unpaid")
    credits = bounded_integer(-sum(row["balance_cents"] for row in balances if row["balance_cents"] < 0), "credit magnitude")
    net = bounded_integer(unpaid - credits, "net invoice balance")
    ledger = bounded_integer(report["monthly_cash_bridge"][-1]["closing_balance_cents"], "cash ledger balance")
    if net != ledger:
        raise ValueError("gross unpaid minus credits does not reconcile to the period-end cash ledger")
    return {"period_start": report["period_start"], "period_end_exclusive": report["period_end"],
            "knowledge_cutoff": report["knowledge_cutoff"], "grain": "invoice", "unit": "integer pence",
            "gross_unpaid_cents": unpaid, "credit_magnitude_cents": credits, "net_balance_cents": net,
            "unpaid_invoice_count": sum(row["balance_cents"] > 0 for row in balances),
            "credit_invoice_count": sum(row["balance_cents"] < 0 for row in balances), "invoices": balances,
            "check": {"check": "gross unpaid - credit magnitude = period-end cash ledger", "actual": net,
                      "expected": ledger, "passed": True}}


def review_metrics(report: dict, exposure: dict) -> dict:
    """Do not let opposite changes across months or invoices cancel in triage."""
    result = {}
    for table, fields in (("monthly_mrr_bridge", ("closing_mrr_cents",)),
                          ("monthly_cash_bridge", ("invoiced_cents", "net_cash_cents", "closing_balance_cents"))):
        for row in report[table]:
            for metric in fields:
                key = ("month", row["month"], metric)
                if key in result:
                    raise ValueError("close review requires unique monthly grain")
                result[key] = bounded_integer(row[metric], metric)
    for row in report["invoice_balances"]:
        result[("period-end invoice", row["invoice_id"], "balance_cents")] = bounded_integer(row["balance_cents"], "balance")
    for metric in ("gross_unpaid_cents", "credit_magnitude_cents", "net_balance_cents"):
        result[("period end", report["period_end"], metric)] = exposure[metric]
    return result


def build_review_packet(source_sha256: str, baseline: dict, candidate: dict, audit: dict, policy: dict) -> dict:
    """Generate one request tied to exact source, reports, audit and proposed policy.

    The content digest is for reproducibility, not a signature or verified human
    identity. No policy or passing check can transition this request to approved.
    """
    if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("source_sha256 must identify frozen source bytes")
    if policy not in POLICIES:
        raise ValueError("choose a documented illustrative policy")
    materiality_reasons(0, 0, policy["absolute_cents"], policy["relative_basis_points"])
    for key in ("period_start", "period_end", "currency", "unit", "scope"):
        if baseline[key] != candidate[key]:
            raise ValueError(f"review requires the same {key}")
    if (audit["baseline_cutoff"] != baseline["knowledge_cutoff"]
            or audit["candidate_cutoff"] != candidate["knowledge_cutoff"]):
        raise ValueError("audit cutoffs do not identify these reports")
    if not audit["checks"] or not all(check["passed"] for check in audit["checks"]):
        raise ValueError("review requires passed restatement checks")
    old_exposure, new_exposure = invoice_exposure(baseline), invoice_exposure(candidate)
    old, new = review_metrics(baseline, old_exposure), review_metrics(candidate, new_exposure)
    changes = []
    for grain, entity, metric in sorted(old.keys() | new.keys()):
        key = (grain, entity, metric)
        before, after = old.get(key, 0), new.get(key, 0)
        if before == after:
            continue
        reasons = materiality_reasons(before, after, policy["absolute_cents"], policy["relative_basis_points"])
        changes.append({"grain": grain, "entity": entity, "metric": metric, "before_cents": before,
                        "after_cents": after, "delta_cents": after - before,
                        "before_present": key in old, "after_present": key in new,
                        "triage": "review_requested" if reasons else "below_proposed_threshold", "reasons": reasons})
    packet = {"schema_version": 1, "scope": "synthetic", "review_status": "pending_human_review",
              "source_sha256": source_sha256, "baseline_report_sha256": canonical_sha256(baseline),
              "candidate_report_sha256": canonical_sha256(candidate), "audit_sha256": canonical_sha256(audit),
              "baseline_cutoff": baseline["knowledge_cutoff"], "candidate_cutoff": candidate["knowledge_cutoff"],
              "period_start": candidate["period_start"], "period_end_exclusive": candidate["period_end"],
              "currency": candidate["currency"], "unit": "integer pence", "proposed_policy": dict(policy),
              "comparison_scope": "Monthly closing MRR, billed, net cash and closing balance; each invoice balance; period-end exposure.",
              "policy_limit": "Illustrative triage only. A below-threshold or unchanged result is not approval. No ledger values change.",
              "relative_rule": "Absolute OR relative, inclusive; absolute baseline denominator; nonzero change from zero requests review.",
              "changed_metric_cells": changes,
              "flagged_metric_cell_count": sum(row["triage"] == "review_requested" for row in changes),
              "source_record_change_count": len(audit["source_changes"]), "period_end_exposure": new_exposure,
              "questions": list(QUESTIONS),
              "feedback_template": {"reviewer": None, "reviewed_at": None, "definition_challenges": [],
                                    "evidence_requested": [], "proposed_changes": [], "decision_rationale": None},
              "authorship_limit": "An exported request is not evidence that anyone reviewed, accepted or used these outputs."}
    return {**packet, "request_sha256": canonical_sha256(packet)}


def verify_request(packet: dict) -> bool:
    """Detect accidental edits or stale identities, not malicious signatures."""
    return packet.get("request_sha256") == canonical_sha256({key: value for key, value in packet.items() if key != "request_sha256"})
