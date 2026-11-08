"""Generate the self-serve explorer: one static HTML file with the cube embedded as JSON."""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

TEMPLATE = Path(__file__).with_name("explorer_template.html")


def build_explorer(con: duckdb.DuckDBPyConnection, out_dir: str | Path) -> dict:
    cube = [[str(m)[:7], c, r, t, round(float(g), 2), round(float(rt), 2), round(float(q), 2), int(o), int(u), int(cu)]
            for m, c, r, t, g, rt, q, o, u, cu in con.execute(
                "select month, country, region, customer_type, gross_sales, returns, quick_reversals, orders, units, customers "
                "from mart_explorer order by 1, 2, 4").fetchall()]
    prod = [[str(m)[:7], code, desc, round(float(rev), 2), int(units)] for m, code, desc, rev, units in con.execute(
        "select month, stock_code, description, sales_revenue, units from mart_explorer_product order by 1, 4 desc").fetchall()]
    segs = [{"segment": s, "customers": int(n), "revenue": round(float(rev), 2), "avg_orders": round(float(fo), 1)}
            for s, n, rev, fo in con.execute(
                "select segment, count(*), sum(monetary), avg(frequency) from mart_customer_rfm group by 1 order by 3 desc").fetchall()]
    raw_rows = con.execute("select count(*) from raw_lines").fetchone()[0]
    quarantined = dict(con.execute("select reject_reason, count(*) from quarantine_lines group by 1").fetchall())
    last_day = con.execute("select max(invoice_date) from fact_lines").fetchone()[0]
    last_month = str(last_day)[:7]
    days_in_last = int(str(last_day)[8:10])
    stats = {"raw_rows": raw_rows, "duplicates": quarantined.get("exact_duplicate", 0),
             "invalid": sum(v for k, v in quarantined.items() if k != "exact_duplicate"),
             "partial_month": last_month, "partial_days": days_in_last, "last_day": str(last_day),
             "first_day": str(con.execute("select min(invoice_date) from fact_lines").fetchone()[0])}
    data = {"cube": cube, "prod": prod, "seg": segs, "stats": stats}
    # A source description containing </script> must remain JSON text, not close the HTML element.
    embedded = json.dumps(data, separators=(",", ":"), ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/", embedded)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")
    return {"cube_rows": len(cube), "product_rows": len(prod), "bytes": len(html)}
