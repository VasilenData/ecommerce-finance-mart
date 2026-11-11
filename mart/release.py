"""Build a complete validated artifact bundle without changing checked-in docs.

Run python -m mart.release --raw data/raw_online_retail_II.csv --output dist/release.
LibreOffice is required: uncalculated workbook formulas cannot become a release.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from openpyxl import load_workbook

from mart.charts import draw_all
from mart.checks import assert_blocking, run_checks
from mart.decisions import write_decision_report
from mart.excel import build_workbook, recalculate
from mart.explorer import build_explorer
from mart.pipeline import build
from mart.provenance import code_records, freeze_source, sha256
from mart.report import write_report

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ("finance_workbook.xlsx", "explorer/index.html", "data_quality.md",
             "img/revenue.png", "img/return_rate.png", "img/cohorts.png")


def verify_value(actual, expected, label, tolerance=1e-8):
    if expected is None:
        if actual not in (None, ""):
            raise ValueError(f"{label}: expected a blank undefined ratio, got {actual!r}")
    elif (isinstance(actual, bool) or not isinstance(actual, (int, float))
          or not math.isfinite(actual)
          or not math.isclose(actual, float(expected), rel_tol=1e-10, abs_tol=tolerance)):
        raise ValueError(f"{label}: cached workbook value {actual!r} differs from SQL {expected!r}")


def validate_workbook(con, path: Path) -> dict:
    """Compare recalculated cells to SQL outputs; reject any cached formula error."""
    formulas = load_workbook(path, data_only=False, read_only=True)
    cached = load_workbook(path, data_only=True, read_only=True)
    checked = 0
    try:
        for sheet in cached:
            for row in sheet:
                for cell in row:
                    if cell.data_type == "e":
                        raise ValueError(f"Workbook formula error: {sheet.title}!{cell.coordinate}={cell.value}")
        monthly = con.execute(
            "select net_product_revenue, avg_order_value, return_rate, return_rate_ex_reversals "
            "from mart_monthly order by month"
        ).fetchall()
        for row_number, values in enumerate(monthly, 2):
            for column, expected in zip(("D", "H", "I", "J"), values, strict=True):
                address = f"{column}{row_number}"
                if formulas["Monthly"][address].data_type != "f":
                    raise ValueError(f"Monthly!{address}: expected a live formula")
                verify_value(cached["Monthly"][address].value, expected, f"Monthly!{address}")
                checked += 1
        totals = con.execute("select sum(gross_sales), sum(returns), sum(net_product_revenue), "
                             "sum(orders), sum(gross_sales)/nullif(sum(orders),0), "
                             "-sum(returns)/nullif(sum(gross_sales),0), sum(quick_reversals), "
                             "-(sum(returns)-sum(quick_reversals))/"
                             "nullif(sum(gross_sales)+sum(quick_reversals),0) from mart_monthly").fetchone()
        for row_number, expected in enumerate(totals, 4):
            verify_value(cached["Summary"][f"B{row_number}"].value, expected, f"Summary!B{row_number}")
            checked += 1
        segments = {name: (count, revenue) for name, count, revenue in con.execute(
            "select segment, count(*), sum(monetary) from mart_customer_rfm group by segment").fetchall()}
        for row_number in range(2, 10):
            name = cached["Segments"][f"A{row_number}"].value
            for column, expected in zip(("B", "C"), segments.get(name, (0, 0)), strict=True):
                verify_value(cached["Segments"][f"{column}{row_number}"].value, expected,
                             f"Segments!{column}{row_number}")
                checked += 1
    finally:
        formulas.close()
        cached.close()
    return {"sql_compared_cells": checked, "formula_errors": 0}


def artifact_records(directory: Path, extra: tuple[str, ...] = ()) -> list[dict]:
    records = []
    for relative in (*ARTIFACTS, *extra):
        path = directory / relative
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing or empty release artifact: {relative}")
        records.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path)})
    return records


def release(raw: Path, output: Path, coverage_start: str | None = None, coverage_end: str | None = None) -> dict:
    raw, output = raw.resolve(), output.resolve()
    if (coverage_start is None) != (coverage_end is None):
        raise ValueError("Management reports require both complete source coverage bounds")
    if output.exists():
        raise FileExistsError(f"Release destination must be new: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    revision = os.environ.get("GITHUB_SHA") or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    with tempfile.TemporaryDirectory(prefix="finance-release-", dir=output.parent) as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir()
        frozen, source = freeze_source(raw, Path(temporary) / "source")
        code = code_records(ROOT)
        con = build(frozen, Path(temporary) / "mart.duckdb")
        try:
            checks = run_checks(con)
            assert_blocking(checks)
            write_report(con, staging / "data_quality.md")
            build_workbook(con, staging / "finance_workbook.xlsx")
            build_explorer(con, staging / "explorer")
            draw_all(con, staging / "img")
            if coverage_start is not None:
                write_decision_report(con, staging, coverage_start, coverage_end,
                                      {"source": source, "code_sha256_lf_normalized": code})
            if not recalculate(staging / "finance_workbook.xlsx"):
                raise RuntimeError("LibreOffice is required to validate cached formula results for a release")
            workbook = validate_workbook(con, staging / "finance_workbook.xlsx")
            row_counts = {table: con.execute(f"select count(*) from {table}").fetchone()[0]
                          for table in ("raw_lines", "fact_lines", "quarantine_lines")}
        finally:
            con.close()
        manifest = {"schema_version": 1, "revision": revision,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source": source, "code_sha256_lf_normalized": code,
                    "packages": {name: version(name) for name in ("duckdb", "pandas", "openpyxl", "matplotlib")},
                    "row_counts": row_counts, "checks": checks, "workbook_validation": workbook,
                    "artifacts": artifact_records(staging, ("management.md", "management.json") if coverage_start else ())}
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        staging.rename(output)  # failed generation never publishes a partial bundle
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=ROOT / "data/raw_online_retail_II.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/release")
    parser.add_argument("--coverage-start", help="Declared complete coverage start, inclusive, for management report")
    parser.add_argument("--coverage-end", help="Declared complete coverage end, exclusive, for management report")
    args = parser.parse_args()
    print(json.dumps(release(args.raw, args.output, args.coverage_start, args.coverage_end), indent=2))


if __name__ == "__main__":
    main()
