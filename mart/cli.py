"""Command line: python -m mart.cli data | build | excel | explorer | charts | all"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mart.charts import draw_all
from mart.checks import assert_blocking, run_checks
from mart.excel import build_workbook, recalculate
from mart.explorer import build_explorer
from mart.ingest import fetch
from mart.pipeline import build
from mart.report import write_report

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw_online_retail_II.csv"
DB = ROOT / "data" / "mart.duckdb"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mart")
    ap.add_argument("step", choices=["data", "build", "excel", "explorer", "charts", "all"])
    step = ap.parse_args(argv).step
    if step == "data":
        print("raw csv:", fetch(ROOT / "data"))
        return 0
    if not RAW.exists():
        print("Raw data missing. Run `python -m mart.cli data` first.", file=sys.stderr)
        return 2
    con = build(RAW, DB)
    results = run_checks(con)
    for r in results:
        print(("PASS " if r["passed"] else ("INFO " if not r["blocking"] else "FAIL ")) + r["name"])
    assert_blocking(results)
    write_report(con, ROOT / "docs" / "data_quality.md")
    if step in ("excel", "all"):
        build_workbook(con, ROOT / "docs" / "finance_workbook.xlsx")
        print("workbook recalculated:", recalculate(ROOT / "docs" / "finance_workbook.xlsx"))
    if step in ("explorer", "all"):
        print(build_explorer(con, ROOT / "docs" / "explorer"))
    if step in ("charts", "all"):
        print(draw_all(con, ROOT / "docs" / "img"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
