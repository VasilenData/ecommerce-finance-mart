"""Independent arithmetic, policy boundaries, temporal netting and request identity."""
import copy
import json

import pytest

from mart.subscription_restatements import compare_reports
from mart.subscription_review import (
    POLICIES,
    build_review_packet,
    canonical_sha256,
    invoice_exposure,
    materiality_reasons,
    verify_request,
)
from mart.subscriptions import DEFAULT_CUTOFFS, ROOT, build_report, digest, publish


@pytest.fixture
def source():
    return json.loads((ROOT / "subscriptions/demo.json").read_text(encoding="utf-8"))


def packet(source, policy=POLICIES[-1], before=DEFAULT_CUTOFFS[0], after=DEFAULT_CUTOFFS[-1]):
    baseline, candidate = build_report(source, before), build_report(source, after)
    audit = compare_reports(source, baseline, candidate)
    return build_review_packet("a" * 64, baseline, candidate, audit, policy)


def test_net_zero_must_not_hide_unpaid_invoice_and_credit():
    source = {"schema_version": 1, "currency": "GBP", "scope": "synthetic", "complete_subscription_history": True,
              "subscription_changes": [], "invoices": [], "payments": [], "refunds": []}
    for name, customer in (("unpaid", "A"), ("credit", "B")):
        source["invoices"].append({"record_id": name, "revision": 1, "recorded_at": "2026-01-02T00:00:00Z",
                                   "customer_id": customer, "issued_date": "2026-01-01", "amount_cents": 10000})
    source["payments"].append({"record_id": "overpayment", "revision": 1, "recorded_at": "2026-01-03T00:00:00Z",
                               "invoice_id": "credit", "paid_date": "2026-01-02", "amount_cents": 20000})
    # Independently: A owes 100 - 0 = 100. B has 100 - 200 = -100.
    # Summing first hides the £100 unpaid amount, although net 0 is arithmetically correct.
    report = build_report(source, DEFAULT_CUTOFFS[0])
    exposure = invoice_exposure(report)
    assert exposure["gross_unpaid_cents"] == 10000
    assert exposure["credit_magnitude_cents"] == 10000
    assert exposure["net_balance_cents"] == 0
    assert exposure["unpaid_invoice_count"] == exposure["credit_invoice_count"] == 1
    assert exposure["invoices"] == [
        {"invoice_id": "credit", "customer_id": "B", "balance_cents": -10000, "classification": "credit"},
        {"invoice_id": "unpaid", "customer_id": "A", "balance_cents": 10000, "classification": "unpaid"}]


@pytest.mark.parametrize(("before", "after", "absolute", "relative", "expected"), [
    (10000, 12499, 2500, None, []),
    (10000, 12500, 2500, None, ["absolute threshold"]),
    (10000, 10500, 2500, 500, ["relative threshold"]),
    (10000, 10499, 2500, 500, []),
    (-10000, -9500, 2500, 500, ["relative threshold"]),
    (0, 1, 2500, 500, ["changed from zero; relative percentage undefined"]),
    (0, 0, 2500, 500, []),
    (0, 1, 2500, None, []),
    (10000, 7500, 2500, 500, ["absolute threshold", "relative threshold"]),
])
def test_inclusive_thresholds_without_rounding_or_zero_division(before, after, absolute, relative, expected):
    assert materiality_reasons(before, after, absolute, relative) == expected


@pytest.mark.parametrize("arguments", [(True, 100, 1), (0, 2**53, 1), (0, 1, 0), (0, 1, True), (0, 1, 1, 0), (0, 1, 1, 10001)])
def test_unsafe_values_and_ambiguous_policies_fail(arguments):
    with pytest.raises(ValueError):
        materiality_reasons(*arguments)


def test_monthly_cash_correction_cannot_disappear_in_quarter_netting(source):
    source["payments"].append({**source["payments"][0], "revision": 2,
                               "recorded_at": "2026-04-05T00:00:00Z", "paid_date": "2026-02-02"})
    result = packet(source, before=DEFAULT_CUTOFFS[-1], after="2026-04-06T00:00:00Z")
    changes = [row for row in result["changed_metric_cells"] if row["metric"] == "net_cash_cents"]
    assert [(row["entity"], row["delta_cents"], row["triage"]) for row in changes] == [
        ("2026-01-01", -10000, "review_requested"), ("2026-02-01", 10000, "review_requested")]
    assert result["period_end_exposure"]["net_balance_cents"] == 5000


