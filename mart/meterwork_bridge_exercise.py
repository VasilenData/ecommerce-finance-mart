"""Reconcile actual versioned Meterwork exports with a fixed hand-arithmetic oracle."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .meterwork_bridge import Bridge, canonical, cash_fixture, render_report, sha

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures/meterwork-bridge/2026-11-15"
# Literal, independently hand-computed amounts. Do not derive this oracle from the receiver.
HAND = {
    "baseline": {
        "invoice_cents": 5450,
        "paid_cents": 4500,
        "refunded_cents": 200,
        "gross_unpaid_cents": 1450,
        "credit_cents": 300,
        "net_balance_cents": 1150,
        "amount_microusd": 54501550,
        "residual_microusd": 1550,
    },
    "corrected": {
        "invoice_cents": 5300,
        "paid_cents": 4500,
        "refunded_cents": 200,
        "gross_unpaid_cents": 1300,
        "credit_cents": 300,
        "net_balance_cents": 1000,
        "amount_microusd": 53003050,
        "residual_microusd": 3050,
    },
}


def write_json(path, value):
    path.write_bytes((canonical(value) + "\n").encode())


def manifest_hash(directory):
    return json.loads((directory / "manifest.json").read_bytes())["bundle_sha256"]


def resigned_copy(source, target, mutate):
    """Deliberately malformed contract fixture, with valid outer hashes for deep checks."""
    shutil.copytree(source, target)
    manifest = json.loads((target / "manifest.json").read_bytes())
    payload = {name: json.loads((target / name).read_bytes()) for name in manifest["files"]}
    mutate(manifest, payload)
    for name, rows in payload.items():
        write_json(target / name, rows)
        manifest["files"][name] = {"sha256": sha((target / name).read_bytes()), "rows": len(rows)}
    manifest["bundle_sha256"] = sha({key: value for key, value in manifest.items() if key != "bundle_sha256"})
    write_json(target / "manifest.json", manifest)
    return manifest["bundle_sha256"]


def exercise(fixtures, output):
    fixtures, output = Path(fixtures), Path(output)
    if output.exists():
        raise FileExistsError("Use a new output directory; dated evidence is immutable")
    baseline, corrected = fixtures / "baseline", fixtures / "corrected"
    first_hash, next_hash = manifest_hash(baseline), manifest_hash(corrected)
    cash = cash_fixture(json.loads((baseline / "accounts.json").read_bytes()))
    checks, attempts = [], []

    def check(name, condition, detail=None):
        if not condition:
            raise AssertionError(f"{name}: {detail}")
        checks.append({"name": name, "passed": True, "detail": detail})

    with tempfile.TemporaryDirectory(prefix="finance-meterwork-") as temporary:
        temp = Path(temporary)
        bridge = Bridge(temp / "bridge.duckdb")
        # Sentinels are real unrelated tables: neither GBP output nor either invoice API is replaced.
        with duckdb.connect(bridge.path) as con:
            con.execute("CREATE TABLE gbp_historical_sentinel(amount_cents BIGINT, currency VARCHAR)")
            con.execute("INSERT INTO gbp_historical_sentinel VALUES (12345, 'GBP')")
            con.execute("CREATE TABLE invoice_contract_v1_sentinel(balance_cents BIGINT)")
            con.execute("INSERT INTO invoice_contract_v1_sentinel VALUES (-100)")
            con.execute(
                "CREATE TABLE invoice_contract_v2_sentinel(net_balance_cents BIGINT, gross_unpaid_cents BIGINT, credit_cents BIGINT)"
            )
            con.execute("INSERT INTO invoice_contract_v2_sentinel VALUES (-100,0,100)")
        result = bridge.receive(baseline, cash, first_hash)
        check("actual_producer_baseline_accepted", result["status"] == "accepted", result)
        first_report = bridge.current()
        check("baseline_independent_hand_totals", first_report["totals"] == HAND["baseline"], first_report["totals"])
        result = bridge.receive(baseline, cash, first_hash)
        check("identical_delivery_replay_does_not_double_count", result["status"] == "replayed" and bridge.current() == first_report)

        def rejected(name, directory, expected, code=None, **kwargs):
            before = bridge.current()
            outcome = bridge.receive(directory, cash, expected, **kwargs)
            attempts.append({"name": name, "expected_bundle_sha256": expected, "result": outcome})
            check(name, outcome["status"] == "rejected" and (code is None or outcome["code"] == code), outcome)
            check(name + "_preserves_last_good", bridge.current() == before)

        rejected("missing_delivery", temp / "absent", next_hash, "missing_delivery")
        corrupt = temp / "corrupt"
        shutil.copytree(corrected, corrupt)
        (corrupt / "statements.json").write_bytes((corrupt / "statements.json").read_bytes() + b" ")
        rejected("corrupt_transport_bytes", corrupt, next_hash, "file_fingerprint")
        variants = [
            (
                "duplicate_logical_statement",
                lambda m, p: p["statements.json"].append(deepcopy(p["statements.json"][0])),
                "duplicate_identity",
            ),
            (
                "missing_statement_revision",
                lambda m, p: p.update({"statements.json": [r for r in p["statements.json"] if r["revision"] != 2]}),
                "revision_gap",
            ),
            ("out_of_order_delivery", lambda m, p: m.update(export_sequence=3), "delivery_gap"),
            ("foreign_currency", lambda m, p: m.update(currency="GBP"), "units"),
            ("corrupted_statement_amount", lambda m, p: p["statements.json"][0].update(amount_microusd=1), "rating_mismatch"),
        ]
        for name, mutate, code in variants:
            variant = temp / name
            expected = resigned_copy(corrected, variant, mutate)
            rejected(name, variant, expected, code)
        rejected("transaction_interruption_before_promotion", corrected, next_hash, "injected_failure", fail_after_insert=True)
        check("uncommitted_candidate_has_no_release", bridge.release(next_hash) is None)
        result = bridge.receive(corrected, cash, next_hash)
        check("actual_producer_correction_accepted", result["status"] == "accepted", result)
        final = bridge.current()
        check("corrected_independent_hand_totals", final["totals"] == HAND["corrected"], final["totals"])
        check("late_correction_reduces_unpaid_by_150_cents", final["change_from_previous"]["gross_unpaid_cents"] == -150)
        check(
            "raw_delta_and_rounding_residual_reconcile",
            final["change_from_previous"]["amount_microusd"] == -1498500 and final["change_from_previous"]["residual_microusd"] == 1500,
        )
        check("old_release_is_immutable", bridge.release(first_hash) == first_report)
        result = bridge.receive(baseline, cash, first_hash)
        check("old_delivery_replay_does_not_roll_back_active", result["status"] == "replayed" and bridge.current() == final)
        with duckdb.connect(bridge.path, read_only=True) as con:
            check("gbp_historical_table_unchanged", con.execute("SELECT * FROM gbp_historical_sentinel").fetchall() == [(12345, "GBP")])
            check(
                "invoice_contracts_v1_v2_unchanged",
                con.execute("SELECT * FROM invoice_contract_v1_sentinel").fetchall() == [(-100,)]
                and con.execute("SELECT * FROM invoice_contract_v2_sentinel").fetchall() == [(-100, 0, 100)],
            )
            check("exactly_two_committed_releases", con.execute("SELECT count(*) FROM meterwork_usd.deliveries").fetchone()[0] == 2)
            ledger = [
                {"code": row[0], "detail": row[1], "preserved_report_sha256": row[2]}
                for row in con.execute(
                    "SELECT code,detail,preserved_report_sha FROM meterwork_usd.exceptions ORDER BY observed_at"
                ).fetchall()
            ]
        check("every_rejection_has_visible_exception", len(ledger) == len(attempts))
    source_files = ["mart/meterwork_bridge.py", "mart/meterwork_bridge_exercise.py", "tests/test_meterwork_bridge.py"]
    evidence = {
        "schema_version": 1,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Local integration consuming real Meterwork API-produced synthetic bundles; receiver collections are explicit simulation",
        "producer_provenance": json.loads((fixtures / "producer-provenance.json").read_bytes()),
        "fixture_files_sha256": {
            path.relative_to(fixtures).as_posix(): sha(path.read_bytes()) for path in sorted(fixtures.rglob("*")) if path.is_file()
        },
        "source_files_sha256": {name: sha((ROOT / name).read_bytes()) for name in source_files},
        "checkout_base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python": platform.python_version(),
        "duckdb": duckdb.__version__,
        "checks": checks,
        "check_count": len(checks),
        "rejected_delivery_attempts": attempts,
        "exception_ledger": ledger,
        "report_sha256": sha(final),
        "baseline_report_sha256": sha(first_report),
        "limitations": [
            "No real customers, payment processor, issued invoices or recognized revenue",
            "One bounded account map and a fixed synthetic collection snapshot; no FX or GBP migration",
            "Sequential single-writer receiver and explicit cumulative deliveries; not a live CDC consumer",
            "SHA256 detects drift against a trusted expected digest; it is not a digital signature",
            "Per-account knowledge cutoffs; no global cross-account source transaction guarantee",
            "No hosted Databricks parity or managed cloud operation is established",
        ],
    }
    output.mkdir(parents=True)
    write_json(output / "evidence.json", evidence)
    write_json(output / "report.json", final)
    write_json(output / "baseline-report.json", first_report)
    write_json(output / "cash-fixture.json", cash)
    (output / "index.html").write_bytes(render_report(final).encode())
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = exercise(args.fixtures, args.output)
    print(json.dumps({"checks": evidence["check_count"], "report_sha256": evidence["report_sha256"]}))


if __name__ == "__main__":
    main()
