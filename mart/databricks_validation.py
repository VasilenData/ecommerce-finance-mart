"""Build a self-contained Databricks validation notebook; never submit cloud work."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import inspect
import json
import math
import platform
import subprocess
import tempfile
import zlib
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from mart.pipeline import ROOT, build

HEADER = ["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"]
RAW_COLUMNS = ["line_id", *[name.replace(" ", "_") for name in HEADER]]
TABLES = [
    "stg_lines", "line_classified", "quarantine_lines", "fact_lines_base", "dim_customer", "fact_lines",
    "dim_product", "dim_date", "fact_invoice", "mart_monthly", "mart_customer_rfm", "mart_cohort",
    "mart_product", "mart_country", "mart_explorer", "mart_explorer_product", "as_of",
]
FULL_ROWS = [name for name in TABLES if name.startswith("mart_") or name.startswith("dim_") or name == "as_of"]
FIXTURES = {
    "returns_only_and_cohort": [
        ["100001", "10001", "Mug", "2", "2011-01-01 10:00:00", "3", "1001", "United Kingdom"],
        ["C100002", "10001", "Mug", "-1", "2011-02-01 10:00:00", "3", "1001", "United Kingdom"],
        ["100003", "10001", "Mug", "1", "2011-03-01 10:00:00", "3", "1001", "United Kingdom"],
    ],
    "fully_reversed": [
        ["100001", "10001", "Mug", "2", "2011-01-01 10:00:00", "3", "1001", "United Kingdom"],
        ["C100002", "10001", "Mug", "-2", "2011-01-01 10:15:00", "3", "1001", "United Kingdom"],
    ],
}
SUMMARY_SQL = """select 'gross_sales' as metric, coalesce(sum(amount), 0) as value from fact_lines where line_type='sale'
union all select 'returns', coalesce(sum(amount), 0) from fact_lines where line_type='return'
union all select 'quick_reversals', coalesce(sum(amount), 0) from fact_lines where is_quick_reversal
union all select 'kept_amount', coalesce(sum(amount), 0) from fact_lines
union all select 'quarantined_amount', coalesce(sum(amount), 0) from quarantine_lines"""


def split_sql(text):
    """Split this port's SQL without breaking quoted semicolons or comments."""
    statements, chunk, quote, comment, i = [], [], None, None, 0
    while i < len(text):
        char, following = text[i], text[i:i + 2]
        if comment == "line":
            if char == "\n":
                comment = None
                chunk.append(char)
        elif comment == "block":
            if following == "*/":
                comment = None
                i += 1
                chunk.append(" ")
        elif quote:
            chunk.append(char)
            if char == "\\" and i + 1 < len(text):
                i += 1
                chunk.append(text[i])
            elif char == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 1
                    chunk.append(text[i])
                else:
                    quote = None
        elif following in ("--", "/*"):
            comment = "line" if following == "--" else "block"
            i += 1
            chunk.append(" ")
        elif char in "'\"`":
            quote = char
            chunk.append(char)
        elif char == ";":
            if "".join(chunk).strip():
                statements.append("".join(chunk).strip())
            chunk = []
        else:
            chunk.append(char)
        i += 1
    if quote or comment == "block":
        raise ValueError("Unterminated SQL quote/comment")
    if "".join(chunk).strip():
        statements.append("".join(chunk).strip())
    return statements


def scalar(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, (float, Decimal)):
        if not math.isfinite(float(value)):
            raise ValueError("Non-finite result")
        return str(value)
    raise TypeError(type(value).__name__)


def signature_sql(engine):
    columns = ['cast(line_id as string)' if engine == "spark" else 'cast(line_id as varchar)']
    columns += [f'`{name}`' if engine == "spark" else f'"{name.replace("_", " ")}"' for name in RAW_COLUMNS[1:]]
    if engine == "spark":
        digest = "sha2(to_json(array(" + ",".join(columns) + ")),256)"
        chunks = [f"sum(cast(conv(substr(h,{1 + i * 8},8),16,10) as decimal(38,0))) as h{i}" for i in range(8)]
    else:
        digest = "sha256(cast(json_array(" + ",".join(columns) + ") as varchar))"
        chunks = [f"sum(cast('0x' || substr(h,{1 + i * 8},8) as bigint)) as h{i}" for i in range(8)]
    return ("select count(*) as rows, count(distinct line_id) as distinct_ids, min(line_id) as min_id, "
            "max(line_id) as max_id, " + ",".join(chunks) + " from (select line_id," + digest + " as h from raw_lines)")


