"""Data quality and reconciliation checks. Blocking checks fail the build."""
from __future__ import annotations

from dataclasses import dataclass

import duckdb


@dataclass
class Check:
    name: str
    description: str
    sql: str  # returns one number: count of violations (or absolute difference)
    blocking: bool = True


CHECKS = [
    Check("row_reconciliation", "Every raw row is either kept or quarantined, none lost or duplicated",
          "select abs((select count(*) from raw_lines) - (select count(*) from fact_lines) - (select count(*) from quarantine_lines))"),
    Check("amount_reconciliation", "Raw quantity x price equals kept amount plus quarantined amount (to the penny)",
          "select abs(coalesce((select sum(quantity * unit_price) from stg_lines), 0)"
          " - coalesce((select sum(amount) from fact_lines), 0) - coalesce((select sum(amount) from quarantine_lines), 0))"),
    Check("line_id_unique", "line_id is unique in the fact table",
          "select count(*) - count(distinct line_id) from fact_lines"),
    Check("sale_lines_positive", "Sale lines have positive quantity and price",
          "select count(*) from fact_lines where line_type = 'sale' and (quantity <= 0 or unit_price <= 0)"),
    Check("return_lines_negative", "Return lines are credit notes with negative quantity",
          "select count(*) from fact_lines where line_type = 'return' and (quantity >= 0 or invoice_no not like 'C%')"),
    Check("product_fk", "Every sale and return line has a product in dim_product",
          "select count(*) from fact_lines f left join dim_product p using (stock_code) "
          "where f.line_type in ('sale', 'return') and p.stock_code is null"),
    Check("customer_fk", "Every customer id on a fact line exists in dim_customer",
          "select count(*) from fact_lines f left join dim_customer c using (customer_id) "
          "where f.customer_id is not null and c.customer_id is null"),
    Check("stock_code_normalized", "Stock codes are trimmed and upper case",
          "select count(*) from fact_lines where stock_code <> upper(trim(stock_code))"),
    Check("one_description_per_product", "dim_product has one row per stock code",
          "select count(*) - count(distinct stock_code) from dim_product"),
    Check("invoice_totals", "fact_invoice net equals fact_lines net",
          "select abs((select sum(net_amount) from fact_invoice) - (select sum(amount) from fact_lines))"),
    Check("invoice_single_customer", "An invoice belongs to one customer",
          "select count(*) from (select invoice_no from fact_lines group by 1 having count(distinct customer_id) > 1)"),
    Check("monthly_reconciles", "mart_monthly net product revenue equals sale plus return lines",
          "select abs(coalesce((select sum(net_product_revenue) from mart_monthly), 0)"
          " - coalesce((select sum(amount) from fact_lines where line_type in ('sale', 'return')), 0))"),
    Check("cohort_month0_full", "Every cohort has 100% retention in month 0",
          "select count(*) from mart_cohort where month_index = 0 and abs(retention - 1) > 1e-9"),
    Check("rfm_one_row_per_customer", "mart_customer_rfm has one row per customer",
          "select count(*) - count(distinct customer_id) from mart_customer_rfm"),
    Check("invoice_has_one_timestamp", "Invoices with more than one distinct timestamp (informational)",
          "select count(*) from (select invoice_no from fact_lines group by 1 having count(distinct invoiced_at) > 1)", False),
]


def run_checks(con: duckdb.DuckDBPyConnection) -> list[dict]:
    results = []
    for c in CHECKS:
        value = con.execute(c.sql).fetchone()[0]
        value = float(value or 0)
        results.append({"name": c.name, "description": c.description, "blocking": c.blocking,
                        "violations": value, "passed": value == 0})
    return results


def assert_blocking(results: list[dict]) -> None:
    failed = [r for r in results if r["blocking"] and not r["passed"]]
    if failed:
        raise AssertionError("Blocking checks failed: " + ", ".join(f"{r['name']}={r['violations']}" for r in failed))
