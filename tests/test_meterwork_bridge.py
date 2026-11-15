"""Independent hand totals and real producer fixture mutations exercise the receiver boundary."""

import json
import shutil
from copy import deepcopy

import duckdb
import pytest

from mart.meterwork_bridge import Bridge, Rejected, cash_fixture, decode_bundle, invoice_rounding, rerate, sha, validated_cash
from mart.meterwork_bridge_exercise import FIXTURES, HAND, exercise, manifest_hash, resigned_copy


@pytest.fixture
def receiver(tmp_path):
    base = FIXTURES / "baseline"
    cash = cash_fixture(json.loads((base / "accounts.json").read_bytes()))
    bridge = Bridge(tmp_path / "finance.duckdb")
    assert bridge.receive(base, cash, manifest_hash(base))["status"] == "accepted"
    return bridge, cash


def test_real_api_producer_fixture_and_hand_arithmetic(receiver):
    bridge, cash = receiver
    first = bridge.current()
    assert first["totals"] == HAND["baseline"]
    corrected = FIXTURES / "corrected"
    assert bridge.receive(corrected, cash, manifest_hash(corrected))["status"] == "accepted"
    last = bridge.current()
    assert last["totals"] == HAND["corrected"]
    assert last["change_from_previous"]["invoice_cents"] == -150
    assert last["change_from_previous"]["amount_microusd"] == -1498500
    assert last["change_from_previous"]["residual_microusd"] == 1500
    changes = last["revision_changes"]
    assert len(changes) == 1
    assert {row["reason"]: row["delta_microusd"] for row in changes[0]["lines"]} == {"usage_correction": -2500250, "late_usage": 1001750}
    actual = {
        (row["customer_id"], row["revision"]): (row["amount_microusd"], row["invoice_cents"], row["rounding_residual_microusd"])
        for row in last["invoice_history"]
    }
    assert actual == {
        ("customer_a", 1): (34501750, 3450, 1750),
        ("customer_a", 2): (33003250, 3300, 3250),
        ("customer_b", 1): (19999800, 2000, -200),
    }
    assert bridge.release(first["bundle_sha256"]) == first
    assert bridge.receive(FIXTURES / "baseline", cash, first["bundle_sha256"])["status"] == "replayed"
    assert bridge.current() == last


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda m, p: p["statements.json"].append(deepcopy(p["statements.json"][0])), "duplicate_identity"),
        (lambda m, p: p.update({"statements.json": [r for r in p["statements.json"] if r["revision"] != 2]}), "revision_gap"),
        (lambda m, p: m.update(export_sequence=3), "delivery_gap"),
        (lambda m, p: m.update(previous_bundle_sha256="0" * 64), "delivery_gap"),
        (lambda m, p: m.update(currency="GBP"), "units"),
        (lambda m, p: p["statements.json"][0].update(amount_microusd=1), "rating_mismatch"),
        (lambda m, p: p["statements.json"][0].update(total_units=True), "integer_money"),
        (
            lambda m, p: p["source_snapshots.json"][0]["events"].append(deepcopy(p["source_snapshots.json"][0]["events"][0])),
            "duplicate_identity",
        ),
        (lambda m, p: p["source_snapshots.json"][0]["events"][0].update(tenant_id="foreign-account"), "account_isolation"),
        (lambda m, p: p["source_snapshots.json"][0].update(source_cutoff_utc="2026-10-01T00:00:00Z"), "source_cutoff"),
        (lambda m, p: m.update(source_cutoff_utc="2026-12-01T00:00:00Z"), "source_cutoff"),
        (lambda m, p: p["accounts.json"][0].update(customer_id="renamed-customer"), "account_scope"),
    ],
)
def test_bad_logical_delivery_preserves_last_good(receiver, tmp_path, mutate, code):
    bridge, cash = receiver
    before = bridge.current()
    variant = tmp_path / "variant"
    expected = resigned_copy(FIXTURES / "corrected", variant, mutate)
    outcome = bridge.receive(variant, cash, expected)
    assert outcome["status"] == "rejected", outcome
    assert outcome["code"] == code, outcome
    assert bridge.current() == before
    with duckdb.connect(bridge.path, read_only=True) as con:
        assert con.execute("SELECT count(*) FROM meterwork_usd.deliveries").fetchone() == (1,)
        assert con.execute("SELECT preserved_report_sha FROM meterwork_usd.exceptions").fetchone() == (sha(before),)


def test_missing_corrupt_and_duplicate_json_key_are_visible(receiver, tmp_path):
    bridge, cash = receiver
    current = bridge.current()
    source = FIXTURES / "corrected"
    expected = manifest_hash(source)
    assert bridge.receive(tmp_path / "missing", cash, expected)["code"] == "missing_delivery"
    target = tmp_path / "corrupt"
    shutil.copytree(source, target)
    (target / "statements.json").write_bytes(b"[]\n")
    assert bridge.receive(target, cash, expected)["code"] == "file_fingerprint"
    (target / "manifest.json").write_bytes(b'{"contract_version":1,"contract_version":2}')
    assert bridge.receive(target, cash, expected)["code"] == "duplicate_json_key"
    assert bridge.current() == current