def snapshot(con, full=False):
    counts = {name: con.execute(f"select count(*) from {name}").fetchone()[0] for name in TABLES}
    rows = {}
    for name in (FULL_ROWS if full else TABLES):
        result = con.execute(f"select * from {name}")
        rows[name] = {
            "columns": [c[0] for c in result.description],
            "types": [str(c[1]) for c in result.description],
            "rows": [[scalar(value) for value in row] for row in result.fetchall()],
        }
    return {
        "counts": counts,
        "rows": rows,
        "amounts": {name: scalar(value) for name, value in con.execute(SUMMARY_SQL).fetchall()},
        "verdicts": dict(con.execute("select verdict,count(*) from line_classified group by verdict").fetchall()),
        "raw_signature": [scalar(value) for value in con.execute(signature_sql("duckdb")).fetchone()],
    }


def compare_rows(expected, actual):
    """Multiset comparison: exact decimals/integers/nulls, bounded floating tolerance."""
    types = expected["types"]

    def normalized(row):
        values = []
        for kind, value in zip(types, row, strict=True):
            if value is None:
                values.append((0, ""))
            elif kind in ("FLOAT", "DOUBLE", "REAL"):
                values.append((1, float(value)))
            elif kind.startswith("DECIMAL") or "INT" in kind or kind == "HUGEINT":
                values.append((1, Decimal(str(value))))
            else:
                values.append((1, str(value)))
        return values

    left, right = sorted(map(normalized, expected["rows"])), sorted(map(normalized, actual))
    if len(left) != len(right):
        return False
    for old, new in zip(left, right, strict=True):
        for kind, a, b in zip(types, old, new, strict=True):
            if a[0] != b[0]:
                return False
            if a[0] and kind in ("FLOAT", "DOUBLE", "REAL"):
                if not math.isclose(a[1], b[1], rel_tol=1e-12, abs_tol=1e-9):
                    return False
            elif a != b:
                return False
    return True


def check_fixture_oracles(name, con):
    if name == "returns_only_and_cohort":
        row = con.execute("select gross_sales,returns,net_product_revenue,orders,avg_order_value,return_rate,"
                          "return_rate_ex_reversals,guest_share from mart_monthly where month='2011-02-01'").fetchone()
        assert row == (0, -3, -3, 0, None, None, None, None)
        cohort = con.execute("select month_index,active_customers,retention,revenue from mart_cohort order by month_index").fetchall()
        assert cohort == [(0, 1, 1, 6), (1, 0, 0, 0), (2, 1, 1, 3)]
    else:
        assert con.execute("select gross_sales,returns,quick_reversals,net_product_revenue,return_rate_ex_reversals "
                           "from mart_monthly").fetchone() == (6, -6, -6, 0, None)


def make_reference(raw_csv):
    sources = [ROOT / "databricks/01_build_core.sql", ROOT / "databricks/02_build_marts.sql"]
    sql_texts = {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8") for p in sources}
    with tempfile.TemporaryDirectory(prefix="finance-reference-") as temp:
        scenarios = {}
        for name, rows in FIXTURES.items():
            path = Path(temp) / f"{name}.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(HEADER)
                writer.writerows(rows)
            with build(path) as con:
                check_fixture_oracles(name, con)
                scenarios[name] = snapshot(con)
        with build(raw_csv) as con:
            scenarios["full"] = snapshot(con, full=True)
    code = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_text(encoding="utf-8").encode()).hexdigest()
            for p in [*sources, *sorted((ROOT / "sql").glob("[1234]*.sql")), *sorted((ROOT / "seeds").glob("*.csv"))]}
    return {
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
        "hosted_status": "NOT_RUN",
        "reference_runtime": {"duckdb": duckdb.__version__, "python": platform.python_version()},
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": hashlib.sha256(Path(raw_csv).read_bytes()).hexdigest(),
        "code_lf_sha256": code,
        "port_sql": sql_texts,
        "fixture_rows": FIXTURES,
        "scenarios": scenarios,
    }


