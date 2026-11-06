"""Build the Excel workbook: values come from the marts, every KPI is a live formula over those values."""
from __future__ import annotations

from pathlib import Path

import duckdb
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

INK, MINT, PAPER, MUTED = "1B1F23", "0B7A5A", "F4F6F7", "5C6770"
HEAD_FILL = PatternFill("solid", fgColor=INK)
HEAD_FONT = Font(bold=True, color="FFFFFF", name="Calibri")
GBP, GBP2, PCT, INT = '£#,##0;[Red]-£#,##0', '£#,##0.00', '0.0%', '#,##0'


def _header(ws, row: int, labels: list[str]) -> None:
    for i, label in enumerate(labels, 1):
        c = ws.cell(row=row, column=i, value=label)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 32


def _widths(ws, widths: list[float]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def recalculate(path: str | Path) -> bool:
    """Round-trip through LibreOffice so formula results are cached and show in any viewer. Returns False if unavailable."""
    import shutil
    import subprocess
    import tempfile

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return False
    path = Path(path)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([soffice, "--headless", "--convert-to", "xlsx", "--outdir", tmp, str(path)],
                       check=True, capture_output=True, timeout=180)
        shutil.copy(Path(tmp) / path.name, path)
    return True


def build_workbook(con: duckdb.DuckDBPyConnection, path: str | Path) -> dict:
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"

    # ---------- Monthly (values + formulas) ----------
    ws = wb.create_sheet("Monthly")
    cols = ["Month", "Gross sales", "Returns", "Net revenue", "Same-hour reversals", "Orders", "Units", "Avg order value",
            "Return rate", "Return rate excl. reversals", "MoM growth (net)", "Active customers", "Shipping", "Discounts", "Other items"]
    _header(ws, 1, cols)
    rows = con.execute("select month, gross_sales, returns, quick_reversals, orders, units, active_customers, shipping, "
                       "discounts, other_items from mart_monthly order by month").fetchall()
    for i, (m, g, r, q, o, u, a, sh, di, ot) in enumerate(rows, start=2):
        ws.cell(i, 1, m).number_format = "mmm yyyy"
        ws.cell(i, 2, float(g)); ws.cell(i, 3, float(r)); ws.cell(i, 5, float(q))  # noqa: E702
        ws.cell(i, 4, f"=B{i}+C{i}")
        ws.cell(i, 6, int(o)); ws.cell(i, 7, int(u))  # noqa: E702
        ws.cell(i, 8, f"=B{i}/F{i}")
        ws.cell(i, 9, f"=-C{i}/B{i}")
        ws.cell(i, 10, f"=-(C{i}-E{i})/(B{i}+E{i})")
        ws.cell(i, 11, f"=D{i}/D{i-1}-1" if i > 2 else None)
        ws.cell(i, 12, int(a)); ws.cell(i, 13, float(sh)); ws.cell(i, 14, float(di)); ws.cell(i, 15, float(ot))  # noqa: E702
        for col, fmt in zip("BCDEHMNO", [GBP] * 4 + [GBP2] + [GBP] * 3, strict=True):
            ws[f"{col}{i}"].number_format = fmt
        for col in "FGL":
            ws[f"{col}{i}"].number_format = INT
        for col in "IJK":
            ws[f"{col}{i}"].number_format = PCT
    last = len(rows) + 1
    ws.cell(last + 2, 1, "Dec 2011 covers 1 to 9 December only. Do not compare it with full months.").font = Font(italic=True, color=MUTED)
    ws.freeze_panes = "B2"
    _widths(ws, [12, 14, 13, 14, 14, 9, 11, 13, 10, 14, 12, 12, 12, 11, 12])

    ch = LineChart()
    ch.title, ch.height, ch.width = "Net product revenue by month, GBP (Dec 2011 has 9 days, left out)", 8.5, 20
    ch.add_data(Reference(ws, min_col=4, min_row=1, max_row=last - 1), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=2, max_row=last - 1))
    ch.y_axis.number_format = '£#,##0'
    ch.x_axis.number_format = "mmm yy"
    ch.legend = None
    ch.series[0].graphicalProperties.line.solidFill = MINT
    ch.series[0].graphicalProperties.line.width = 28000
    ch.series[0].smooth = False
    ch.x_axis.delete = ch.y_axis.delete = False
    summary.add_chart(ch, "E3")

    # ---------- Pareto ----------
    wp = wb.create_sheet("Product Pareto")
    _header(wp, 1, ["Rank", "Stock code", "Description", "Sales revenue", "Share of all products", "Cumulative share", "Units sold",
                    "Units returned", "Unit return rate"])
    prod = con.execute("select revenue_rank, stock_code, description, sales_revenue, units_sold, units_returned "
                       "from mart_product order by revenue_rank limit 25").fetchall()
    total_prod = float(con.execute("select sum(sales_revenue) from mart_product").fetchone()[0])
    n_prod = con.execute("select count(*) from mart_product").fetchone()[0]
    wp["K1"], wp["L1"] = "Revenue, all products", total_prod
    wp["K2"], wp["L2"] = "Products with revenue", n_prod
    wp["L1"].number_format, wp["L2"].number_format = GBP, INT
    for i, (rk, code, desc, rev, units, ret) in enumerate(prod, start=2):
        wp.cell(i, 1, int(rk)); wp.cell(i, 2, code); wp.cell(i, 3, desc); wp.cell(i, 4, float(rev))  # noqa: E702
        wp.cell(i, 5, f"=D{i}/$L$1")
        wp.cell(i, 6, f"=E{i}" if i == 2 else f"=F{i-1}+E{i}")
        wp.cell(i, 7, int(units)); wp.cell(i, 8, int(ret))  # noqa: E702
        wp.cell(i, 9, f"=H{i}/G{i}")
        wp[f"D{i}"].number_format = GBP
        for col in "EFI":
            wp[f"{col}{i}"].number_format = PCT
        for col in "GH":
            wp[f"{col}{i}"].number_format = INT
    wp.freeze_panes = "A2"
    _widths(wp, [7, 12, 40, 14, 14, 14, 11, 12, 12, 3, 24, 14])
    pc = BarChart()
    pc.type, pc.title, pc.height, pc.width = "col", "Top 25 products: revenue", 8.5, 20
    pc.add_data(Reference(wp, min_col=4, min_row=1, max_row=26), titles_from_data=True)
    pc.set_categories(Reference(wp, min_col=2, min_row=2, max_row=26))
    pc.legend = None
    pc.y_axis.number_format = '£#,##0'
    pc.series[0].graphicalProperties.solidFill = MINT
    pc.x_axis.delete = pc.y_axis.delete = False
    wp.add_chart(pc, "K5")

    # ---------- Customers ----------
    wc = wb.create_sheet("Customers")
    _header(wc, 1, ["Customer ID", "Country", "Cohort month", "Days since last order", "Orders", "Net revenue", "R", "F", "M", "Segment"])
    cust = con.execute("select customer_id, country, cohort_month, recency_days, frequency, monetary, r_score, f_score, m_score, "
                       "segment from mart_customer_rfm order by monetary desc").fetchall()
    for i, row in enumerate(cust, start=2):
        for j, v in enumerate(row, 1):
            if j == 6:
                v = float(v)
            wc.cell(i, j, v)
        wc.cell(i, 3).number_format = "mmm yyyy"
        wc.cell(i, 6).number_format = GBP
    wc.freeze_panes = "A2"
    wc.auto_filter.ref = f"A1:J{len(cust) + 1}"
    _widths(wc, [12, 20, 12, 12, 9, 13, 5, 5, 5, 20])
    n_c = len(cust) + 1

    # ---------- Segments (formulas over Customers) ----------
    wsg = wb.create_sheet("Segments")
    _header(wsg, 1, ["Segment", "Customers", "Net revenue", "Share of revenue", "Avg revenue per customer", "Avg orders per customer",
                     "Share of customers"])
    segs = ["Champions", "Loyal", "Potential loyalists", "New", "Needs attention", "At risk", "Hibernating", "Net negative"]
    for i, name in enumerate(segs, start=2):
        wsg.cell(i, 1, name)
        wsg.cell(i, 2, f"=COUNTIF(Customers!$J$2:$J${n_c},A{i})")
        wsg.cell(i, 3, f"=SUMIF(Customers!$J$2:$J${n_c},A{i},Customers!$F$2:$F${n_c})")
        wsg.cell(i, 4, f"=C{i}/$C$10")
        wsg.cell(i, 5, f"=IF(B{i}=0,0,C{i}/B{i})")
        wsg.cell(i, 6, f"=IF(B{i}=0,0,AVERAGEIF(Customers!$J$2:$J${n_c},A{i},Customers!$E$2:$E${n_c}))")
        wsg.cell(i, 7, f"=B{i}/$B$10")
        wsg[f"B{i}"].number_format = INT
        wsg[f"C{i}"].number_format = wsg[f"E{i}"].number_format = GBP
        wsg[f"D{i}"].number_format = wsg[f"G{i}"].number_format = PCT
        wsg[f"F{i}"].number_format = "0.0"
    wsg["A10"], wsg["B10"], wsg["C10"] = "Total", "=SUM(B2:B9)", "=SUM(C2:C9)"
    wsg["B10"].number_format, wsg["C10"].number_format = INT, GBP
    for c in "ABC":
        wsg[f"{c}10"].font = Font(bold=True)
    _widths(wsg, [22, 12, 14, 14, 18, 18, 16])
    sc = BarChart()
    sc.type, sc.title, sc.height, sc.width = "bar", "Net revenue by RFM segment", 8.5, 16
    sc.add_data(Reference(wsg, min_col=3, min_row=1, max_row=9), titles_from_data=True)
    sc.set_categories(Reference(wsg, min_col=1, min_row=2, max_row=9))
    sc.legend = None
    sc.x_axis.scaling.orientation = "maxMin"
    sc.y_axis.number_format = '£#,##0'
    sc.series[0].graphicalProperties.solidFill = MINT
    sc.x_axis.delete = sc.y_axis.delete = False
    wsg.add_chart(sc, "I2")

    # ---------- Countries ----------
    wk = wb.create_sheet("Countries")
    _header(wk, 1, ["Country", "Region", "Gross sales", "Share of gross sales", "Orders", "Customers", "Avg order value"])
    ctry = con.execute("select country, region, gross_sales, orders, customers from mart_country "
                       "where gross_sales > 0 order by gross_sales desc limit 15").fetchall()
    total_g = float(con.execute("select sum(gross_sales) from mart_country").fetchone()[0])
    wk["I1"], wk["J1"] = "Gross sales, all countries", total_g
    wk["J1"].number_format = GBP
    for i, (cn, rg, g, o, cu) in enumerate(ctry, start=2):
        wk.cell(i, 1, cn); wk.cell(i, 2, rg); wk.cell(i, 3, float(g))  # noqa: E702
        wk.cell(i, 4, f"=C{i}/$J$1")
        wk.cell(i, 5, int(o)); wk.cell(i, 6, int(cu))  # noqa: E702
        wk.cell(i, 7, f"=C{i}/E{i}")
        wk[f"C{i}"].number_format = wk[f"G{i}"].number_format = GBP
        wk[f"D{i}"].number_format = PCT
    _widths(wk, [22, 16, 14, 14, 10, 11, 14, 3, 26, 14])

    # ---------- Summary (formulas) ----------
    s = summary
    s["A1"] = "E-commerce finance summary"
    s["A1"].font = Font(bold=True, size=16, color=INK)
    s["A2"] = "UCI Online Retail II, Dec 2009 to 9 Dec 2011, GBP. Every figure below is a formula over the other sheets."
    s["A2"].font = Font(italic=True, color=MUTED)
    kp = [
        ("Gross sales", f"=SUM(Monthly!B2:B{last})", GBP),
        ("Returns (credit notes)", f"=SUM(Monthly!C2:C{last})", GBP),
        ("Net product revenue", "=B4+B5", GBP),
        ("Orders", f"=SUM(Monthly!F2:F{last})", INT),
        ("Average order value", "=B4/B7", GBP2),
        ("Return rate as reported", "=-B5/B4", PCT),
        ("Same-hour order reversals", f"=SUM(Monthly!E2:E{last})", GBP),
        ("Return rate excl. same-hour reversals", "=-(B5-B10)/(B4+B10)", PCT),
        ("Best month (net revenue)", f"=INDEX(Monthly!A2:A{last},MATCH(MAX(Monthly!D2:D{last}),Monthly!D2:D{last},0))", "mmm yyyy"),
        ("Net revenue, Jan-Nov 2010", f'=SUMIFS(Monthly!D2:D{last},Monthly!A2:A{last},">="&DATE(2010,1,1),Monthly!A2:A{last},"<="&DATE(2010,11,30))', GBP),
        ("Net revenue, Jan-Nov 2011", f'=SUMIFS(Monthly!D2:D{last},Monthly!A2:A{last},">="&DATE(2011,1,1),Monthly!A2:A{last},"<="&DATE(2011,11,30))', GBP),
        ("Growth, Jan-Nov 2011 vs 2010", "=B14/B13-1", PCT),
        ("Orders, Jan-Nov 2010", f'=SUMIFS(Monthly!F2:F{last},Monthly!A2:A{last},">="&DATE(2010,1,1),Monthly!A2:A{last},"<="&DATE(2010,11,30))', INT),
        ("Orders, Jan-Nov 2011", f'=SUMIFS(Monthly!F2:F{last},Monthly!A2:A{last},">="&DATE(2011,1,1),Monthly!A2:A{last},"<="&DATE(2011,11,30))', INT),
        ("Order growth, Jan-Nov 2011 vs 2010", "=B17/B16-1", PCT),
        ("UK share of gross sales", "=Countries!D2", PCT),
        ("Champions: share of customer revenue", "=Segments!D2", PCT),
        ("Top 25 products: share of product revenue", "='Product Pareto'!F26", PCT),
    ]
    _header(s, 3, ["Metric", "Value"])
    for i, (label, formula, fmt) in enumerate(kp, start=4):
        s.cell(i, 1, label)
        c = s.cell(i, 2, formula)
        c.number_format = fmt
        c.font = Font(bold=True, color=INK)
        c.alignment = Alignment(horizontal="right")
    s["A4"].value = "Gross sales"
    s["A23"] = "Segments cover registered customers only. Guest orders (about 13% of gross sales) have no customer id."
    s["A23"].font = Font(italic=True, color=MUTED, size=9)
    _widths(s, [40, 18, 3])

    # ---------- Notes ----------
    wn = wb.create_sheet("Notes")
    notes = [
        "How to read this workbook",
        "Monthly, Product Pareto, Customers and Countries hold values loaded from the DuckDB marts. Every KPI, ratio and rollup is a formula.",
        "Change a value on Monthly or Customers and the Summary and Segments sheets recalculate.",
        "",
        "Definitions",
        "Gross sales: sum of quantity x price on sale lines (positive quantity and price, numeric invoice).",
        "Returns: credit-note lines (invoice starts with C, negative quantity). Negative numbers.",
        "Net revenue: gross sales plus returns. Shipping, fees, discounts and adjustments are kept out of it.",
        "Same-hour reversals: a return that exactly mirrors a sale by the same customer within 60 minutes (an order-entry error).",
        "Product Pareto revenue is net of same-hour reversals, so an order entered and cancelled in minutes does not rank as a best seller.",
        "RFM: recency, frequency and monetary quintiles per registered customer; segments follow rules in docs/metrics.md.",
        "",
        "Limits",
        "Public data for one UK online wholesaler (2009 to 2011). It is a practice dataset, not data from any employer.",
        "Customers are only the 5,900 or so with a customer id. About 13% of gross sales are guest orders with none.",
        "34,335 exact duplicate rows (GBP 431,717 of amount) were quarantined, not counted. This is an assumption that they are double-posted.",
    ]
    for i, t in enumerate(notes, start=1):
        wn.cell(i, 1, t)
        if t in ("How to read this workbook", "Definitions", "Limits"):
            wn.cell(i, 1).font = Font(bold=True, size=12, color=MINT)
    wn.column_dimensions["A"].width = 140

    wb.calculation.fullCalcOnLoad = True
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return {"customers": len(cust), "months": len(rows)}
