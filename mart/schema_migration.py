"""Version an invoice contract and atomically publish validated, frozen mart snapshots.

Synthetic, single-writer DuckDB/PostgreSQL exercise; not a managed warehouse service.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import platform
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from mart.dbt_warehouse import MART_KEYS, PROJECT, Warehouse, canonical, python_oracle, sha
from mart.subscriptions import EXPORT_FIELDS, FEEDS, ROOT

CONTRACTS = {
    **EXPORT_FIELDS,
    "invoice_balances_v2": [
        "invoice_id",
        "customer_id",
        "invoiced_cents",
        "paid_cents",
        "refunded_cents",
        "net_balance_cents",
        "gross_unpaid_cents",
        "credit_cents",
    ],
}


def fixture():
    """Two invoices for one customer: unpaid 4000, credit 1500, net 2500 pence."""
    result = {
        "schema_version": 1,
        "currency": "GBP",
        "scope": "synthetic",
        "complete_subscription_history": True,
        **{feed: [] for feed in FEEDS},
    }

    def row(identity, day, **values):
        return {"record_id": identity, "revision": 1, "recorded_at": day + "T12:00:00Z", **values}

    result["invoices"] = [
        row("i1", "2026-01-01", customer_id="A", issued_date="2026-01-01", amount_cents=10000),
        row("i2", "2026-01-01", customer_id="A", issued_date="2026-01-01", amount_cents=5000),
    ]
    result["payments"] = [
        row("p1", "2026-01-02", invoice_id="i1", paid_date="2026-01-02", amount_cents=12000),
        row("p2", "2026-01-02", invoice_id="i2", paid_date="2026-01-02", amount_cents=1000),
    ]
    result["refunds"] = [row("r1", "2026-01-03", payment_id="p1", refunded_date="2026-01-03", amount_cents=500)]
    return result


def expected_rows(source, cutoff):
    result = python_oracle(source, cutoff)
    result["invoice_balances_v2"] = [
        {
            **{key: value for key, value in row.items() if key != "balance_cents"},
            "net_balance_cents": row["balance_cents"],
            "gross_unpaid_cents": max(0, row["balance_cents"]),
            "credit_cents": max(0, -row["balance_cents"]),
        }
        for row in result["invoice_balances"]
    ]
    return result


def read_contracts(con, schema):
    """Read exact ordered columns and canonical rows; refuse silent schema drift."""
    if not re.fullmatch(r"fm_[a-f0-9]{16}(?:_published|_release_[a-z][a-z0-9_]*)?", schema):
        raise ValueError("Unexpected schema identity")
    result = {}
    for table, columns in CONTRACTS.items():
        cursor = con.execute(f"SELECT * FROM {schema}.{table}")
        if [item[0] for item in cursor.description] != columns:
            raise ValueError("Consumer column contract changed: " + table)
        rows = [
            {key: value.isoformat() if isinstance(value, (date, datetime)) else value for key, value in zip(columns, values, strict=True)}
            for values in cursor.fetchall()
        ]
        for row in rows:
            if any(not isinstance(row[key], int) or isinstance(row[key], bool) for key in columns if key.endswith("_cents")):
                raise ValueError("Money must remain non-null integer pence")
        keys = MART_KEYS.get(table, ("invoice_id",))
        result[table] = sorted(rows, key=lambda row: tuple(row[key] for key in keys))
    return result


class ReleasePublisher:
    """Frozen candidates plus transactionally replaced consumer views, one writer only."""

    def __init__(self, warehouse):
        self.warehouse = warehouse
        self.schema = warehouse.schema + "_published"
        self.candidates = []
        with warehouse.connection() as con:
            con.execute(f"CREATE SCHEMA {self.schema}")
            con.execute(
                f"CREATE TABLE {self.schema}.releases (release_id VARCHAR PRIMARY KEY, candidate_schema VARCHAR NOT NULL, "
                "rows_sha256 VARCHAR NOT NULL, source_json VARCHAR NOT NULL, knowledge_cutoff VARCHAR NOT NULL)"
            )
            con.execute(
                f"CREATE TABLE {self.schema}.active_release (singleton INTEGER PRIMARY KEY CHECK(singleton=1), release_id VARCHAR NOT NULL)"
            )

    @property
    def placeholder(self):
        return "?" if self.warehouse.adapter == "duckdb" else "%s"

    def stage(self, release_id, source, cutoff):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,20}", release_id):
            raise ValueError("Invalid release identity")
        schema = self.warehouse.schema + "_release_" + release_id
        expected = expected_rows(source, cutoff)
        with self.warehouse.connection() as con:
            con.execute("BEGIN")
            try:
                con.execute(f"CREATE SCHEMA {schema}")
                for table in CONTRACTS:
                    con.execute(f"CREATE TABLE {schema}.{table} AS SELECT * FROM {self.warehouse.schema}.{table}")
                actual = read_contracts(con, schema)
                if actual != expected:
                    raise ValueError("Candidate differs from independent oracle")
                digest = sha(canonical(actual).encode())
                p = self.placeholder
                con.execute(
                    f"INSERT INTO {self.schema}.releases VALUES ({','.join([p] * 5)})",
                    [release_id, schema, digest, canonical(source), cutoff],
                )
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        self.candidates.append(schema)
        return {"release_id": release_id, "rows_sha256": digest, "rows": actual}

    def promote(self, release_id, *, fail_after_view=None):
        """Same operation for forward promotion and rollback; reject modified snapshots."""
        with self.warehouse.connection() as con:
            con.execute("BEGIN")
            try:
                release = con.execute(
                    f"SELECT candidate_schema, rows_sha256 FROM {self.schema}.releases WHERE release_id={self.placeholder}", [release_id]
                ).fetchone()
                if release is None:
                    raise ValueError("Unknown validated release")
                schema, digest = release
                if sha(canonical(read_contracts(con, schema)).encode()) != digest:
                    raise ValueError("Frozen candidate changed after validation")
                for index, (table, columns) in enumerate(CONTRACTS.items(), start=1):
                    con.execute(f"CREATE OR REPLACE VIEW {self.schema}.{table} AS SELECT {','.join(columns)} FROM {schema}.{table}")
                    if index == fail_after_view:
                        raise RuntimeError("Injected interruption after consumer view replacement")
                con.execute(f"DELETE FROM {self.schema}.active_release")
                con.execute(f"INSERT INTO {self.schema}.active_release VALUES (1,{self.placeholder})", [release_id])
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise

    def view_rows(self):
        """Inspect convenience views after a completed operation, not a concurrent snapshot API."""
        with self.warehouse.connection() as con:
            return read_contracts(con, self.schema)

    def consume(self, *, _after_pin=None):
        """Pin one immutable release schema before reading multiple tables.

        Repeatable-read alone does not pin mutable view definitions. The private
        callback synchronizes a real second-connection promotion in the exercise.
        """
        with self.warehouse.connection() as con:
            con.execute("BEGIN")
            if self.warehouse.adapter == "postgres":
                con.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            try:
                identity, schema, digest = con.execute(
                    f"SELECT a.release_id,r.candidate_schema,r.rows_sha256 FROM {self.schema}.active_release a "
                    f"JOIN {self.schema}.releases r ON r.release_id=a.release_id WHERE a.singleton=1"
                ).fetchone()
                if _after_pin is not None:
                    _after_pin()
                rows = read_contracts(con, schema)
                if sha(canonical(rows).encode()) != digest:
                    raise ValueError("Pinned release content changed")
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return {"release_id": identity, "rows": rows, "rows_sha256": sha(canonical(rows).encode())}

    def frozen_source(self, release_id):
        with self.warehouse.connection() as con:
            row = con.execute(
                f"SELECT source_json, knowledge_cutoff FROM {self.schema}.releases WHERE release_id={self.placeholder}", [release_id]
            ).fetchone()
        if row is None:
            raise ValueError("Unknown release")
        return json.loads(row[0]), row[1]

    def close(self):
        with self.warehouse.connection() as con:
            con.execute(f"DROP SCHEMA {self.schema} CASCADE")
            for schema in self.candidates:
                con.execute(f"DROP SCHEMA {schema} CASCADE")


def exercise(adapter, output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Evidence output must be new")
    output.parent.mkdir(parents=True, exist_ok=True)
    baseline, cutoff = fixture(), "2026-04-01T00:00:00Z"
    checks, runs, releases = [], [], []

    def check(name, passed):
        if not passed:
            raise AssertionError(name)
        checks.append({"name": name, "passed": True})

    with tempfile.TemporaryDirectory(prefix="finance-migration-") as temporary:
        warehouse = Warehouse(adapter, temporary)
        publisher = ReleasePublisher(warehouse)
        try:
            warehouse.append(baseline)
            runs.append(warehouse.run("migration_baseline", cutoff))
            original = publisher.stage("close_v1", baseline, cutoff)
            releases.append(original)
            publisher.promote("close_v1")
            first = publisher.consume()
            first_bytes = canonical(first).encode()
            rows = first["rows"]["invoice_balances_v2"]
            check(
                "hand calculated unpaid and credit cannot be netted",
                [(row["gross_unpaid_cents"], row["credit_cents"], row["net_balance_cents"]) for row in rows]
                == [(0, 1500, -1500), (4000, 0, 4000)],
            )
            check(
                "v1 remains an exact compatibility projection",
                first["rows"]["invoice_balances"] == python_oracle(baseline, cutoff)["invoice_balances"],
            )

            changed = copy.deepcopy(baseline)
            changed["refunds"].append(
                {**baseline["refunds"][0], "revision": 2, "recorded_at": "2026-04-03T00:00:00Z", "amount_cents": 1000}
            )
            changed["payments"].append(
                {
                    "record_id": "late",
                    "revision": 1,
                    "recorded_at": "2026-01-02T00:00:00Z",
                    "invoice_id": "i2",
                    "paid_date": "2026-01-02",
                    "amount_cents": 1500,
                }
            )
            check("late backfill and correction append two identities", warehouse.append(changed) == 2)
            new_cutoff = "2026-04-04T00:00:00Z"
            runs.append(warehouse.run("migration_late_backfill", new_cutoff))
            check("candidate dbt changes cannot alter published close", canonical(publisher.consume()).encode() == first_bytes)
            with warehouse.connection() as con:
                incremental = read_contracts(con, warehouse.schema)
            check("incremental backfill equals independent oracle", incremental == expected_rows(changed, new_cutoff))
            # Corrupt a candidate value rather than changing an expectation to make a bad migration pass.
            with warehouse.connection() as con:
                con.execute(f"CREATE TABLE {warehouse.schema}.bad_v2 AS SELECT * FROM {warehouse.schema}.invoice_balances_v2")
                con.execute(f"UPDATE {warehouse.schema}.bad_v2 SET gross_unpaid_cents=gross_unpaid_cents+1")
                con.execute(f"CREATE OR REPLACE VIEW {warehouse.schema}.invoice_balances_v2 AS SELECT * FROM {warehouse.schema}.bad_v2")
            try:
                publisher.stage("bad", changed, new_cutoff)
                raise AssertionError("Invalid candidate was accepted")
            except ValueError as error:
                check(
                    "invalid migration preserves all published rows",
                    "oracle" in str(error)
                    and canonical(publisher.consume()).encode() == first_bytes
                    and publisher.view_rows() == first["rows"],
                )

            runs.append(warehouse.run("migration_full_refresh", new_cutoff, full_refresh=True))
            replacement = publisher.stage("close_v2", changed, new_cutoff)
            releases.append(replacement)
            check("late backfill full refresh equals independent oracle", replacement["rows"] == expected_rows(changed, new_cutoff))
            check("incremental and full refresh match row for row", replacement["rows"] == incremental)
            try:
                publisher.promote("close_v2", fail_after_view=2)
                raise AssertionError("Publication fault was not triggered")
            except RuntimeError as error:
                check(
                    "interrupted promotion rolls back every view and pointer",
                    "Injected" in str(error)
                    and canonical(publisher.consume()).encode() == first_bytes
                    and publisher.view_rows() == first["rows"],
                )
            pinned = publisher.consume(_after_pin=lambda: publisher.promote("close_v2"))
            check("reader pinned before concurrent promotion retains original release", canonical(pinned).encode() == first_bytes)
            second = publisher.consume()
            check(
                "both consumers switch to the validated release",
                second["rows"] == replacement["rows"] and second["release_id"] == "close_v2" and publisher.view_rows() == second["rows"],
            )
            check(
                "corrected hand calculated exposure",
                [
                    (row["gross_unpaid_cents"], row["credit_cents"], row["net_balance_cents"])
                    for row in second["rows"]["invoice_balances_v2"]
                ]
                == [(0, 1000, -1000), (2500, 0, 2500)],
            )
            publisher.promote("close_v1")
            check(
                "rollback restores byte identical close",
                canonical(publisher.consume()).encode() == first_bytes and publisher.view_rows() == first["rows"],
            )

            frozen, original_cutoff = publisher.frozen_source("close_v1")
            fresh_directory = Path(temporary) / "rebuild"
            fresh_directory.mkdir()
            fresh = Warehouse(adapter, fresh_directory)
            try:
                fresh.append(frozen)
                runs.append(fresh.run("reproduce_frozen_close", original_cutoff, full_refresh=True))
                with fresh.connection() as con:
                    rebuilt = read_contracts(con, fresh.schema)
                check("fresh rebuild reproduces frozen close bytes", canonical(rebuilt).encode() == canonical(first["rows"]).encode())
            finally:
                fresh.close()
        finally:
            publisher.close()
            warehouse.close()
    files = [
        Path("mart/schema_migration.py"),
        Path("mart/dbt_warehouse.py"),
        Path("mart/subscriptions.py"),
        Path("requirements-dbt.txt"),
        Path("tests/test_schema_migration.py"),
        Path(".github/workflows/dbt-warehouse.yml"),
    ]
    files += [
        path.relative_to(ROOT)
        for path in PROJECT.rglob("*")
        if path.is_file() and path.suffix in {".yml", ".sql"} and path.name != ".user.yml"
    ]
    result = {
        "schema_version": 1,
        "adapter": adapter,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Synthetic local/disposable database exercise; no Databricks or production execution",
        "execution_environment": "GitHub-hosted Linux" if os.getenv("GITHUB_ACTIONS") else platform.platform(),
        "source_commit": os.getenv("GITHUB_SHA", "uncommitted-local"),
        "ci_run_id": os.getenv("GITHUB_RUN_ID"),
        "source_files": {str(path).replace("\\", "/"): sha((ROOT / path).read_bytes()) for path in files},
        "checks": checks,
        "runs": runs,
        "releases": releases,
        "limits": [
            "Single writer; no concurrent promotion protocol or production grants.",
            "Multi-query consumers pin a release ID/schema; repeatable read alone does not pin view definitions.",
            "Snapshots are workflow-frozen and rechecked before promotion, not protected from a database administrator.",
            "Fault injection proves transaction rollback after observed DDL, not process-kill or power-loss recovery.",
            "Original close reproduction uses its frozen source, not only a knowledge cutoff after late deliveries.",
        ],
    }
    with tempfile.TemporaryDirectory(prefix=".migration-", dir=output.parent) as staging:
        target = Path(staging)
        (target / "evidence.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        (target / "baseline-source.json").write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
        (target / "changed-source.json").write_text(json.dumps(changed, indent=2) + "\n", encoding="utf-8")
        target.rename(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", choices=["duckdb", "postgres"], default="duckdb")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = exercise(args.adapter, args.output)
    print(json.dumps({"adapter": args.adapter, "checks_passed": len(report["checks"]), "builds": len(report["runs"])}))


if __name__ == "__main__":
    main()
