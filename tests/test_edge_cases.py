import json
import re

import duckdb
import pytest

from mart.checks import assert_blocking, run_checks
from mart.explorer import build_explorer
from mart.pipeline import build

HEADER = "Invoice,StockCode,Description,Quantity,InvoiceDate,Price,Customer ID,Country"
ROWS = [
    "100001,10001,Mug,2,2011-01-01 10:00:00,3,1001,United Kingdom",
    "C100002,10001,Mug,-1,2011-02-01 10:00:00,3,1001,United Kingdom",
    "100003,10001,Mug,1,2011-03-01 10:00:00,3,1001,United Kingdom",
]


def write_fixture(path, rows=ROWS):
    path.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_returns_only_month_reconciles_and_has_undefined_ratios(tmp_path):
    with build(write_fixture(tmp_path / "raw.csv")) as con:
        row = con.execute("select gross_sales, returns, net_product_revenue, orders, avg_order_value, "
                          "return_rate, return_rate_ex_reversals, guest_share from mart_monthly "
                          "where month = '2011-02-01'").fetchone()
        assert tuple(float(v) for v in row[:3]) == (0, -3, -3)
        assert row[3:] == (0, None, None, None, None)
        assert_blocking(run_checks(con))


def test_cohort_contains_observed_zero_activity_months_but_no_future_months(tmp_path):
    with build(write_fixture(tmp_path / "raw.csv")) as con:
        rows = con.execute("select month_index, active_customers, retention from mart_cohort order by month_index").fetchall()
        assert rows == [(0, 1, 1.0), (1, 0, 0.0), (2, 1, 1.0)]


def test_fully_reversed_month_has_no_infinite_adjusted_rate(tmp_path):
    rows = [ROWS[0], "C100002,10001,Mug,-2,2011-01-01 10:15:00,3,1001,United Kingdom"]
    with build(write_fixture(tmp_path / "raw.csv", rows)) as con:
        assert con.execute("select return_rate_ex_reversals from mart_monthly").fetchone()[0] is None


def test_failed_rebuild_preserves_previous_database_and_handles_quote_in_path(tmp_path):
    raw = write_fixture(tmp_path / "retail's.csv")
    database = tmp_path / "mart.duckdb"
    with build(raw, database) as con:
        before = con.execute("select count(*), sum(amount) from fact_lines").fetchone()
    broken = tmp_path / "broken.csv"
    broken.write_text("unexpected\nvalue\n", encoding="utf-8")
    with pytest.raises(duckdb.Error):
        build(broken, database)
    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute("select count(*), sum(amount) from fact_lines").fetchone() == before
        assert_blocking(run_checks(con))


def test_empty_monthly_mart_cannot_pass_reconciliation(tmp_path):
    with build(write_fixture(tmp_path / "raw.csv")) as con:
        con.execute("delete from mart_monthly")
        with pytest.raises(AssertionError, match="monthly_reconciles"):
            assert_blocking(run_checks(con))


def test_explorer_embedded_json_cannot_close_its_script_element(tmp_path):
    with build(write_fixture(tmp_path / "raw.csv")) as con:
        dangerous = "</script><script>alert('fixture')</script>"
        con.execute("update mart_explorer_product set description = ?", [dangerous])
        build_explorer(con, tmp_path / "explorer")
    html = (tmp_path / "explorer/index.html").read_text(encoding="utf-8")
    embedded = re.search(r'<script id="cube" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert "</script>" not in embedded
    assert json.loads(embedded)["prod"][0][2] == dangerous
