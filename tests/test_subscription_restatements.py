"""Hand-calculated restatement differences, provenance and release integrity."""
import copy
import csv
import json
import random

import pytest

from mart.subscription_restatements import compare_reports
from mart.subscriptions import DEFAULT_CUTOFFS, FEEDS, ROOT, build_report, digest, publish


@pytest.fixture
def source():
    return json.loads((ROOT / "subscriptions/demo.json").read_text(encoding="utf-8"))


def comparison(source, before=DEFAULT_CUTOFFS[0], after=DEFAULT_CUTOFFS[-1]):
    return compare_reports(source, build_report(source, before), build_report(source, after))


def select(result, table, metric):
    return [row for row in result["monetary_deltas"] if row["table"] == table and row["metric"] == metric]


def test_rate_revision_explains_customer_and_months_without_changing_cash(source):
    result = comparison(source, after=DEFAULT_CUTOFFS[1])
    assert [(row["feed"], row["record_id"]) for row in result["source_changes"]] == [("subscription_changes", "b-contract")]
    revision = result["source_changes"][0]
    assert revision["before"]["monthly_amount_cents"] == 5000
    assert revision["after"]["monthly_amount_cents"] == 6000
    assert [(row["customer_id"], row["month"], row["before_cents"], row["after_cents"], row["delta_cents"])
            for row in select(result, "customer_mrr_bridge", "closing_mrr_cents")] == [
                ("B", "2026-02-01", 5000, 6000, 1000), ("B", "2026-03-01", 5000, 6000, 1000)]
    assert not any(row["table"] in ("monthly_cash_bridge", "invoice_balances") for row in result["monetary_deltas"])
    assert len(result["checks"]) == 22
    assert all(check["passed"] for check in result["checks"])


def test_invoice_revision_changes_billed_and_balance_but_never_cash_or_mrr(source):
    result = comparison(source, before=DEFAULT_CUTOFFS[1])
    assert [(row["feed"], row["record_id"]) for row in result["source_changes"]] == [("invoices", "invoice-B-2")]
    assert not any(row["table"] in ("monthly_mrr_bridge", "customer_mrr_bridge") for row in result["monetary_deltas"])
    assert not select(result, "monthly_cash_bridge", "net_cash_cents")
    assert [(row["invoice_id"], row["before_cents"], row["after_cents"], row["delta_cents"])
            for row in select(result, "invoice_balances", "balance_cents")] == [("invoice-B-2", 0, 1000, 1000)]
    assert [(row["month"], row["delta_cents"]) for row in select(result, "monthly_cash_bridge", "closing_balance_cents")] == [
        ("2026-02-01", 1000), ("2026-03-01", 1000)]


def test_payment_date_shift_changes_months_but_not_quarter_cash(source):
    source["payments"].append({**source["payments"][0], "revision": 2,
                               "recorded_at": "2026-04-05T00:00:00Z", "paid_date": "2026-02-02"})
    result = comparison(source, before=DEFAULT_CUTOFFS[-1], after="2026-04-06T00:00:00Z")
    assert [(row["month"], row["delta_cents"]) for row in select(result, "monthly_cash_bridge", "net_cash_cents")] == [
        ("2026-01-01", -10000), ("2026-02-01", 10000)]
    assert sum(row["delta_cents"] for row in select(result, "monthly_cash_bridge", "net_cash_cents")) == 0
    assert not any(row["table"] == "invoice_balances" for row in result["monetary_deltas"])
    assert result["source_changes"][0]["changed_fields"] == ["paid_date", "recorded_at", "revision"]


def test_newly_visible_invoice_has_explicit_absent_baseline(source):
    source["invoices"].append({"record_id": "late-D", "revision": 1, "recorded_at": "2026-04-05T00:00:00Z",
                                "customer_id": "D", "issued_date": "2026-03-15", "amount_cents": 1200})
    result = comparison(source, before=DEFAULT_CUTOFFS[-1], after="2026-04-06T00:00:00Z")
    assert result["source_changes"][0]["change"] == "newly_visible"
    assert result["source_changes"][0]["before"] is None
    row = select(result, "invoice_balances", "balance_cents")[0]
    assert row["before_present"] is False
    assert row["after_present"] is True
    assert row["delta_cents"] == 1200


def test_same_cutoff_has_no_changes_and_future_revisions_are_absent(source):
    result = comparison(source, after=DEFAULT_CUTOFFS[0])
    assert result["source_changes"] == result["monetary_deltas"] == []
    assert result["checks"][-1]["actual"] == 0
    assert "2026-04-02T12:00:00Z" not in json.dumps(result)


def test_comparison_preserves_inputs_and_is_permutation_replay_invariant(source):
    untouched = copy.deepcopy(source)
    expected = comparison(source)
    assert source == untouched
    rng = random.Random(31)
    for feed in FEEDS:
        source[feed] += copy.deepcopy(source[feed])
        rng.shuffle(source[feed])
    assert comparison(source) == expected


def test_incompatible_windows_and_reversed_cutoffs_fail(source):
    baseline = build_report(source, DEFAULT_CUTOFFS[0])
    candidate = build_report(source, DEFAULT_CUTOFFS[-1], "2026-02-01")
    with pytest.raises(ValueError, match="same period_start"):
        compare_reports(source, baseline, candidate)
    with pytest.raises(ValueError, match="must not precede"):
        compare_reports(source, build_report(source, DEFAULT_CUTOFFS[-1]), baseline)


def test_corrupted_customer_delta_is_blocked_by_reconciliation(source):
    before, after = [build_report(source, cutoff) for cutoff in (DEFAULT_CUTOFFS[0], DEFAULT_CUTOFFS[-1])]
    after["customer_mrr_bridge"][0]["closing_mrr_cents"] += 1
    with pytest.raises(ValueError, match="customer deltas reconcile"):
        compare_reports(source, before, after)


def test_release_restated_downloads_share_source_and_hashes(tmp_path):
    output = tmp_path / "release"
    manifest = publish(ROOT / "subscriptions/demo.json", output)
    bundle = json.loads((output / "report.json").read_text())
    restatements = json.loads((output / "restatements.json").read_text())
    assert restatements["source_sha256"] == bundle["source_sha256"] == digest(output / "source.json")
    assert restatements["restatements"] == bundle["restatements"]
    assert manifest["restatement_checks_passed"] == 44
    with (output / "restatement_deltas.csv").open(newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    expected = [row for item in bundle["restatements"] for row in item["monetary_deltas"]]
    assert len(csv_rows) == len(expected)
    assert [int(row["delta_cents"]) for row in csv_rows] == [row["delta_cents"] for row in expected]
    for entry in manifest["artifacts"]:
        assert digest(output / entry["path"]) == entry["sha256"]


def test_single_snapshot_still_has_empty_audit_downloads(tmp_path):
    output = tmp_path / "single"
    manifest = publish(ROOT / "subscriptions/demo.json", output, [DEFAULT_CUTOFFS[0]])
    assert manifest["restatement_checks_passed"] == 0
    assert json.loads((output / "restatements.json").read_text())["restatements"] == []
    assert len((output / "restatement_deltas.csv").read_text().splitlines()) == 1
