"""Independent hand-calculated oracles and deterministic transaction failures."""

import copy
import json
import subprocess
import sys

import duckdb
import pytest

from mart.cdc import FIELDS as SOURCE_FIELDS
from mart.cdc import TOPIC, apply_batch, initialize, state
from mart.cdc_impact import CONTRACT, impact


def row(order_id=1, **changes):
    return {"order_id": order_id, "customer_id": 100 + order_id, "order_day": "2026-11-01",
            "gross_cents": 10000, "refund_cents": 0, "status": "paid", **changes}


def event(offset, operation="c", record=None):
    record = record or row()
    value = {"source": {"table": "orders"}, "op": operation,
             "before": record if operation == "d" else None,
             "after": None if operation == "d" else record}
    return {"topic": TOPIC, "partition": 0, "offset": offset, "key": {"order_id": record["order_id"]}, "value": value}


@pytest.fixture
def sink():
    with duckdb.connect() as con:
        initialize(con)
        yield con


def test_snapshot_correction_refund_cancellation_delete_and_tombstone(sink):
    apply_batch(sink, [event(0, "r"), event(1, "r", row(2, gross_cents=5000))])
    assert state(sink)["daily"] == [["2026-11-01", 2, 15000, 0, 15000]]
    apply_batch(sink, [event(2, "u", row(gross_cents=12000, refund_cents=2000)),
                       event(3, "u", row(2, gross_cents=5000, status="cancelled")),
                       event(4, "c", row(3, gross_cents=8000, order_day="2026-11-02"))])
    assert state(sink)["daily"] == [["2026-11-01", 1, 12000, 2000, 10000], ["2026-11-02", 1, 8000, 0, 8000]]
    tombstone = {**event(6, record=row(3)), "value": None}
    apply_batch(sink, [event(5, "d", row(3, gross_cents=8000, order_day="2026-11-02")), tombstone])
    assert state(sink)["daily"] == [["2026-11-01", 1, 12000, 2000, 10000]]
    assert len(state(sink)["orders"]) == 2


def test_replay_older_offsets_does_not_resurrect_deleted_orders(sink):
    history = [event(0), event(1, "d")]
    apply_batch(sink, history)
    expected = state(sink)
    result = apply_batch(sink, history)
    assert result == {"applied": 0, "duplicates": 2, "last_offset": 1}
    assert state(sink) == expected


def test_connector_redelivery_at_new_offset_is_idempotent_current_state(sink):
    apply_batch(sink, [event(0), event(1, "u", row(refund_cents=3000))])
    before = state(sink)
    apply_batch(sink, [event(2, "u", row(refund_cents=3000))])
    assert state(sink)["daily"] == before["daily"]
    assert state(sink)["orders"] == before["orders"]
    assert state(sink)["events"] == 3  # retain the separate delivery in the audit


def test_offset_identity_conflict_fails_without_changes(sink):
    apply_batch(sink, [event(0)])
    before = state(sink)
    with pytest.raises(ValueError, match="different contents"):
        apply_batch(sink, [event(0, record=row(gross_cents=9000))])
    assert state(sink) == before


@pytest.mark.parametrize("bad", [event(2), {**event(0), "partition": 1}, {**event(0), "topic": "another.orders"}])
def test_missing_history_or_wrong_stream_is_not_silently_skipped(sink, bad):
    with pytest.raises(ValueError):
        apply_batch(sink, [bad])
    assert state(sink) == {"orders": [], "daily": [], "last_offset": -1, "events": 0}


@pytest.mark.parametrize("change", [
    {"refund_cents": 10001}, {"gross_cents": 12.5}, {"order_day": "2026-02-30"},
    {"status": "unknown"}, {"future_column": 1}, {"customer_id": True},
])
def test_contract_failure_rolls_back_whole_batch_including_offset(sink, change):
    apply_batch(sink, [event(0)])
    before = state(sink)
    with pytest.raises((ValueError, TypeError)):
        apply_batch(sink, [event(1, "u", row(gross_cents=11000)), event(2, "c", row(2, **change))])
    assert state(sink) == before


def test_key_mismatch_and_truncate_are_rejected(sink):
    wrong = copy.deepcopy(event(0))
    wrong["key"]["order_id"] = 2
    for bad in [wrong, event(0, "t")]:
        with pytest.raises(ValueError):
            apply_batch(sink, [bad])
    assert state(sink)["events"] == 0


def test_exception_before_commit_keeps_last_release_and_can_retry(sink):
    apply_batch(sink, [event(0)])
    before = state(sink)

    def fail():
        raise RuntimeError("publication interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        apply_batch(sink, [event(1, "u", row(refund_cents=2000))], before_commit=fail)
    assert state(sink) == before
    apply_batch(sink, [event(1, "u", row(refund_cents=2000))])
    assert state(sink)["daily"] == [["2026-11-01", 1, 10000, 2000, 8000]]


def test_process_death_before_commit_preserves_db_and_durable_offset(tmp_path):
    db = tmp_path / "sink.duckdb"
    with duckdb.connect(str(db)) as con:
        initialize(con)
        apply_batch(con, [event(0)])
        before = state(con)
    candidate = tmp_path / "event.json"
    candidate.write_text(json.dumps(event(1, "u", row(refund_cents=2000))))
    code = """import json, os, sys, duckdb
from mart.cdc import apply_batch
con = duckdb.connect(sys.argv[1])
record = json.loads(open(sys.argv[2]).read())
apply_batch(con, [record], before_commit=lambda: os._exit(86))
"""
    result = subprocess.run([sys.executable, "-c", code, str(db), str(candidate)], timeout=30, capture_output=True)
    assert result.returncode == 86, result.stderr
    with duckdb.connect(str(db)) as con:
        assert state(con) == before
        apply_batch(con, [json.loads(candidate.read_text())])
        assert state(con)["daily"] == [["2026-11-01", 1, 10000, 2000, 8000]]


def test_independent_rebuild_from_entire_history_matches_published_state(sink):
    history = [event(0), event(1, "c", row(2, gross_cents=5000)), event(2, "d"),
               event(3, "u", row(2, gross_cents=5000, refund_cents=4999))]
    for item in history:
        apply_batch(sink, [item])
    assert state(sink)["daily"] == [["2026-11-01", 1, 5000, 4999, 1]]
    with duckdb.connect() as rebuilt:
        initialize(rebuilt)
        apply_batch(rebuilt, history)
        assert state(rebuilt) == state(sink)


def test_documented_source_contract_and_refund_impact_remain_consistent():
    contract = json.loads(CONTRACT.read_text())
    assert set(contract["fields"]) == SOURCE_FIELDS
    result = impact("orders.refund_cents")
    assert result["affected"] == ["evidence.json", "operational_daily.net_cents", "operational_daily.refund_cents"]
    assert result["historical_uci_outputs_affected"] is False
    with pytest.raises(ValueError, match="declared"):
        impact("orders.unknown")
