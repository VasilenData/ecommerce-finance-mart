"""Small independent arithmetic and journal failure cases; dbt is exercised in its CI job."""

import copy
import json

import duckdb
import pytest

from mart.dbt_warehouse import Warehouse, python_oracle
from mart.subscriptions import FEEDS, ROOT, build_report


def empty_source():
    return {
        "schema_version": 1,
        "currency": "GBP",
        "scope": "synthetic",
        "complete_subscription_history": True,
        **{name: [] for name in FEEDS},
    }


def test_empty_sources_keep_new_month_and_zero_balances():
    result = python_oracle(empty_source(), "2026-05-01T00:00:00Z", end="2026-05-01")
    assert len(result["monthly_cash_bridge"]) == 4
    assert result["monthly_cash_bridge"][-1] == {
        "month": "2026-04-01",
        "opening_balance_cents": 0,
        "invoiced_cents": 0,
        "paid_cents": 0,
        "refunded_cents": 0,
        "net_cash_cents": 0,
        "closing_balance_cents": 0,
    }
    assert result["customer_mrr_bridge"] == []
    assert result["invoice_balances"] == []


def test_oracle_hand_calculated_refund_and_credit_without_join_multiplication():
    source = empty_source()
    source["invoices"] = [
        {
            "record_id": "i",
            "revision": 1,
            "recorded_at": "2026-01-01T00:00:00Z",
            "issued_date": "2026-01-01",
            "customer_id": "A",
            "amount_cents": 10000,
        }
    ]
    source["payments"] = [
        {
            "record_id": name,
            "revision": 1,
            "recorded_at": "2026-01-02T00:00:00Z",
            "paid_date": "2026-01-02",
            "invoice_id": "i",
            "amount_cents": amount,
        }
        for name, amount in (("p1", 7000), ("p2", 6000))
    ]
    source["refunds"] = [
        {
            "record_id": name,
            "revision": 1,
            "recorded_at": "2026-01-03T00:00:00Z",
            "refunded_date": "2026-01-03",
            "payment_id": "p1",
            "amount_cents": amount,
        }
        for name, amount in (("r1", 1000), ("r2", 500))
    ]
    actual = python_oracle(source, "2026-04-01T00:00:00Z")
    assert actual["invoice_balances"] == [
        {
            "invoice_id": "i",
            "customer_id": "A",
            "invoiced_cents": 10000,
            "paid_cents": 13000,
            "refunded_cents": 1500,
            "balance_cents": -1500,
        }
    ]
    assert actual["monthly_cash_bridge"][0]["net_cash_cents"] == 11500


def test_oracle_preserves_all_published_cutoffs():
    source = json.loads((ROOT / "subscriptions/demo.json").read_text(encoding="utf-8"))
    for cutoff in ("2026-04-01T00:00:00Z", "2026-04-03T00:00:00Z", "2026-04-04T00:00:00Z"):
        oracle = python_oracle(source, cutoff)
        report = build_report(source, cutoff)
        assert oracle == {name: report[name] for name in oracle}


def test_journal_replay_conflict_and_sequence_enforcement(tmp_path):
    source = json.loads((ROOT / "subscriptions/demo.json").read_text(encoding="utf-8"))
    store = Warehouse("duckdb", tmp_path)
    assert store.append(source) > 0
    before = store.counts()
    assert store.append(source) == 0
    broken = copy.deepcopy(source)
    broken["invoices"][0]["amount_cents"] += 1
    # Put a valid new row in an earlier feed: conflict must prevent partial append.
    fresh = {**broken["subscription_changes"][0], "record_id": "extra", "subscription_id": "extra"}
    broken["subscription_changes"].append(fresh)
    with pytest.raises(ValueError, match="Immutable journal identity"):
        store.append(broken)
    assert store.counts() == before
    with store.connection() as con:
        with pytest.raises(duckdb.ConstraintException):
            con.execute(f"INSERT INTO {store.raw_schema}.invoices SELECT * FROM {store.raw_schema}.invoices LIMIT 1")
    assert store.counts() == before
