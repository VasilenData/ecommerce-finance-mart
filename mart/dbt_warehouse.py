"""Execute and independently reconcile a bounded dbt warehouse exercise.

DuckDB is local. PostgreSQL is a disposable integration target, not a cloud
production warehouse. No backend or credentials are published by this command.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from importlib.metadata import version
from pathlib import Path

import duckdb

from mart.subscriptions import COMMON, FEEDS, MRR_FIELDS, ROOT, build_report, select_snapshot, timestamp, validate_source

PROJECT = ROOT / "dbt_subscriptions"
MART_KEYS = {
    "customer_mrr_bridge": ("month", "customer_id"),
    "monthly_mrr_bridge": ("month",),
    "monthly_cash_bridge": ("month",),
    "invoice_balances": ("invoice_id",),
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def python_oracle(source, cutoff, start="2026-01-01", end="2026-04-01"):
    """Independent loops over selected source records; no warehouse SQL or intervals."""
    selected = select_snapshot(validate_source(source), timestamp(cutoff))
    changes = list(selected["subscription_changes"].values())
    customers = sorted({row["customer_id"] for row in changes})
    months, day = [], date.fromisoformat(start)
    while day.isoformat() < end:
        months.append(day.isoformat())
        day = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    result = {name: [] for name in MART_KEYS}

    def customer_state(customer, boundary):
        states = {}
        for row in changes:
            sid = row["subscription_id"]
            if row["customer_id"] == customer and row["effective_date"] < boundary:
                if sid not in states or states[sid]["effective_date"] < row["effective_date"]:
                    states[sid] = row
        return sum(row["monthly_amount_cents"] for row in states.values())

    for index, month in enumerate(months):
        boundary = months[index + 1] if index + 1 < len(months) else end
        for customer in customers:
            opening, closing = customer_state(customer, month), customer_state(customer, boundary)
            prior = any(
                row["customer_id"] == customer and row["effective_date"] < month and row["monthly_amount_cents"] > 0 for row in changes
            )
            result["customer_mrr_bridge"].append(
                {
                    "month": month,
                    "customer_id": customer,
                    "opening_mrr_cents": opening,
                    "closing_mrr_cents": closing,
                    "previously_paying": prior,
                    "new_mrr_cents": closing if opening == 0 and not prior else 0,
                    "reactivation_mrr_cents": closing if opening == 0 and prior else 0,
                    "expansion_mrr_cents": closing - opening if opening > 0 and closing > opening else 0,
                    "contraction_mrr_cents": opening - closing if 0 < closing < opening else 0,
                    "churn_mrr_cents": opening if closing == 0 else 0,
                }
            )
        result["monthly_mrr_bridge"].append(
            {
                "month": month,
                **{field: sum(row[field] for row in result["customer_mrr_bridge"] if row["month"] == month) for field in MRR_FIELDS},
            }
        )
        totals, before = {}, {}
        for feed, business in (("invoices", "issued_date"), ("payments", "paid_date"), ("refunds", "refunded_date")):
            records = list(selected[feed].values())
            totals[feed] = sum(row["amount_cents"] for row in records if month <= row[business] < boundary)
            before[feed] = sum(row["amount_cents"] for row in records if row[business] < month)
        opening = before["invoices"] - before["payments"] + before["refunds"]
        net = totals["payments"] - totals["refunds"]
        result["monthly_cash_bridge"].append(
            {
                "month": month,
                "opening_balance_cents": opening,
                "invoiced_cents": totals["invoices"],
                "paid_cents": totals["payments"],
                "refunded_cents": totals["refunds"],
                "net_cash_cents": net,
                "closing_balance_cents": opening + totals["invoices"] - net,
            }
        )
    for iid, invoice in selected["invoices"].items():
        if invoice["issued_date"] >= end:
            continue
        payments = {pid: row for pid, row in selected["payments"].items() if row["invoice_id"] == iid}
        paid = sum(row["amount_cents"] for row in payments.values() if row["paid_date"] < end)
        refunded = sum(
            row["amount_cents"] for row in selected["refunds"].values() if row["payment_id"] in payments and row["refunded_date"] < end
        )
        result["invoice_balances"].append(
            {
                "invoice_id": iid,
                "customer_id": invoice["customer_id"],
                "invoiced_cents": invoice["amount_cents"],
                "paid_cents": paid,
                "refunded_cents": refunded,
                "balance_cents": invoice["amount_cents"] - paid + refunded,
            }
        )
    for name, keys in MART_KEYS.items():
        result[name].sort(key=lambda row, fields=keys: tuple(row[key] for key in fields))
    return result


class Warehouse:
    def __init__(self, adapter, directory):
        self.adapter = adapter
        self.directory = Path(directory)
        self.schema = "fm_" + uuid.uuid4().hex[:16]
        self.raw_schema = self.schema + "_raw"
        self.path = self.directory / "warehouse.duckdb"
        self.environment = {
            **os.environ,
            "FINANCE_WAREHOUSE_SCHEMA": self.schema,
            "FINANCE_WAREHOUSE_PATH": str(self.path),
            "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        }
        self.url = None
        if adapter == "postgres":
            from psycopg.conninfo import conninfo_to_dict

            self.url = os.environ["FINANCE_TEST_DATABASE_URL"]
            info = conninfo_to_dict(self.url)
            if not info.get("dbname", "").endswith("_test"):
                raise ValueError("Use a dedicated PostgreSQL database ending in _test")
            for name, key in (("HOST", "host"), ("PORT", "port"), ("USER", "user"), ("PASSWORD", "password"), ("DATABASE", "dbname")):
                if key in info:
                    self.environment["FINANCE_PG_" + name] = info[key]
        with self.connection() as con:
            con.execute(f"CREATE SCHEMA {self.raw_schema}")
            for name, columns in FEEDS.items():
                fields = ", ".join(f"{key} {kind}" for key, kind in (COMMON | columns).items())
                con.execute(
                    f"CREATE TABLE {self.raw_schema}.{name} ({fields}, ingestion_sequence BIGINT PRIMARY KEY, "
                    "payload_sha256 VARCHAR NOT NULL, UNIQUE(record_id, revision))"
                )

    @contextmanager
    def connection(self):
        if self.adapter == "duckdb":
            con = duckdb.connect(str(self.path))
        else:
            import psycopg

            con = psycopg.connect(self.url, autocommit=True)
        try:
            con.execute("SET TIME ZONE 'UTC'")
            yield con
        finally:
            con.close()

    def append(self, source):
        """Single-writer transport; validate the full source and atomically append new identities.

        A repeated identity with changed payload is rejected across every feed
        before any insert. Sequence is allocated here, never supplied by a client.
        """
        source = validate_source(source)
        additions = []
        placeholder = "?" if self.adapter == "duckdb" else "%s"
        with self.connection() as con:
            con.execute("BEGIN")
            try:
                for name, columns in FEEDS.items():
                    records = con.execute(
                        f"SELECT record_id, revision, ingestion_sequence, payload_sha256 FROM {self.raw_schema}.{name}"
                    ).fetchall()
                    known = {(row[0], row[1]): row[3] for row in records}
                    sequence = max((row[2] for row in records), default=0)
                    for row in source[name]:
                        key, digest = (row["record_id"], row["revision"]), sha(canonical(row).encode())
                        if key in known:
                            if known[key] != digest:
                                raise ValueError("Immutable journal identity has changed payload")
                            continue
                        sequence += 1
                        values = [row[field] for field in COMMON | columns]
                        additions.append((name, values + [sequence, digest]))
                for name, values in additions:
                    con.execute(f"INSERT INTO {self.raw_schema}.{name} VALUES ({','.join([placeholder] * len(values))})", values)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return len(additions)

    def counts(self, history=False):
        with self.connection() as con:
            return {
                name: con.execute(
                    f"SELECT count(*) FROM {self.schema + '.history_' if history else self.raw_schema + '.'}{name}"
                ).fetchone()[0]
                for name in FEEDS
            }

    def run(self, label, cutoff, *, full_refresh=False, definition=1, start="2026-01-01", end="2026-04-01"):
        variables = {
            "raw_schema": self.raw_schema,
            "knowledge_cutoff": cutoff,
            "period_start": start,
            "period_end": end,
            "growth_definition": definition,
        }
        arguments = [
            "build",
            "--project-dir",
            str(PROJECT),
            "--profiles-dir",
            str(PROJECT),
            "--target",
            self.adapter,
            "--vars",
            json.dumps(variables),
            "--target-path",
            str(self.directory / "target"),
            "--log-path",
            str(self.directory / "logs"),
            "--no-use-colors",
        ]
        if full_refresh:
            arguments.append("--full-refresh")
        started = time.perf_counter()
        result = subprocess.run(
            [sys.executable, "-c", "from dbt.cli.main import cli; cli()", *arguments],
            env=self.environment,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            # Connection strings/passwords are never in arguments. Logs remain local
            # and are not copied to the public evidence report.
            private = ROOT / "dist/private-dbt-failures" / self.schema
            private.mkdir(parents=True, exist_ok=True)
            (private / (label + "-failure.log")).write_text(result.stdout + result.stderr, encoding="utf-8")
            raise RuntimeError(f"dbt {label} failed; inspect private log in {private}")
        run = json.loads((self.directory / "target/run_results.json").read_text(encoding="utf-8"))
        if not run["results"] or any(row["status"] not in {"success", "pass"} for row in run["results"]):
            raise ValueError("dbt results contain failed/skipped nodes")
        return {
            "scenario": label,
            "full_refresh": full_refresh,
            "knowledge_cutoff": cutoff,
            "growth_definition": definition,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "nodes": [{"id": row["unique_id"], "status": row["status"]} for row in run["results"]],
        }

    def table(self, name):
        if name not in {*MART_KEYS, "monthly_subscription_metrics", "management_scorecard"}:
            raise ValueError("Unknown evidence table")
        with self.connection() as con:
            cursor = con.execute(f"SELECT * FROM {self.schema}.{name}")
            columns = [item[0] for item in cursor.description]
            rows = [
                {
                    key: value.isoformat() if isinstance(value, (date, datetime)) else value
                    for key, value in zip(columns, values, strict=True)
                }
                for values in cursor.fetchall()
            ]
        keys = MART_KEYS.get(name, ("month",))
        return sorted(rows, key=lambda row: tuple(row[key] for key in keys))

    def close(self):
        if self.adapter == "postgres":
            if not re.fullmatch(r"fm_[a-f0-9]{16}", self.schema):
                raise ValueError("Refusing unexpected cleanup schema")
            with self.connection() as con:
                # Only the two unique schemas allocated by this instance.
                con.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
                con.execute(f"DROP SCHEMA {self.raw_schema} CASCADE")


def exercise(adapter, output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Evidence is immutable; choose a new output directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = (ROOT / "subscriptions/demo.json").read_bytes()
    complete = json.loads(source_bytes)
    baseline = {**complete, **{name: [row for row in complete[name] if row["revision"] == 1] for name in FEEDS}}
    checks, runs, reports = [], [], []

    def check(name, condition, detail):
        if not condition:
            raise AssertionError(name)
        checks.append({"name": name, "passed": True, "detail": detail})

    # The system temporary directory also avoids Windows' deep compiled-node paths.
    with tempfile.TemporaryDirectory(prefix="finance-dbt-") as temporary:
        directory = Path(temporary)
        warehouse = Warehouse(adapter, directory)
        try:

            def observe(label, source, cutoff="2026-04-04T00:00:00Z", **options):
                run = warehouse.run(label, cutoff, **options)
                actual = {name: warehouse.table(name) for name in MART_KEYS}
                oracle = python_oracle(source, cutoff, options.get("start", "2026-01-01"), options.get("end", "2026-04-01"))
                reference = build_report(source, cutoff, options.get("start", "2026-01-01"), options.get("end", "2026-04-01"))
                check(label + ": all marts equal independent Python oracle", actual == oracle, "Every row and metric in four marts")
                check(
                    label + ": preserves existing reporting contract",
                    actual == {name: reference[name] for name in MART_KEYS},
                    "Existing report is a compatibility reference, distinct from the independent Python oracle",
                )
                check(
                    label + ": journal is fully represented", warehouse.counts() == warehouse.counts(history=True), "Four feed row counts"
                )
                runs.append(run)
                reports.append(
                    {
                        "scenario": label,
                        "knowledge_cutoff": cutoff,
                        "marts": actual,
                        "metrics": warehouse.table("monthly_subscription_metrics"),
                        "scorecard": warehouse.table("management_scorecard"),
                    }
                )
                return actual

            empty = {**complete, **{name: [] for name in FEEDS}}
            observe("empty_source", empty)
            warehouse.append(baseline)
            first = observe("baseline", baseline)
            check("exact source replay adds no records", warehouse.append(baseline) == 0, "Immutable record_id/revision identity")
            replay = observe("replay", baseline)
            check("replay preserves all rows", first == replay, "No duplicated identities or multiplied financial amounts")
            check(
                "new corrections are appended",
                warehouse.append(complete) == 2,
                "Subscription and invoice corrections have earlier business dates",
            )
            corrected = observe("corrections", complete)
            check(
                "untouched January cash is preserved",
                first["monthly_cash_bridge"][0] == corrected["monthly_cash_bridge"][0],
                "Corrections affect February and the carried March balance",
            )
            old_close = observe("historical_cutoff", complete, cutoff="2026-04-01T00:00:00Z")
            check("rewinding cutoff preserves original close", old_close == first, "Revisions remain in the journal but are not yet known")
            late = copy.deepcopy(complete)
            late["invoices"].append(
                {
                    "record_id": "late-invoice",
                    "revision": 1,
                    "recorded_at": "2026-01-05T12:00:00Z",
                    "customer_id": "E",
                    "issued_date": "2026-01-05",
                    "amount_cents": 5000,
                }
            )
            late["payments"].append(
                {
                    "record_id": "late-payment",
                    "revision": 1,
                    "recorded_at": "2026-01-06T12:00:00Z",
                    "invoice_id": "late-invoice",
                    "paid_date": "2026-01-06",
                    "amount_cents": 4000,
                }
            )
            skipped = sum(
                timestamp(late[name][-1]["recorded_at"]) <= max(timestamp(row["recorded_at"]) for row in complete[name])
                for name in ("invoices", "payments")
            )
            check(
                "timestamp-watermark counterexample reproduced",
                skipped == 2,
                "A max(recorded_at) filter would miss both newly delivered records; ingestion sequence does not",
            )
            check(
                "late transport backfill appends both records",
                warehouse.append(late) == 2,
                "Older knowledge/business dates, newer transport sequence",
            )
            backfilled = observe("late_backfill", late)
            check(
                "late invoice/payment arithmetic",
                backfilled["monthly_cash_bridge"][0]["closing_balance_cents"] == 1000,
                "January receives 5000 billed minus 4000 paid; subsequent months carry the 1000 balance",
            )
            counts = warehouse.counts()
            conflicting = copy.deepcopy(late)
            conflicting["invoices"][0]["amount_cents"] += 1
            try:
                warehouse.append(conflicting)
                raise AssertionError("Conflicting journal identity was accepted")
            except ValueError as error:
                check(
                    "conflicting replay rejected atomically",
                    "Immutable journal identity" in str(error) and warehouse.counts() == counts,
                    "No feed is partially appended; existing candidate remains unchanged",
                )
            refreshed = observe("full_refresh", late, full_refresh=True)
            check("incremental and full refresh are row-identical", refreshed == backfilled, "All four marts, not only sums")
            metric_v1 = warehouse.table("monthly_subscription_metrics")
            observe("definition_v2", late, definition=2)
            metric_v2 = warehouse.table("monthly_subscription_metrics")
            deltas = [
                {
                    "month": old["month"],
                    "before_cents": old["growth_mrr_cents"],
                    "after_cents": new["growth_mrr_cents"],
                    "delta_cents": new["growth_mrr_cents"] - old["growth_mrr_cents"],
                }
                for old, new in zip(metric_v1, metric_v2, strict=True)
            ]
            check(
                "metric definition change propagates to scorecard",
                [row["delta_cents"] for row in deltas] == [0, 0, 9000]
                and [row["growth_mrr_cents"] for row in warehouse.table("management_scorecard")]
                == [row["growth_mrr_cents"] for row in metric_v2],
                "Version 2 explicitly includes reactivation; March changes by 9000 pence. Actuals and ledger definitions are unchanged",
            )
            extended = observe("new_reporting_month", late, cutoff="2026-05-01T00:00:00Z", end="2026-05-01")
            check(
                "new month carries prior balances without inventing activity",
                extended["monthly_cash_bridge"][-1]["closing_balance_cents"]
                == backfilled["monthly_cash_bridge"][-1]["closing_balance_cents"]
                and extended["monthly_cash_bridge"][-1]["invoiced_cents"] == 0,
                "April contains no new invoice activity; the historical balance remains visible",
            )
            manifest = json.loads((directory / "target/manifest.json").read_text(encoding="utf-8"))
            graph = {key: node["depends_on"]["nodes"] for key, node in manifest["nodes"].items() if node["resource_type"] == "model"}
        finally:
            warehouse.close()
        source_files = [
            Path(name)
            for name in (
                "mart/dbt_warehouse.py",
                "mart/subscriptions.py",
                "sql/70_subscriptions.sql",
                "requirements-dbt.txt",
                "subscriptions/demo.json",
                "tests/test_dbt_warehouse.py",
                ".github/workflows/dbt-warehouse.yml",
            )
        ]
        source_files.extend(
            path.relative_to(ROOT)
            for path in PROJECT.rglob("*")
            if path.is_file() and path.suffix in {".yml", ".sql"} and path.name != ".user.yml"
        )
        result = {
            "schema_version": 1,
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "adapter": adapter,
            "scope": "Synthetic fixture on " + ("local DuckDB" if adapter == "duckdb" else "disposable PostgreSQL; not a cloud warehouse"),
            "execution_environment": "GitHub-hosted Linux" if os.getenv("GITHUB_ACTIONS") == "true" else platform.platform(),
            "source_commit": os.getenv("GITHUB_SHA", "uncommitted-local"),
            "ci_run_id": os.getenv("GITHUB_RUN_ID"),
            "versions": {name: version(name) for name in ("dbt-core", "dbt-duckdb", "dbt-postgres", "duckdb", "psycopg")},
            "source_sha256": sha(source_bytes),
            "source_files": {str(path).replace("\\", "/"): sha((ROOT / path).read_bytes()) for path in source_files},
            "checks": checks,
            "runs": runs,
            "reports": reports,
            "metric_definition_changes": deltas,
            "dbt_model_dependencies": graph,
            "limits": [
                "Four raw revision ledgers are incremental; downstream views recompute the bounded reporting window.",
                "Single-writer ingestion sequence; no multiwriter or high-volume performance claim.",
                "Isolated development schemas; dbt does not provide an atomic multi-model publication here.",
                "Only this validated evidence bundle is published after every check passes.",
                "Source contract has no tombstone/deletion events; zero-value invoice corrections and cancellations retain history.",
                "No cloud deployment, live users, human approval, production savings or unaided owner assessment.",
            ],
        }
        # Stage beside the final directory so the rename stays on one filesystem.
        with tempfile.TemporaryDirectory(prefix=".dbt-release-", dir=output.parent) as release:
            staging = Path(release)
            (staging / "evidence.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            (staging / "source.json").write_bytes(source_bytes)
            (staging / "backfill-source.json").write_text(json.dumps(late, indent=2) + "\n", encoding="utf-8")
            staging.rename(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", choices=["duckdb", "postgres"], default="duckdb")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = exercise(args.adapter, args.output)
    print(
        json.dumps(
            {"adapter": args.adapter, "checks_passed": len(result["checks"]), "dbt_builds": len(result["runs"]), "output": str(args.output)}
        )
    )


if __name__ == "__main__":
    main()
