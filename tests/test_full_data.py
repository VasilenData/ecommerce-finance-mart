"""Optional end-to-end test on the real UCI file. Skipped when the data has not been downloaded."""
from pathlib import Path

import pytest

from mart.checks import assert_blocking, run_checks
from mart.pipeline import build

RAW = Path(__file__).resolve().parent.parent / "data" / "raw_online_retail_II.csv"
pytestmark = pytest.mark.skipif(not RAW.exists(), reason="run `make data` first")


def test_real_data_reconciles():
    con = build(RAW)
    assert_blocking(run_checks(con))
    assert con.execute("select count(*) from raw_lines").fetchone()[0] == 1_067_371
    assert con.execute("select count(*) from quarantine_lines where reject_reason = 'exact_duplicate'").fetchone()[0] == 34_335