def test_first_delivery_cannot_skip_predecessor(tmp_path):
    source = FIXTURES / "corrected"
    cash = cash_fixture(json.loads((source / "accounts.json").read_bytes()))
    bridge = Bridge(tmp_path / "new.duckdb")
    assert bridge.receive(source, cash, manifest_hash(source))["code"] == "delivery_gap"
    assert bridge.current() is None


def test_transaction_fault_rolls_back_insert_and_active_pointer(receiver):
    bridge, cash = receiver
    before = bridge.current()
    source = FIXTURES / "corrected"
    expected = manifest_hash(source)
    assert bridge.receive(source, cash, expected, fail_after_insert=True)["code"] == "injected_failure"
    assert bridge.current() == before
    assert bridge.release(expected) is None
    assert bridge.receive(source, cash, expected)["status"] == "accepted"


def test_fixed_cash_snapshot_cannot_silently_change_on_replay_or_next_delivery(receiver):
    bridge, cash = receiver
    before = bridge.current()
    altered = deepcopy(cash)
    altered["payments"][0]["amount_cents"] += 1
    for name in ("baseline", "corrected"):
        source = FIXTURES / name
        assert bridge.receive(source, altered, manifest_hash(source))["code"] == "immutable_cash"
        assert bridge.current() == before


@pytest.mark.parametrize(
    "mutation",
    [
        lambda cash: cash["payments"].append(deepcopy(cash["payments"][0])),
        lambda cash: cash["payments"][0].update(amount_cents=True),
        lambda cash: cash["payments"][0].update(amount_cents=1.5),
        lambda cash: cash["refunds"][0].update(amount_cents=2501),
        lambda cash: cash["refunds"][0].update(recorded_at="2026-10-01T00:00:00Z"),
        lambda cash: cash["refunds"][0].update(payment_id="unknown"),
        lambda cash: cash.update(currency="GBP"),
    ],
)
def test_cash_rejects_duplicate_logical_records_and_invalid_references(receiver, mutation):
    bridge, cash = receiver
    mutation(cash)
    with pytest.raises(Rejected):
        validated_cash(cash, {row["invoice_id"] for row in bridge.current()["invoice_history"]})


def test_cash_cutoff_is_applied_before_aggregation(receiver):
    bridge, cash = receiver
    cash["knowledge_cutoff_utc"] = "2026-11-01T06:00:00Z"
    amounts = validated_cash(cash, {row["invoice_id"] for row in bridge.current()["invoice_history"]})
    assert sum(row["paid_cents"] for row in amounts.values()) == 4500
    assert sum(row["refunded_cents"] for row in amounts.values()) == 0


def test_revision_uses_its_own_cutoff_even_in_later_full_snapshot():
    source = FIXTURES / "corrected"
    _, payload, history = decode_bundle(source, manifest_hash(source))
    snapshots = {row["account_id"]: row for row in payload["source_snapshots.json"]}
    account = next(row["account_id"] for row in payload["accounts.json"] if row["customer_id"] == "customer_a")
    statements = sorted([row for row in payload["statements.json"] if row["tenant_id"] == account], key=lambda row: row["revision"])
    assert len(snapshots[account]["events"]) == 4
    assert rerate(statements[0], snapshots[account])["amount_microusd"] == 34501750
    assert rerate(statements[1], snapshots[account])["amount_microusd"] == 33003250
    assert len(history) == 3


def test_old_statement_cannot_be_reidentified_even_with_valid_hashes(receiver, tmp_path):
    bridge, cash = receiver
    before = bridge.current()
    target = tmp_path / "rewritten"
    # Publication UUID is deliberately not part of the rating content hash.
    expected = resigned_copy(FIXTURES / "corrected", target, lambda m, p: p["statements.json"][0].update(id="replacement-uuid"))
    assert bridge.receive(target, cash, expected)["code"] == "historical_rewrite"
    assert bridge.current() == before


def test_complete_offline_reproduction_from_actual_producer_fixtures(tmp_path):
    evidence = exercise(FIXTURES, tmp_path / "release")
    assert evidence["check_count"] >= 25
    assert all(row["passed"] for row in evidence["checks"])
    assert len(evidence["exception_ledger"]) == 8
    assert (tmp_path / "release/index.html").is_file()
    with pytest.raises(FileExistsError):
        exercise(FIXTURES, tmp_path / "release")


@pytest.mark.parametrize(
    ("amount", "cents", "residual"),
    [
        (0, 0, 0),
        (4999, 0, 4999),
        (5000, 1, -5000),
        (5001, 1, -4999),
        (9999, 1, -1),
        (10000, 1, 0),
        (14999, 1, 4999),
        (15000, 2, -5000),
    ],
)
def test_half_cent_boundaries_round_once_and_retain_signed_residual(amount, cents, residual):
    assert invoice_rounding(amount) == (cents, residual)
    assert amount == cents * 10000 + residual


@pytest.mark.parametrize("amount", [True, -1, 1.5, "5000", 10**20])
def test_rounding_rejects_non_integer_or_out_of_contract_amount(amount):
    with pytest.raises(Rejected):
        invoice_rounding(amount)
