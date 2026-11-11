"""Historical management decision brief, derived from reconciled finance marts.

Coverage bounds are a caller assertion of complete source coverage, not inferred
from transaction timestamps. Partial boundary months are never compared.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from datetime import date
from pathlib import Path

from mart.pipeline import ROOT, build
from mart.provenance import code_records, freeze_source, sha256


def previous_month(value: date) -> date:
    return date(value.year - (value.month == 1), value.month - 1 if value.month > 1 else 12, 1)


def next_month(value: date) -> date:
    return date(value.year + (value.month == 12), value.month + 1 if value.month < 12 else 1, 1)


def period_metrics(con, month: date) -> dict:
    gross, returns, orders, guest, reversals = con.execute("""
        select coalesce(sum(amount) filter(where line_type='sale'),0),
               coalesce(sum(amount) filter(where line_type='return'),0),
               count(distinct invoice_no) filter(where line_type='sale'),
               coalesce(sum(amount) filter(where line_type='sale' and customer_id is null),0),
               coalesce(sum(amount) filter(where is_quick_reversal),0)
        from fact_lines where invoice_date >= ? and invoice_date < ?
    """, [month, next_month(month)]).fetchone()
    gross, returns, guest, reversals = map(float, (gross, returns, guest, reversals))
    return {"month": str(month), "gross_sales": gross, "returns": returns,
            "net_product_revenue": gross + returns, "orders": orders,
            "avg_order_value": gross / orders if orders else None,
            "return_rate": -returns / gross if gross else None,
            "guest_sales_share": guest / gross if gross else None,
            "quick_reversals": reversals}


def compare(current: dict, prior: dict | None) -> dict | None:
    if prior is None:
        return None
    changes = {}
    for metric in ("gross_sales", "returns", "net_product_revenue", "orders"):
        delta = current[metric] - prior[metric]
        changes[metric] = {"delta": delta, "relative_change": delta / abs(prior[metric]) if prior[metric] else None}
    return {"reference_month": prior["month"], "changes": changes}


def decision_report(con, coverage_start: str, coverage_end: str) -> dict:
    start, end = date.fromisoformat(coverage_start), date.fromisoformat(coverage_end)
    first_complete = start.replace(day=1) if start.day == 1 else next_month(start)
    latest = previous_month(end.replace(day=1))
    if end <= start or latest < first_complete:
        raise ValueError("At least one complete calendar month of declared source coverage is required")
    prior_month = previous_month(latest)
    current = period_metrics(con, latest)
    prior = period_metrics(con, prior_month) if prior_month >= first_complete else None
    year_ago = date(latest.year - 1, latest.month, 1)
    prior_year = period_metrics(con, year_ago) if year_ago >= first_complete else None
    drivers = None
    countries = []
    if prior is not None:
        drivers = {"gross_sales_change": current["gross_sales"] - prior["gross_sales"],
                   "returns_change": current["returns"] - prior["returns"],
                   "net_revenue_change": current["net_product_revenue"] - prior["net_product_revenue"],
                   "order_volume_effect": None, "basket_value_effect": None}
        if current["orders"] and prior["orders"]:
            drivers["order_volume_effect"] = ((current["orders"] - prior["orders"])
                                             * (current["avg_order_value"] + prior["avg_order_value"]) / 2)
            drivers["basket_value_effect"] = ((current["avg_order_value"] - prior["avg_order_value"])
                                             * (current["orders"] + prior["orders"]) / 2)
            if not math.isclose(drivers["order_volume_effect"] + drivers["basket_value_effect"],
                                drivers["gross_sales_change"], abs_tol=1e-6):
                raise ValueError("Order/basket decomposition does not reconcile")
        for country, before, after in con.execute("""
            select country,
                   coalesce(sum(amount) filter(where invoice_date < ?),0),
                   coalesce(sum(amount) filter(where invoice_date >= ?),0)
            from fact_lines where line_type in ('sale','return')
              and invoice_date >= ? and invoice_date < ? group by country order by country
        """, [latest, latest, prior_month, next_month(latest)]).fetchall():
            countries.append({"country": country, "prior_net_revenue": float(before),
                              "current_net_revenue": float(after), "delta": float(after - before)})
        countries.sort(key=lambda item: (-abs(item["delta"]), str(item["country"])))
        if not math.isclose(sum(item["delta"] for item in countries), drivers["net_revenue_change"], abs_tol=1e-6):
            raise ValueError("Country contributions do not reconcile to the revenue change")
    return {
        "schema_version": 1,
        "scope": "Historical decision support for one UCI online retailer; not current trading or measured business impact",
        "coverage": {"start_inclusive": str(start), "end_exclusive": str(end),
                     "basis": "Caller-declared complete source coverage; partial boundary months excluded"},
        "definitions": {"currency": "GBP", "revenue": "Product sales plus negative product returns; excludes shipping, discounts and fees",
                        "orders": "Distinct sale invoices", "country": "Existing finance mart customer-country allocation",
                        "driver_method": "Symmetric order-volume/basket-value decomposition; descriptive, not causal"},
        "current": current, "previous": prior, "previous_year": prior_year,
        "month_over_month": compare(current, prior), "year_over_year": compare(current, prior_year),
        "drivers": drivers, "country_contributions": countries,
        "proposed_actions": [
            {"action": "Review the largest country revenue changes with commercial and operations owners",
             "evidence": "Reconciled country contributions and order/basket decomposition",
             "data_needed": "Traffic, conversion, campaign spend, stock availability and contribution margins",
             "decision_rule": "Investigate the driver before allocating budget; revenue alone does not establish profitable demand"},
            {"action": "Audit returns and same-hour reversals separately before changing fulfillment or retention policy",
             "evidence": "Negative return amounts, gross return rate and separately flagged quick reversals",
             "data_needed": "Return reasons, product defects, dispatch dates, customer contacts and cost to serve",
             "decision_rule": "Confirm mechanisms and costs; aggregate associations do not identify an intervention effect"},
            {"action": "Use customer-value scores only to propose a controlled retention pilot",
             "evidence": "Separate historical 90-day repeat-purchase evaluation; past-spend baseline won revenue selection",
             "data_needed": "Consent, offer costs, randomized holdout, margins and incremental net contribution",
             "decision_rule": "Do not treat predicted repeat purchase or high spend as incremental offer response"},
        ],
        "limitations": [
            "Historical transaction totals are descriptive; no sampling confidence interval or causal uplift is claimed.",
            "Zero-sale months inside declared coverage are zero observations; a missing source feed must not be declared complete.",
            "Country allocation uses existing full-history finance definitions, not point-in-time model features.",
            "Guest sales contribute to revenue but cannot support identified-customer retention actions.",
            "No margin, acquisition cost, inventory, traffic or intervention outcome is available; "
            "revenue is not profit or lifetime value.",
        ],
    }


def render_markdown(report: dict) -> str:
    def money(value):
        return "Not available" if value is None else f"GBP {value:,.2f}"

    def percent(value):
        return "Undefined" if value is None else f"{value:.1%}"

    current, prior = report["current"], report["previous"]
    lines = ["# Management decision brief", "", report["scope"] + ".", "",
             f"Latest complete month: **{current['month'][:7]}**. Declared coverage: "
             f"{report['coverage']['start_inclusive']} to {report['coverage']['end_exclusive']} (exclusive). "
             "Partial boundary months are excluded.", "", "## What changed", "",
             "| Metric | Latest complete month | Previous complete month |", "|---|---:|---:|"]
    for key, label in [("gross_sales", "Gross product sales"), ("returns", "Product returns (negative)"),
                       ("net_product_revenue", "Net product revenue"), ("avg_order_value", "Gross average order value")]:
        lines.append(f"| {label} | {money(current[key])} | {money(prior[key] if prior else None)} |")
    lines += [f"| Sale invoices | {current['orders']:,} | {prior['orders'] if prior else 'Not available'} |",
              f"| Gross return rate | {percent(current['return_rate'])} | {percent(prior['return_rate'] if prior else None)} |", ""]
    for comparison, label in [("month_over_month", "Month over month"), ("year_over_year", "Year over year")]:
        result = report[comparison]
        change = result["changes"]["net_product_revenue"] if result else None
        lines += [f"{label} net product revenue change: " +
                  (f"{money(change['delta'])} ({percent(change['relative_change'])})." if change else "not available within coverage."), ""]
    if report["drivers"]:
        d = report["drivers"]
        lines += ["## Descriptive drivers", "",
                  f"Gross sales changed by {money(d['gross_sales_change'])}; the change in negative returns contributed "
                  f"{money(d['returns_change'])}. Together they reconcile to {money(d['net_revenue_change'])} net revenue change.", "",
                  f"The symmetric decomposition attributes {money(d['order_volume_effect'])} to order-volume differences "
                  f"and {money(d['basket_value_effect'])} to average-basket differences. "
                  "These are arithmetic contributions, not causes. Components are unavailable if either month has no sale invoices.", "",
                  "Largest country contributions by absolute change (all countries reconcile in JSON):", "",
                  "| Country | Net revenue change |", "|---|---:|"]
        lines += [f"| {item['country']} | {money(item['delta'])} |" for item in report["country_contributions"][:5]]
    lines += ["", "## Proposed decisions, evidence and missing context", ""]
    for action in report["proposed_actions"]:
        lines += [f"**{action['action']}.** {action['evidence']}. "
                  f"Needed before action: {action['data_needed']}. {action['decision_rule']}.", ""]
    lines += ["## Limits", ""] + ["- " + item for item in report["limitations"]]
    if report.get("provenance"):
        lines += ["", "## Reproduction", "",
                  f"Source CSV SHA256: `{report['provenance']['source']['sha256']}`. "
                  "The JSON records the exact input-byte identity and LF-normalized code hashes "
                  "(CRLF is converted to LF only for code hashing). No input CSV is included."]
    return "\n".join(lines) + "\n"


def write_decision_report(con, directory: Path, coverage_start: str, coverage_end: str, provenance: dict | None = None) -> dict:
    report = decision_report(con, coverage_start, coverage_end)
    if provenance:
        report["provenance"] = provenance
    (directory / "management.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (directory / "management.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--coverage-start", required=True)
    parser.add_argument("--coverage-end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("Management report destination must be new")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="finance-decisions-", dir=output.parent) as temporary:
        work = Path(temporary)
        frozen, source = freeze_source(args.raw, work / "source")
        stage = work / "bundle"
        stage.mkdir()
        con = build(frozen)
        try:
            write_decision_report(con, stage, args.coverage_start, args.coverage_end,
                                  {"source": source, "code_sha256_lf_normalized": code_records(ROOT)})
        finally:
            con.close()
        files = {path.name: {"sha256": sha256(path), "bytes": path.stat().st_size} for path in sorted(stage.iterdir())}
        (stage / "manifest.json").write_text(json.dumps({"artifacts": files}, indent=2) + "\n", encoding="utf-8")
        stage.rename(output)
    print(f"Historical management brief: {output}")


if __name__ == "__main__":
    main()