def runtime_body():
    # Executed only by the user in an existing Databricks notebook session.
    spark.sql("SET TIME ZONE 'UTC'")  # noqa: F821
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]  # noqa: F821
    evidence = {
        "run_id": run_id, "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "engine": "Databricks Spark", "spark_version": spark.version,  # noqa: F821
        "reference_base_commit": payload["base_commit"], "code_lf_sha256": payload["code_lf_sha256"],  # noqa: F821
        "reference_source_sha256": payload["source_sha256"], "checks": [], "schemas": [], "scenarios": {},  # noqa: F821
        "scope": "Synthetic raw fixtures plus historical public retail data. No production or performance claim.",
    }

    def record(name, passed, observed=None):
        evidence["checks"].append({"name": name, "passed": bool(passed), "observed": observed})
        if not passed:
            raise AssertionError(name)

    try:
        for name, reference in payload["scenarios"].items():  # noqa: F821
            schema = "workspace.finance_verify_" + run_id + "_" + name
            spark.sql("CREATE SCHEMA " + schema)  # noqa: F821
            evidence["schemas"].append(schema)
            spark.sql("USE SCHEMA " + schema)  # noqa: F821
            if name == "full":
                # One CTAS freezes the existing uploaded source; nothing overwrites it.
                spark.sql("CREATE TABLE raw_lines AS SELECT * FROM workspace.finance_mart.raw_lines")  # noqa: F821
            else:
                definitions = "line_id LONG," + ",".join(f"`{col}` STRING" for col in RAW_COLUMNS[1:])
                rows = [(i + 1, *row) for i, row in enumerate(payload["fixture_rows"][name])]  # noqa: F821
                spark.createDataFrame(rows, definitions).write.mode("errorifexists").saveAsTable(schema + ".raw_lines")  # noqa: F821
            actual_signature = [scalar(x) for x in spark.sql(signature_sql("spark")).first()]  # noqa: F821
            # Numeric JSON representation may use decimal scale; compare numerical values.
            record(name + ":frozen_raw_signature", all(
                Decimal(str(a)) == Decimal(str(b)) for a, b in zip(actual_signature, reference["raw_signature"], strict=True)
            ), actual_signature)
            for file, sql in payload["port_sql"].items():  # noqa: F821
                record(name + ":source:" + file,
                       hashlib.sha256(sql.encode()).hexdigest() == payload["code_lf_sha256"][file])  # noqa: F821
                statements = split_sql(sql)
                for statement in statements:
                    spark.sql(statement).collect()  # noqa: F821
                evidence["scenarios"].setdefault(name, {})[file] = len(statements)
            for table, count in reference["counts"].items():
                observed = spark.sql("SELECT count(*) FROM " + table).first()[0]  # noqa: F821
                record(name + ":count:" + table, observed == count, observed)
            amounts = {r[0]: scalar(r[1]) for r in spark.sql(SUMMARY_SQL).collect()}  # noqa: F821
            record(name + ":exact_money", all(Decimal(amounts[k]) == Decimal(v) for k, v in reference["amounts"].items()), amounts)
            verdicts = {r[0]: r[1] for r in spark.sql("SELECT verdict,count(*) FROM line_classified GROUP BY verdict").collect()}  # noqa: F821
            record(name + ":classification", verdicts == reference["verdicts"], verdicts)
            for table, expected in reference["rows"].items():
                frame = spark.table(table).select(*expected["columns"])  # noqa: F821
                actual = [[scalar(value) for value in row] for row in frame.collect()]
                record(name + ":rows:" + table, compare_rows(expected, actual), {"rows": len(actual), "columns": expected["columns"]})
        evidence["status"] = "PASS"
    except Exception as exc:
        evidence["status"] = "FAIL"
        evidence["error"] = str(exc)[:2000]
        raise
    finally:
        result = json.dumps(evidence, indent=2)
        print(result)
        encoded = base64.b64encode(result.encode()).decode()
        displayHTML(  # noqa: F821
            '<h2>Finance validation: ' + evidence["status"] + '</h2><p>Download the actual executed evidence.</p>'
            '<a download="finance-databricks-' + run_id + '.json" href="data:application/json;base64,' + encoded
            + '">Download validation JSON</a><p>The isolated schemas are retained for inspection. '
            'No source table or historical report was overwritten.</p>'
        )


