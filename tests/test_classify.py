"""Every raw line lands in exactly one class, for the right reason."""
import pytest


def verdicts(con):
    return dict(con.execute("select line_id, verdict from line_classified").fetchall())


@pytest.mark.parametrize("line_id,expected", [
    (1, "keep:sale"), (2, "keep:sale"), (3, "reject:exact_duplicate"), (4, "keep:sale"),
    (5, "keep:return"), (6, "keep:shipping"), (7, "keep:sale"), (8, "reject:non_positive_price"),
    (9, "reject:test_product"), (10, "keep:bad_debt"), (11, "keep:discount"),
    (12, "reject:non_positive_quantity"), (13, "reject:credit_note_positive_quantity"),
    (14, "reject:missing_key_field"), (15, "keep:sale"), (16, "reject:zero_price_non_product"),
    (17, "keep:fee"), (18, "keep:sale"), (19, "keep:return"), (20, "keep:sale"), (21, "keep:return"),
])
def test_line_verdict(con, line_id, expected):
    assert verdicts(con)[line_id] == expected


def test_no_row_lost(con):
    kept, rejected, raw = con.execute(
        "select (select count(*) from fact_lines), (select count(*) from quarantine_lines), (select count(*) from raw_lines)"
    ).fetchone()
    assert kept + rejected == raw == 21


def test_normalization(con):
    codes = {r[0] for r in con.execute("select stock_code from fact_lines").fetchall()}
    assert "10002A" in codes and "10002a" not in codes
    assert con.execute("select description from dim_product where stock_code = '10001'").fetchone()[0] == "Red Mug"
    assert con.execute("select distinct country from fact_lines where customer_id = 1002").fetchall() == [("Ireland",)]
    assert con.execute("select distinct region from fact_lines where customer_id = 1002").fetchall() == [("Europe (non-UK)",)]


def test_quick_reversal_flag(con):
    flagged = dict(con.execute("select line_id, is_quick_reversal from fact_lines where line_type = 'return'").fetchall())
    assert flagged[19] is True   # mirrors a sale by the same customer 30 minutes earlier
    assert flagged[21] is False  # same product returned two days later is a real return
    assert flagged[5] is False   # return with no matching sale
