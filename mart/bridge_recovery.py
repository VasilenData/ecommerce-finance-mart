"""Observe abrupt receiver termination, reopen DuckDB, and reconcile exact recovery.

This is a bounded synthetic local exercise, not hardware power-loss recovery or
an assertion about a live service. Only this command's temporary processes and
database are interrupted. Existing evidence directories are never replaced.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from mart.meterwork_bridge import Bridge, StoredReleaseError, canonical, cash_fixture, sha
from mart.meterwork_bridge_exercise import FIXTURES, HAND, manifest_hash

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.write_bytes((canonical(value) + "\n").encode())


def worker(database, boundary, ready):
    baseline, corrected = FIXTURES / "baseline", FIXTURES / "corrected"
    cash = cash_fixture(json.loads((baseline / "accounts.json").read_bytes()))

    def observed(connection):
        receipt = {
            "pid": os.getpid(),
            "boundary": boundary,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "uncommitted_delivery_count": connection.execute("SELECT count(*) FROM meterwork_usd.deliveries").fetchone()[0],
            "uncommitted_active_bundle": connection.execute("SELECT bundle_sha FROM meterwork_usd.active").fetchone()[0],
            "candidate_bundle": manifest_hash(corrected),
        }
        temporary = ready.with_suffix(".pending")
        write_json(temporary, receipt)
        os.replace(temporary, ready)
        # Parent kills only after reading the observed SQL boundary. A closed pipe
        # rolls back instead of accidentally letting this exercise commit.
        sys.stdin.buffer.read(1)
        raise RuntimeError("Parent did not terminate the prepared exercise worker")

    hooks = {"_after_insert" if boundary == "insert" else "_after_promote": observed}
    Bridge(database).receive(corrected, cash, manifest_hash(corrected), **hooks)


def await_ready(process, path, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            result = json.loads(path.read_bytes())
            if process.poll() is not None:
                raise RuntimeError("Worker exited before interruption")
            return result
        if process.poll() is not None:
            raise RuntimeError("Worker exited without observing the transaction boundary")
        time.sleep(0.01)  # Condition polling, not a guessed pre-kill delay.
    raise TimeoutError("Worker did not reach the observed write boundary")


def reopen(database):
    deadline, attempts = time.monotonic() + 5, 0
    while True:
        attempts += 1
        try:
            return Bridge(database), attempts
        except duckdb.IOException as error:
            # A terminated process's file handle can briefly remain unavailable on
            # Windows. Retry only an observed file-lock/sharing denial.
            locked = any(text in str(error).lower() for text in ("could not set lock", "being used by another process"))
            if not locked or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def exercise(output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Use a new evidence directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    checks, interruptions = [], []

    def check(name, passed):
        if not passed:
            raise AssertionError(name)
        checks.append({"name": name, "passed": True})

    baseline, corrected = FIXTURES / "baseline", FIXTURES / "corrected"
    first_hash, second_hash = manifest_hash(baseline), manifest_hash(corrected)
    cash = cash_fixture(json.loads((baseline / "accounts.json").read_bytes()))
    with tempfile.TemporaryDirectory(prefix="finance-receiver-recovery-") as temporary:
        root = Path(temporary)
        for boundary in ("insert", "promote"):
            database, ready = root / (boundary + ".duckdb"), root / (boundary + ".json")
            receiver = Bridge(database)
            check(boundary + ": baseline accepted", receiver.receive(baseline, cash, first_hash)["status"] == "accepted")
            before = canonical(receiver.current())
            check(boundary + ": independent baseline arithmetic", receiver.current()["totals"] == HAND["baseline"])
            with (root / (boundary + ".log")).open("wb") as log:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "mart.bridge_recovery",
                        "--worker",
                        "--database",
                        str(database),
                        "--boundary",
                        boundary,
                        "--ready",
                        str(ready),
                    ],
                    cwd=ROOT,
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    observed = await_ready(child, ready)
                    check(boundary + ": observed uncommitted insert", observed["uncommitted_delivery_count"] == 2)
                    expected_pointer = first_hash if boundary == "insert" else second_hash
                    check(boundary + ": observed pointer boundary", observed["uncommitted_active_bundle"] == expected_pointer)
                    killed_at = time.perf_counter()
                    child.kill()
                    exit_code = child.wait(timeout=10)
                    check(boundary + ": worker abruptly terminated", exit_code != 0)
                    receiver, probes = reopen(database)
                    check(boundary + ": restart preserves exact prior report", canonical(receiver.current()) == before)
                    check(boundary + ": candidate rolled back", receiver.release(second_hash) is None)
                    check(boundary + ": retained history verifies", receiver.audit()["verified_releases"] == 1)
                    recovered_ms = round((time.perf_counter() - killed_at) * 1000, 3)
                    check(boundary + ": retry accepted", receiver.receive(corrected, cash, second_hash)["status"] == "accepted")
                    final = canonical(receiver.current())
                    check(boundary + ": recovered arithmetic", receiver.current()["totals"] == HAND["corrected"])
                    check(boundary + ": old close remains unchanged", canonical(receiver.release(first_hash)) == before)
                    check(boundary + ": retry is idempotent", receiver.receive(corrected, cash, second_hash)["status"] == "replayed")
                    check(boundary + ": replay cannot change current", canonical(receiver.current()) == final)
                    check(boundary + ": independent reopen verifies both releases", Bridge(database).audit()["verified_releases"] == 2)
                    interruptions.append(
                        {
                            **observed,
                            "exit_code": exit_code,
                            "reopen_attempts": probes,
                            "kill_to_verified_prior_ms": recovered_ms,
                            "prior_report_sha256": sha(before.encode()),
                            "recovered_report_sha256": sha(final.encode()),
                        }
                    )
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait(timeout=10)
                    if child.stdin is not None:
                        child.stdin.close()

        # A changed stored amount was previously served without checking report_sha.
        original = receiver.current()
        bad = json.loads(canonical(original))
        bad["totals"]["invoice_cents"] += 100000
        with duckdb.connect(str(database)) as connection:
            connection.execute("UPDATE meterwork_usd.deliveries SET report_json=? WHERE bundle_sha=?", [canonical(bad), second_hash])
        for name, call in (("current", receiver.current), ("history", lambda: receiver.release(second_hash)), ("audit", receiver.audit)):
            try:
                call()
                raise AssertionError("Corrupt report was served")
            except StoredReleaseError:
                check("stored corruption refused by " + name, True)
        with duckdb.connect(str(database)) as connection:
            connection.execute("UPDATE meterwork_usd.deliveries SET report_json=? WHERE bundle_sha=?", [canonical(original), second_hash])
        check(
            "exact report restoration recovers verified history",
            receiver.audit()["verified_releases"] == 2 and receiver.current() == original,
        )

    files = ["mart/meterwork_bridge.py", "mart/bridge_recovery.py", "tests/test_bridge_recovery.py"]
    evidence = {
        "schema_version": 1,
        "observed_at_utc": started,
        "python": platform.python_version(),
        "duckdb": duckdb.__version__,
        "platform": platform.platform(),
        "scope": "Synthetic local receiver process termination and storage-drift exercise",
        "source_files_sha256": {name: sha((ROOT / name).read_bytes()) for name in files},
        "fixture_files_sha256": {p.relative_to(FIXTURES).as_posix(): sha(p.read_bytes()) for p in sorted(FIXTURES.rglob("*.json"))},
        "checks": checks,
        "check_count": len(checks),
        "interruptions": interruptions,
        "limitations": [
            "Single writer; no concurrent receiver service or managed warehouse is claimed",
            "Process termination is not hardware power-loss or backup-restoration evidence",
            "Stored report digest shares the database trust boundary; an administrator can rewrite both",
            "Observed recovery timings are individual local measurements, not a service-level guarantee",
        ],
    }
    with tempfile.TemporaryDirectory(prefix=".receiver-evidence-", dir=output.parent) as temporary:
        stage = Path(temporary)
        write_json(stage / "evidence.json", evidence)
        stage.rename(output)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--database", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--boundary", choices=("insert", "promote"), help=argparse.SUPPRESS)
    parser.add_argument("--ready", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if not all((args.database, args.boundary, args.ready)):
            parser.error("Worker requires database, boundary and readiness path")
        worker(args.database, args.boundary, args.ready)
    else:
        if args.output is None:
            parser.error("--output is required")
        result = exercise(args.output)
        print(json.dumps({"checks_passed": result["check_count"], "interruptions": len(result["interruptions"])}))


if __name__ == "__main__":
    main()