def write_bundle(raw_csv, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    payload = make_reference(raw_csv)
    reference = json.dumps(payload, separators=(",", ":"))
    (output / "duckdb-reference.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    encoded = base64.b64encode(zlib.compress(reference.encode(), 9)).decode()
    encoded_literal = "\n".join("    " + repr(encoded[i:i + 100]) for i in range(0, len(encoded), 100))
    sql_literal = "\n".join("    " + repr(line + "\n") for line in SUMMARY_SQL.splitlines())
    setup = (
        "import base64\nimport hashlib\nimport json\nimport math\nimport uuid\nimport zlib\n"
        "from datetime import date, datetime, timezone\nfrom decimal import Decimal\n\n"
        "encoded_reference = (\n" + encoded_literal + "\n)\n"
        "payload = json.loads(zlib.decompress(base64.b64decode(encoded_reference)))\n"
        + "RAW_COLUMNS = " + repr(RAW_COLUMNS) + "\nSUMMARY_SQL = (\n" + sql_literal + "\n)\n"
        + "\n\n".join(inspect.getsource(function) for function in [split_sql, scalar, signature_sql, compare_rows, runtime_body])
    )
    intro = (
        "# Finance Mart — execute current Databricks parity checks\n\n"
        "Prepared locally; **Databricks execution is NOT_RUN until this notebook completes**. "
        "Use only the existing Free Edition serverless compute; no paid upgrade is needed or authorized.\n\n"
        "Run all cells. This creates three uniquely named `workspace.finance_verify_*` schemas, builds the current "
        "SQL port over two raw edge-case fixtures and a snapshot of `workspace.finance_mart.raw_lines`, and retains "
        "the result tables. It does not update or delete the original source or historical mart. "
        "Full-data row comparisons cover dimensions and marts; large staging/fact relations are checked by counts, "
        "exact financial totals and verdict groups. Source identity is checked by eight commutative SHA-256 chunk sums "
        "plus count/IDs, not a byte-for-byte cloud CSV comparison. Decimal money is exact; floating ratios allow "
        "absolute tolerance 1e-9 and relative tolerance 1e-12.\n\n"
        "The DuckDB reference first passed independent hand-calculated return/cohort oracles. "
        "Download the final JSON and return it for publication. A local reference or parsed notebook does not prove hosted parity."
    )
    def cell(kind, text, identity):
        result = {"cell_type": kind, "metadata": {}, "id": identity, "source": text.splitlines(keepends=True)}
        if kind == "code":
            result.update(execution_count=None, outputs=[])
        return result
    notebook = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
        "cells": [cell("markdown", intro, "scope"), cell("code", setup, "setup"), cell("code", "runtime_body()\n", "execute")],
    }
    (output / "Finance_Mart_Revalidation.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
    (output / "README.txt").write_text(
        "1. In the existing Databricks Free Edition workspace, import Finance_Mart_Revalidation.ipynb.\n"
        "2. Select existing Serverless compute and Run all. Do not create paid compute or upgrade.\n"
        "3. The notebook creates unique workspace.finance_verify_* schemas; existing finance_mart remains unchanged.\n"
        "4. Download validation JSON from the final cell and return that file. FAIL must not be described as parity.\n"
        "5. Schemas remain for inspection; delete only when no longer needed, using your normal account controls.\n",
        encoding="utf-8",
    )
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = write_bundle(args.raw, args.output)
    print(json.dumps({"output": str(args.output), "hosted_status": "NOT_RUN", "source_sha256": payload["source_sha256"]}))


if __name__ == "__main__":
    main()
