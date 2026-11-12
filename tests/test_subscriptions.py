"""Independent hand-computed accounting examples and temporal/release failures."""
import copy
import csv
import json
import random

import pytest

from mart import subscription_report
from mart.subscriptions import DEFAULT_CUTOFFS, FEEDS, ROOT, build_report, digest, publish


@pytest.fixture
def source():
    return json.loads((ROOT / "subscriptions/demo.json").read_text(encoding="utf-8"))


def report(source, cutoff=DEFAULT_CUTOFFS[0]):
    return build_report(source, cutoff)


def test_hand_computed_customer_month_bridge(source):
    result = report(source)
    fields = ["opening_mrr_cents", "new_mrr_cents", "expansion_mrr_cents", "reactivation_mrr_cents",
              "contraction_mrr_cents", "churn_mrr_cents", "closing_mrr_cents"]
    # Jan: A=100, B=80. Feb: D adds40, A expands50, B contracts30.
    # Mar: A churns150; C returns at90 (its February start/cancel netted to0).
    expected = [[0, 18000, 0, 0, 0, 0, 18000],
                [18000, 4000, 5000, 0, 3000, 0, 24000],
                [24000, 0, 0, 9000, 0, 15000, 18000]]
    assert [[row[key] for key in fields] for row in result["monthly_mrr_bridge"]] == expected
    a_january = next(row for row in result["customer_mrr_bridge"] if row["customer_id"] == "A" and row["month"] == "2026-01-01")
    assert a_january["new_mrr_cents"] == 10000  # Second A subscription ended within January.
    c_march = next(row for row in result["customer_mrr_bridge"] if row["customer_id"] == "C" and row["month"] == "2026-03-01")
    assert c_march["reactivation_mrr_cents"] == 9000
    assert c_march["new_mrr_cents"] == 0


def test_hand_computed_cash_without_payment_fanout(source):
    result = report(source)
    assert result["monthly_cash_bridge"] == [
        {"month": "2026-01-01", "opening_balance_cents": 0, "invoiced_cents": 18000,
         "paid_cents": 18000, "refunded_cents": 0, "net_cash_cents": 18000, "closing_balance_cents": 0},
        {"month": "2026-02-01", "opening_balance_cents": 0, "invoiced_cents": 24000,
         "paid_cents": 24000, "refunded_cents": 0, "net_cash_cents": 24000, "closing_balance_cents": 0},
        {"month": "2026-03-01", "opening_balance_cents": 0, "invoiced_cents": 33000,
         "paid_cents": 31000, "refunded_cents": 2000, "net_cash_cents": 29000, "closing_balance_cents": 4000},
    ]
    balances = {row["invoice_id"]: row for row in result["invoice_balances"]}
    assert balances["invoice-A-2"]["invoiced_cents"] == 15000  # Two payments; invoice counted once.
    assert balances["invoice-A-2"]["paid_cents"] == 15000
    assert balances["invoice-A-3"]["balance_cents"] == 2000  # Refund reopens obligation.
    assert balances["invoice-D-3"]["balance_cents"] == 2000  # Partial collection.


def test_late_change_restates_only_visible_cutoff(source):
    original, late, corrected = [report(source, cutoff) for cutoff in DEFAULT_CUTOFFS]
    assert original["monthly_mrr_bridge"][1]["closing_mrr_cents"] == 24000
    assert late["monthly_mrr_bridge"][1]["closing_mrr_cents"] == 25000
    assert late["monthly_mrr_bridge"] == corrected["monthly_mrr_bridge"]
    assert original["monthly_cash_bridge"] == late["monthly_cash_bridge"]
    assert corrected["monthly_cash_bridge"][1]["invoiced_cents"] == 25000
    assert corrected["monthly_cash_bridge"][-1]["closing_balance_cents"] == 5000
    assert sum(row["net_cash_cents"] for row in corrected["monthly_cash_bridge"]) == 71000


def test_earlier_report_is_unchanged_after_later_versions_arrive(source):
    prefix = copy.deepcopy(source)
    for feed in FEEDS:
        prefix[feed] = [row for row in prefix[feed] if row["revision"] == 1]
    assert report(prefix) == report(source)


