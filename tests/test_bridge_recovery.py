"""Persisted report corruption and abrupt termination must not silently change a close."""

import json

import duckdb
import pytest

from mart.bridge_recovery import exercise
from mart.meterwork_bridge import Bridge, StoredReleaseError, canonical, cash_fixture, sha
from mart.meterwork_bridge_exercise import FIXTURES, HAND, manifest_hash


@pytest.fixture
def receiver(tmp_path):
    baseline, corrected = FIXTURES / "baseline", FIXTURES / "corrected"
    cash = cash_fixture(json.loads((baseline / "accounts.json").read_bytes()))
    receiver = Bridge(tmp_path / "receiver.duckdb")
    for source in (baseline, corrected):
        assert receiver.receive(source, cash, manifest_hash(source))["status"] == "accepted"
    return receiver, cash


def test_current_historical_and_audit_reads_refuse_changed_report(receiver):
    bridge, _ = receiver
    current = bridge.current()
    altered = json.loads(canonical(current))
    altered["totals"]["invoice_cents"] += 100000
    with duckdb.connect(bridge.path) as con:
        con.execute("UPDATE meterwork_usd.deliveries SET report_json=? WHERE bundle_sha=?", [canonical(altered), current["bundle_sha256"]])
    for call in (bridge.current, lambda: bridge.release(current["bundle_sha256"]), bridge.audit):
        with pytest.raises(StoredReleaseError, match="fingerprint"):
            call()
    with duckdb.connect(bridge.path) as con:
        con.execute("UPDATE meterwork_usd.deliveries SET report_json=? WHERE bundle_sha=?", [canonical(current), current["bundle_sha256"]])
    assert bridge.current()["totals"] == HAND["corrected"]
    assert bridge.audit()["verified_releases"] == 2


@pytest.mark.parametrize("field,value", [("bundle_sha256", "0" * 64), ("sequence", 99), ("cash_sha256", "0" * 64)])
def test_recomputed_report_hash_cannot_change_persisted_identity(receiver, field, value):
    bridge, _ = receiver
    original = bridge.current()
    altered = {**original, field: value}
    with duckdb.connect(bridge.path) as con:
        con.execute(
            "UPDATE meterwork_usd.deliveries SET report_json=?, report_sha=? WHERE bundle_sha=?",
            [canonical(altered), sha(altered), original["bundle_sha256"]],
        )
    with pytest.raises(StoredReleaseError, match="identity"):
        bridge.current()


def test_corrupt_old_release_cannot_be_reported_as_successful_replay(receiver):
    bridge, cash = receiver
    current = bridge.current()
    old_hash = manifest_hash(FIXTURES / "baseline")
    with duckdb.connect(bridge.path) as con:
        con.execute("UPDATE meterwork_usd.deliveries SET report_json='invalid json' WHERE bundle_sha=?", [old_hash])
    assert bridge.current() == current
    with pytest.raises(StoredReleaseError):
        bridge.audit()
    with pytest.raises(StoredReleaseError):
        bridge.receive(FIXTURES / "baseline", cash, old_hash)
    assert bridge.current() == current


def test_orphan_pointer_is_corruption_not_empty_history(receiver):
    bridge, _ = receiver
    with duckdb.connect(bridge.path) as con:
        con.execute("UPDATE meterwork_usd.active SET bundle_sha=?", ["0" * 64])
    with pytest.raises(StoredReleaseError):
        bridge.current()


def test_missing_pointer_cannot_silently_erase_retained_history(receiver):
    bridge, _ = receiver
    with duckdb.connect(bridge.path) as con:
        con.execute("DELETE FROM meterwork_usd.active")
    with pytest.raises(StoredReleaseError, match="without an active pointer"):
        bridge.current()


def test_observed_process_death_reopens_previous_close_and_recovers(tmp_path):
    evidence = exercise(tmp_path / "evidence")
    assert evidence["check_count"] == 32
    assert all(row["passed"] for row in evidence["checks"])
    assert {row["boundary"] for row in evidence["interruptions"]} == {"insert", "promote"}
    assert all(row["exit_code"] != 0 and row["uncommitted_delivery_count"] == 2 for row in evidence["interruptions"])
    with pytest.raises(FileExistsError):
        exercise(tmp_path / "evidence")
