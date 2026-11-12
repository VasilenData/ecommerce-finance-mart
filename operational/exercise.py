"""Exercise real PostgreSQL WAL -> Debezium -> Kafka -> DuckDB with synthetic orders."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from mart.cdc import apply_batch, canonical, initialize, state
from mart.cdc_worker import consume

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "operational/compose.yml")]
DSN = "postgresql://lab:local_lab_only@localhost:15432/commerce"
CONNECT = "http://localhost:18083"


def request(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(CONNECT + path, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as result:
        return json.load(result)


def await_connect(timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return request("/connectors")
        except (OSError, http.client.HTTPException):
            time.sleep(1)
    raise TimeoutError("Kafka Connect did not become available")


def source_state(pg):
    orders = pg.execute("select * from orders order by order_id").fetchall()
    daily = pg.execute("""select order_day, count(*), sum(gross_cents), sum(refund_cents), sum(gross_cents-refund_cents)
                          from orders where status='paid' group by order_day order by order_day""").fetchall()
    return {"orders": [list(x) for x in orders], "daily": [[int(v) if i else v for i, v in enumerate(x)] for x in daily]}


def target_state(db):
    with duckdb.connect(str(db)) as con:
        return state(con)


def reconcile(pg, db):
    target, source = target_state(db), source_state(pg)
    assert target["orders"] == source["orders"], {"target": target, "source": source}
    assert target["daily"] == source["daily"], {"target": target, "source": source}
    return target


def exercise(output):
    import importlib.metadata

    import psycopg

    if output.exists():
        raise FileExistsError("Use a new evidence directory; a fresh Compose project is required")
    output.mkdir(parents=True)
    db = output / "operational.duckdb"
    evidence = {"scope": "Synthetic orders through real PostgreSQL, Debezium, Kafka and DuckDB; controlled recovery exercise",
                "started_at": datetime.now(timezone.utc).isoformat(), "checks": [],
                "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip(),
                "runtime": {"python": platform.python_version(), **{
                    name: importlib.metadata.version(name) for name in ["duckdb", "confluent-kafka", "psycopg"]}}}
    records = []
    try:
        existing = await_connect()
        if existing:
            raise RuntimeError("This exercise requires fresh lab containers; connectors already exist")
        request("/connectors", json.loads((ROOT / "operational/connector.json").read_text()))
        with psycopg.connect(DSN, autocommit=True) as pg:
            records += consume(db, count=2)
            assert all(r["value"]["op"] == "r" for r in records)
            initial = reconcile(pg, db)
            assert initial["daily"] == [["2026-11-01", 2, 15000, 0, 15000]]
            evidence["checks"].append({"name": "initial_snapshot", "passed": True, "state": initial})

            pg.execute("update orders set gross_cents=12000, refund_cents=1000 where order_id=1")
            pg.execute("insert into orders values (3,103,'2026-11-02',8000,0,'paid')")
            pg.execute("update orders set status='cancelled' where order_id=2")
            pg.execute("delete from orders where order_id=3")
            records += consume(db, count=5)
            corrected = reconcile(pg, db)
            assert corrected["daily"] == [["2026-11-01", 1, 12000, 1000, 11000]]
            evidence["checks"].append({"name": "insert_correction_refund_cancellation_delete", "passed": True,
                                       "state": corrected})

            pg.execute("update orders set refund_cents=2000 where order_id=1")
            started = time.monotonic()
            crashed = subprocess.run([sys.executable, "-m", "mart.cdc_worker", "--database", str(db), "--count", "1",
                                      "--crash-before-commit"], cwd=ROOT, timeout=100, capture_output=True, text=True)
            assert crashed.returncode == 86, {"returncode": crashed.returncode, "stderr": crashed.stderr}
            assert target_state(db) == corrected, "Uncommitted process death modified the last valid publication"
            records += consume(db, count=1)
            recovered = reconcile(pg, db)
            assert recovered["daily"] == [["2026-11-01", 1, 12000, 2000, 10000]]
            evidence["checks"].append({"name": "sink_process_exit_before_commit_and_resume", "passed": True,
                                       "observed_exercise_seconds": round(time.monotonic() - started, 3), "state": recovered})

            subprocess.run([*COMPOSE, "kill", "-s", "SIGKILL", "connect"], check=True, capture_output=True)
            pg.execute("update orders set gross_cents=13000 where order_id=1")
            pg.execute("insert into orders values (4,104,'2026-11-02',2500,500,'paid')")
            started = time.monotonic()
            subprocess.run([*COMPOSE, "up", "-d", "connect"], check=True, capture_output=True)
            await_connect()
            # Connector restart may redeliver a committed source event at a new Kafka
            # offset. Drain until independently reconciled, recording every event.
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                records += consume(db, count=1, timeout=90)
                current = target_state(db)
                expected = source_state(pg)
                if current["orders"] == expected["orders"] and current["daily"] == expected["daily"]:
                    break
            final = reconcile(pg, db)
            assert final["daily"] == [["2026-11-01", 1, 13000, 2000, 11000], ["2026-11-02", 1, 2500, 500, 2000]]
            evidence["checks"].append({"name": "connector_sigkill_source_changes_and_resume", "passed": True,
                                       "observed_recovery_seconds": round(time.monotonic() - started, 3), "state": final})

        with duckdb.connect(str(db)) as con:
            replay = apply_batch(con, records)
            assert replay["applied"] == 0 and replay["duplicates"] == len(records)
            assert state(con) == final
        evidence["checks"].append({"name": "duplicate_offset_replay", "passed": True, "duplicates": len(records)})
        with duckdb.connect(str(output / "rebuilt.duckdb")) as con:
            initialize(con)
            apply_batch(con, records)
            assert state(con) == final
        evidence["checks"].append({"name": "complete_event_history_rebuild", "passed": True})
        evidence["connector_status"] = request("/connectors/finance-orders/status")
        evidence["passed"] = True
    except BaseException as exc:
        evidence["passed"] = False
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        event_bytes = ("\n".join(canonical(r) for r in records) + "\n").encode()
        (output / "events.jsonl").write_bytes(event_bytes)
        evidence["events_sha256"] = hashlib.sha256(event_bytes).hexdigest()
        evidence["events_recorded"] = len(records)
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        evidence["implementation_sha256"] = {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [ROOT / "mart/cdc.py", ROOT / "mart/cdc_worker.py", Path(__file__),
                      ROOT / "operational/compose.yml", ROOT / "operational/init.sql", ROOT / "operational/connector.json"]}
        (output / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "checks": len(evidence["checks"]), "events": len(records)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    exercise(parser.parse_args().output.resolve())