def test_effective_date_correction_moves_only_later_report(source):
    # After close, a March cancellation is corrected to April 1.
    cancellation = next(row for row in source["subscription_changes"] if row["record_id"] == "a-end")
    before = report(source)
    source["subscription_changes"].append({**cancellation, "revision": 2,
                                           "recorded_at": "2026-04-05T00:00:00Z", "effective_date": "2026-04-01"})
    assert report(source) == before
    later = report(source, "2026-04-06T00:00:00Z")
    assert later["monthly_mrr_bridge"][-1]["closing_mrr_cents"] == 34000
    assert later["monthly_mrr_bridge"][-1]["churn_mrr_cents"] == 0


def test_cash_correction_has_separate_knowledge_and_business_dates(source):
    old = source["payments"][0]
    source["payments"].append({**old, "revision": 2, "recorded_at": "2026-04-05T00:00:00Z", "paid_date": "2026-02-02"})
    assert report(source)["monthly_cash_bridge"][0]["paid_cents"] == 18000
    later = report(source, "2026-04-06T00:00:00Z")
    assert later["monthly_cash_bridge"][0]["paid_cents"] == 8000
    assert later["monthly_cash_bridge"][1]["paid_cents"] == 34000
    assert later["monthly_cash_bridge"][-1]["closing_balance_cents"] == 5000


def test_exact_knowledge_boundary_includes_revision(source):
    just_before = report(source, "2026-04-02T11:59:59Z")
    at_boundary = report(source, "2026-04-02T12:00:00Z")
    assert just_before["monthly_mrr_bridge"][-1]["closing_mrr_cents"] == 18000
    assert at_boundary["monthly_mrr_bridge"][-1]["closing_mrr_cents"] == 19000


def test_permutation_and_identical_redelivery_are_invariant(source):
    expected = report(source)
    rng = random.Random(17)
    for feed in FEEDS:
        source[feed] += copy.deepcopy(source[feed])
        rng.shuffle(source[feed])
    assert report(source) == expected


def test_prior_history_drives_opening_balance_and_reactivation(source):
    result = build_report(source, DEFAULT_CUTOFFS[0], "2026-03-01", "2026-04-01")
    assert result["monthly_mrr_bridge"][0]["opening_mrr_cents"] == 24000
    assert result["monthly_mrr_bridge"][0]["reactivation_mrr_cents"] == 9000
    assert result["monthly_cash_bridge"][0]["closing_balance_cents"] == 4000


def test_empty_history_emits_calendar_months(source):
    for feed in FEEDS:
        source[feed] = []
    result = report(source)
    assert len(result["monthly_mrr_bridge"]) == 3
    assert all(row["closing_mrr_cents"] == 0 for row in result["monthly_mrr_bridge"])
    assert len(result["monthly_cash_bridge"]) == 3
    assert all(row["closing_balance_cents"] == 0 for row in result["monthly_cash_bridge"])


def test_overpayment_remains_visible_as_credit_balance(source):
    source["payments"][0]["amount_cents"] += 50000
    result = report(source)
    assert result["monthly_cash_bridge"][-1]["closing_balance_cents"] == -46000
    assert next(row for row in result["invoice_balances"] if row["invoice_id"] == "invoice-A-1")["balance_cents"] == -50000


@pytest.mark.parametrize("mutation, match", [
    (lambda s: s["payments"][0].update(invoice_id="missing"), "visible invoice"),
    (lambda s: s["refunds"][0].update(payment_id="missing"), "visible payment"),
    (lambda s: s["refunds"][0].update(amount_cents=999999), "Refunds exceed"),
    (lambda s: s["payments"][0].update(paid_date="2025-12-01"), "precede"),
    (lambda s: s["refunds"][0].update(refunded_date="2026-01-01"), "precede"),
    (lambda s: s["invoices"][0].update(amount_cents=True), "integer"),
    (lambda s: s["invoices"][0].update(amount_cents=10.01), "integer"),
    (lambda s: s["invoices"][0].update(extra="unannounced field"), "unexpected"),
    (lambda s: s["invoices"][0].update(recorded_at="2026-01-01T00:00:00"), "UTC"),
    (lambda s: s["subscription_changes"][0].update(status="cancelled"), "zero MRR"),
    (lambda s: s.update(complete_subscription_history=False), "Full subscription history"),
    (lambda s: s.update(scope="production"), "synthetic GBP"),
    (lambda s: s["invoices"][-1].update(revision=3), "contiguous"),
    (lambda s: s["invoices"][-1].update(customer_id="other"), "between entities"),
    (lambda s: s["invoices"][-1].update(recorded_at="2026-01-01T00:00:00Z"), "strictly increasing"),
])
def test_bad_contracts_fail_closed(source, mutation, match):
    mutation(source)
    with pytest.raises(ValueError, match=match):
        report(source, DEFAULT_CUTOFFS[-1])


