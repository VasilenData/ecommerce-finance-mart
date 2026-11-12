"""Explain report restatements without confusing new knowledge with new cash.

The audit compares report cells and the source revisions visible at two cutoffs.
It does not allocate causal impact among simultaneous source changes.
"""
from __future__ import annotations

from mart.subscriptions import EXPORT_FIELDS, FEEDS, MRR_FIELDS, select_snapshot, timestamp, validate_source

GRAINS = {
    "monthly_mrr_bridge": ("month",),
    "customer_mrr_bridge": ("month", "customer_id"),
    "monthly_cash_bridge": ("month",),
    "invoice_balances": ("invoice_id",),
}


def _index(rows, keys):
    indexed = {}
    for row in rows:
        key = tuple(row[name] for name in keys)
        if key in indexed:
            raise ValueError(f"Restatement audit requires a unique grain: {keys}")
        indexed[key] = row
    return indexed


def _monetary_deltas(before, after, table):
    keys = GRAINS[table]
    left, right = _index(before[table], keys), _index(after[table], keys)
    output = []
    for key in sorted(left.keys() | right.keys()):
        old, new = left.get(key), right.get(key)
        for metric in EXPORT_FIELDS[table]:
            if not metric.endswith("_cents"):
                continue
            previous, current = old[metric] if old else 0, new[metric] if new else 0
            if previous == current:
                continue
            delta = current - previous
            if abs(delta) > 2**53 - 1:
                raise ValueError("Restatement delta exceeds exact browser integer range")
            output.append({"table": table, **dict(zip(keys, key, strict=True)), "metric": metric,
                           "before_cents": previous, "after_cents": current, "delta_cents": delta,
                           "before_present": old is not None, "after_present": new is not None})
    return output


def compare_reports(source: dict, baseline: dict, candidate: dict) -> dict:
    """Compare two checked reports for the same window, in increasing knowledge time."""
    source = validate_source(source)
    for field in ("period_start", "period_end", "currency", "unit", "scope"):
        if baseline[field] != candidate[field]:
            raise ValueError(f"Restatement comparison requires the same {field}")
    before_time, after_time = timestamp(baseline["knowledge_cutoff"]), timestamp(candidate["knowledge_cutoff"])
    if before_time > after_time:
        raise ValueError("Candidate knowledge cutoff must not precede baseline")
    before, after = select_snapshot(source, before_time), select_snapshot(source, after_time)
    changes = []
    for feed in FEEDS:
        for identity in sorted(before[feed].keys() | after[feed].keys()):
            old, new = before[feed].get(identity), after[feed].get(identity)
            if old == new:
                continue
            changed_fields = [field for field in sorted(new or old) if (old or {}).get(field) != (new or {}).get(field)]
            changes.append({"feed": feed, "record_id": identity,
                            "change": "newly_visible" if old is None else "revision",
                            "changed_fields": changed_fields, "before": old, "after": new})
    deltas = [row for table in GRAINS for row in _monetary_deltas(baseline, candidate, table)]
    checks = []

    def check(name, actual, expected):
        if actual != expected:
            raise ValueError(f"Restatement {name}: expected {expected}, found {actual}")
        checks.append({"check": name, "actual": actual, "expected": expected, "passed": True})

    for monthly in candidate["monthly_mrr_bridge"]:
        month = monthly["month"]
        for metric in MRR_FIELDS:
            customer_delta = sum(row["delta_cents"] for row in deltas
                                 if row["table"] == "customer_mrr_bridge" and row["month"] == month and row["metric"] == metric)
            total_delta = sum(row["delta_cents"] for row in deltas
                              if row["table"] == "monthly_mrr_bridge" and row["month"] == month and row["metric"] == metric)
            check(f"{month}: customer deltas reconcile to {metric}", customer_delta, total_delta)
    invoice_delta = sum(row["delta_cents"] for row in deltas if row["table"] == "invoice_balances" and row["metric"] == "balance_cents")
    closing_delta = (candidate["monthly_cash_bridge"][-1]["closing_balance_cents"]
                     - baseline["monthly_cash_bridge"][-1]["closing_balance_cents"])
    check("invoice deltas reconcile to period closing balance", invoice_delta, closing_delta)
    if not changes:
        check("unchanged selected source has no monetary restatement", len(deltas), 0)
    return {"baseline_cutoff": baseline["knowledge_cutoff"], "candidate_cutoff": candidate["knowledge_cutoff"],
            "period_start": baseline["period_start"], "period_end": baseline["period_end"],
            "currency": baseline["currency"], "scope": "synthetic", "source_changes": changes,
            "monetary_deltas": deltas, "checks": checks,
            "interpretation": "Net report differences and visible source revisions; not causal allocation among simultaneous changes."}
