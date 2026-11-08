"""Run the SQL layers in order against DuckDB."""
from __future__ import annotations

from pathlib import Path

import duckdb

from mart.checks import assert_blocking, run_checks

ROOT = Path(__file__).resolve().parent.parent
LAYERS = ["10_staging.sql", "20_classify.sql", "30_core.sql", "40_marts.sql"]


def build(raw_csv: str | Path, db_path: str | Path = ":memory:") -> duckdb.DuckDBPyConnection:
    """Build and validate all tables atomically; preserve an existing mart on failure."""
    con = duckdb.connect(str(db_path))
    subs = {"{{RAW}}": str(Path(raw_csv).resolve()), "{{SEEDS}}": str((ROOT / "seeds").resolve())}
    con.execute("BEGIN TRANSACTION")
    try:
        for name in LAYERS:
            sql = (ROOT / "sql" / name).read_text(encoding="utf-8")
            for key, value in subs.items():
                sql = sql.replace(key, value.replace("'", "''"))
            con.execute(sql)
        assert_blocking(run_checks(con))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        con.close()
        raise
    return con
