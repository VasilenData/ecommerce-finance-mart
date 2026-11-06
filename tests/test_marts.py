"""Hand-computed expectations for the finance and customer marts."""
import pytest

from mart.checks import assert_blocking, run_checks


def test_blocking_checks_pass(con):
    assert_blocking(run_checks(con))


def test_blocking_check_catches_tampering(con):
    con.execute("create or replace table fact_lines_backup as select * from fact_lines")
    try:
        con.execute("delete from fact_lines where line_id = 1")
        with pytest.raises(AssertionError, match="row_reconciliation"):
            assert_blocking(run_checks(con))
    finally:
        con.execute("create or replace table fact_lines as select * from fact_lines_backup")
        con.execute("drop table fact_lines_backup")


def test_monthly_finance(con):
    rows = {str(r[0]): r for r in con.execute(
        "select month, gross_sales, returns, shipping, orders, active_customers, new_customers, guest_share, "
        "quick_reversals, return_rate_ex_reversals "
        "from mart_monthly").fetchall()}
    jan, feb, mar = rows["2011-01-01"], rows["2011-02-01"], rows["2011-03-01"]
    assert (float(jan[1]), jan[4], jan[5]) == (11.0, 1, 1)
    assert jan[6] is None  # first data month is left-censored
    assert (float(feb[1]), float(feb[2]), float(feb[3]), feb[4], feb[6]) == (15.0, -3.0, 10.0, 1, 1)
    assert (float(mar[1]), float(mar[2]), mar[4], mar[5], mar[6]) == (37.0, -28.0, 4, 2, 1)
    assert float(mar[7]) == pytest.approx(6 / 37)
    assert float(mar[8]) == -20.0  # the 30-minute mirror order
    # real returns 8 over sales left after removing the reversed order: 37 - 20 = 17
    assert float(mar[9]) == pytest.approx(8 / 17)


def test_net_revenue_and_aov(con):
    net, aov = con.execute(
        "select sum(net_product_revenue), sum(gross_sales) / sum(orders) from mart_monthly").fetchone()
    assert float(net) == 32.0
    assert float(aov) == pytest.approx(63 / 6)


def test_rfm(con):
    rows = {r[0]: r for r in con.execute(
        "select customer_id, recency_days, frequency, monetary from mart_customer_rfm").fetchall()}
    assert (rows[1001][1], rows[1001][2], float(rows[1001][3])) == (4, 2, 14.0)
    assert (rows[1002][1], rows[1002][2], float(rows[1002][3])) == (31, 1, 12.0)
    assert (rows[1003][1], rows[1003][2], float(rows[1003][3])) == (3, 2, 0.0)
    assert set(rows) == {1001, 1002, 1003}  # guests have no customer-level metrics
    seg = dict(con.execute("select customer_id, segment from mart_customer_rfm").fetchall())
    assert seg[1003] == "Net negative"  # bought and sent everything back


def test_cohort_retention(con):
    got = {(str(r[0]), r[1]): (r[2], r[3], r[4]) for r in con.execute(
        "select cohort_month, month_index, cohort_size, active_customers, retention from mart_cohort").fetchall()}
    assert got[("2011-01-01", 0)] == (1, 1, 1.0)
    assert got[("2011-01-01", 2)] == (1, 1, 1.0)
    assert got[("2011-02-01", 0)] == (1, 1, 1.0)


def test_product_mart(con):
    row = con.execute(
        "select sales_revenue, units_sold, units_returned, return_rate_units, abc_class "
        "from mart_product where stock_code = '10001'").fetchone()
    assert (float(row[0]), row[1], row[2], row[3]) == (24.0, 8, 1, 0.125)