def test_demo_thresholds_have_hand_computed_consequences_without_approving(source):
    strict = packet(source, POLICIES[0])
    absolute = packet(source, POLICIES[1])
    relative = packet(source, POLICIES[2])
    # Every changed cell is £10: all are below £25, but March MRR changes
    # 180->190 (>5%); February closing 0->10 (undefined%); March closing and
    # period-end net/unpaid 40->50 (25%); B invoice 0->10 (undefined%).
    assert strict["flagged_metric_cell_count"] == 8
    assert absolute["flagged_metric_cell_count"] == 0
    assert relative["flagged_metric_cell_count"] == 6
    assert {(row["grain"], row["entity"], row["metric"]) for row in relative["changed_metric_cells"]
            if row["triage"] == "review_requested"} == {
                ("month", "2026-03-01", "closing_mrr_cents"),
                ("month", "2026-02-01", "closing_balance_cents"), ("month", "2026-03-01", "closing_balance_cents"),
                ("period end", "2026-04-01", "gross_unpaid_cents"), ("period end", "2026-04-01", "net_balance_cents"),
                ("period-end invoice", "invoice-B-2", "balance_cents")}
    assert all(item["review_status"] == "pending_human_review" for item in (strict, absolute, relative))
    assert all(item["feedback_template"]["reviewer"] is None for item in (strict, absolute, relative))
    assert len({item["request_sha256"] for item in (strict, absolute, relative)}) == 3


def test_same_cutoff_is_pending_definition_review_even_with_no_deltas(source):
    result = packet(source, after=DEFAULT_CUTOFFS[0])
    assert result["changed_metric_cells"] == []
    assert result["source_record_change_count"] == result["flagged_metric_cell_count"] == 0
    assert result["review_status"] == "pending_human_review"
    assert result["questions"]


def test_exact_packet_identity_and_read_only_inputs(source):
    unchanged = copy.deepcopy(source)
    result = packet(source)
    assert source == unchanged
    assert verify_request(result)
    assert packet(source) == result
    altered = copy.deepcopy(result)
    altered["review_status"] = "approved"
    assert not verify_request(altered)
    altered = copy.deepcopy(result)
    altered["candidate_cutoff"] = DEFAULT_CUTOFFS[0]
    assert not verify_request(altered)
    assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256({"b": 2, "a": 1})


def test_exposure_rejects_duplicate_grain_wrong_ledger_and_unsafe_gross(source):
    report = build_report(source, DEFAULT_CUTOFFS[0])
    report["invoice_balances"].append(copy.deepcopy(report["invoice_balances"][0]))
    with pytest.raises(ValueError, match="unique invoice"):
        invoice_exposure(report)
    report["invoice_balances"].pop()
    report["monthly_cash_bridge"][-1]["closing_balance_cents"] += 1
    with pytest.raises(ValueError, match="does not reconcile"):
        invoice_exposure(report)
    for row in report["invoice_balances"]:
        row["balance_cents"] = 2**53 - 1
    with pytest.raises(ValueError, match="gross unpaid"):
        invoice_exposure(report)


def test_review_rejects_stale_audit_and_failed_reconciliation(source):
    before, after = [build_report(source, cutoff) for cutoff in (DEFAULT_CUTOFFS[0], DEFAULT_CUTOFFS[-1])]
    audit = compare_reports(source, before, after)
    wrong = {**audit, "candidate_cutoff": DEFAULT_CUTOFFS[0]}
    with pytest.raises(ValueError, match="audit cutoffs"):
        build_review_packet("a" * 64, before, after, wrong, POLICIES[0])
    audit["checks"][0]["passed"] = False
    with pytest.raises(ValueError, match="passed restatement checks"):
        build_review_packet("a" * 64, before, after, audit, POLICIES[0])


def test_published_requests_are_bound_to_artifacts_and_preserve_history(tmp_path):
    historical = ROOT / "docs/subscriptions/2026-11-12-restatement/manifest.json"
    original_hash = digest(historical)
    output = tmp_path / "review"
    manifest = publish(ROOT / "subscriptions/demo.json", output)
    bundle = json.loads((output / "report.json").read_text(encoding="utf-8"))
    requests = json.loads((output / "review_requests.json").read_text(encoding="utf-8"))
    assert requests["requests"] == bundle["review_packets"]
    assert manifest["review_requests"] == len(requests["requests"]) == 9
    assert manifest["invoice_exposure_checks_passed"] == 3
    assert manifest["human_review_status"] == "pending"
    assert all(verify_request(item) and item["source_sha256"] == digest(output / "source.json") for item in requests["requests"])
    assert all(digest(output / item["path"]) == item["sha256"] for item in manifest["artifacts"])
    assert digest(historical) == original_hash
