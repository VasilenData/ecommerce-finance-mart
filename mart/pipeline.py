"""Run the SQL layers in order against DuckDB."""
from __future__ import annotations

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
LAYERS = ["10_staging.sql", "20_classify.sql", "30_core.sql", "40_marts.sql"]


def build(raw_csv: str | Path, db_path: str | Path = ":memory:") -> duckdb.DuckDBPyConnection:
    """Build every table from the raw CSV and return the open connection."""
    con = duckdb.connect(str(db_path))
    subs = {"{{RAW}}": str(Path(raw_csv).resolve()), "{{SEEDS}}": str((ROOT / "seeds").resolve())}
    for name in LAYERS:
        sql = (ROOT / "sql" / name).read_text()
        for key, value in subs.items():
            sql = sql.replace(key, value)
        con.execute(sql)
    return con