def test_conflicting_duplicate_rejected(source):
    source["invoices"].append({**source["invoices"][0], "amount_cents": 123})
    with pytest.raises(ValueError, match="conflicting duplicate"):
        report(source)


def test_simultaneous_logical_events_rejected(source):
    source["subscription_changes"].append({**source["subscription_changes"][0], "record_id": "different-event"})
    with pytest.raises(ValueError, match="Ambiguous simultaneous"):
        report(source)


@pytest.mark.parametrize("start,end,cutoff", [
    ("2026-01-02", "2026-04-01", DEFAULT_CUTOFFS[0]),
    ("2026-01-01", "2026-04-02", DEFAULT_CUTOFFS[-1]),
    ("2026-04-01", "2026-01-01", DEFAULT_CUTOFFS[0]),
    ("2026-01-01", "2026-04-01", "2026-03-31T00:00:00Z"),
])
def test_unclosed_or_misaligned_period_rejected(source, start, end, cutoff):
    with pytest.raises(ValueError, match="complete months"):
        build_report(source, cutoff, start, end)


def test_release_matches_downloads_and_hashes(tmp_path):
    output = tmp_path / "release-1"
    manifest = publish(ROOT / "subscriptions/demo.json", output)
    assert manifest["checks_passed"] == 33
    for entry in manifest["artifacts"]:
        assert digest(output / entry["path"]) == entry["sha256"]
    bundle = json.loads((output / "report.json").read_text(encoding="utf-8"))
    with (output / "monthly_mrr_bridge.csv").open(newline="", encoding="utf-8") as stream:
        exported = list(csv.DictReader(stream))
    expected = [row for result in bundle["reports"] for row in result["monthly_mrr_bridge"]]
    assert [int(row["closing_mrr_cents"]) for row in exported] == [row["closing_mrr_cents"] for row in expected]
    assert (output / "index.html").read_text(encoding="utf-8").count('id="report-data"') == 1
    assert bundle["source_sha256"] == digest(ROOT / "subscriptions/demo.json")


def test_failed_release_preserves_previous_complete_bundle(tmp_path, monkeypatch):
    prior = tmp_path / "release-1"
    publish(ROOT / "subscriptions/demo.json", prior)
    original = {path.name: digest(path) for path in prior.iterdir()}

    def fail_after_exports(_):
        raise RuntimeError("Controlled interruption after report/CSV writes, before publication")

    monkeypatch.setattr(subscription_report, "render", fail_after_exports)
    with pytest.raises(RuntimeError, match="Controlled interruption"):
        publish(ROOT / "subscriptions/demo.json", tmp_path / "release-2")
    assert not (tmp_path / "release-2").exists()
    assert not list(tmp_path.glob(".subscriptions-*"))
    assert {path.name: digest(path) for path in prior.iterdir()} == original


def test_release_refuses_to_overwrite_existing_output(tmp_path):
    output = tmp_path / "release"
    publish(ROOT / "subscriptions/demo.json", output)
    before = digest(output / "manifest.json")
    with pytest.raises(FileExistsError, match="Immutable release"):
        publish(ROOT / "subscriptions/demo.json", output)
    assert digest(output / "manifest.json") == before


def test_html_payload_cannot_close_script_tag():
    html = subscription_report.render({"untrusted": "</script><script>alert(1)</script>"})
    assert "<script>alert(1)" not in html
    assert r"\u003c/script\u003e" in html


def test_unsafe_csv_identifier_rejected(source):
    source["invoices"][0]["record_id"] = "=1+1"
    with pytest.raises(ValueError, match="identifiers require"):
        report(source)


def test_derived_money_cannot_silently_lose_browser_precision(source):
    for row in source["subscription_changes"]:
        if row["monthly_amount_cents"]:
            row["monthly_amount_cents"] = 2**53 - 1
    with pytest.raises(ValueError, match="exact browser integer range"):
        report(source)


def test_empty_release_still_has_every_csv_download(source, tmp_path):
    for feed in FEEDS:
        source[feed] = []
    raw = tmp_path / "empty.json"
    raw.write_text(json.dumps(source), encoding="utf-8")
    output = tmp_path / "empty-release"
    publish(raw, output)
    for name in ("monthly_mrr_bridge", "monthly_cash_bridge", "customer_mrr_bridge", "invoice_balances"):
        assert (output / f"{name}.csv").read_text(encoding="utf-8").startswith("knowledge_cutoff,")
