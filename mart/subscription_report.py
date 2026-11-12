"""Dependency-free, inspectable subscription decision explorer."""
import json


def render(bundle: dict) -> str:
    payload = json.dumps(bundle, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return TEMPLATE.replace("__REPORT_JSON__", payload)


TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Subscription revenue | Finance Mart</title>
<style>
:root{color-scheme:light;--ink:#102b3f;--muted:#536877;--line:#d8e2e8;--accent:#087f74;--paper:#f3f7f9;--red:#b54145}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,sans-serif}
a{color:#08675f;text-underline-offset:3px}header{background:var(--ink);color:white;padding:54px max(24px,calc((100vw - 1120px)/2)) 42px}
header a{color:#b4eee1}.eyebrow{font-size:12px;letter-spacing:.15em;text-transform:uppercase;color:#b4eee1;font-weight:700}
h1{font-size:clamp(32px,5vw,54px);letter-spacing:-.04em;line-height:1.1;margin:12px 0 18px;max-width:850px}
header p{max-width:800px;color:#d2dfe8}.scope{display:inline-block;padding:5px 10px;border:1px solid #547185;border-radius:5px;font-size:12px}
main{max-width:1168px;margin:auto;padding:28px 24px 60px}.toolbar{display:flex;gap:24px;align-items:end;flex-wrap:wrap}
label{display:block;font-size:13px;font-weight:700}select{font:inherit;padding:11px 16px;margin-top:5px;border:1px solid #b3c5d1;background:white;border-radius:6px;min-width:240px}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:24px 0}.card,section{background:white;border:1px solid var(--line);border-radius:10px}
.card{padding:20px}.card span{color:var(--muted);font-size:13px}.card strong{display:block;font-size:30px;letter-spacing:-.04em;line-height:1.3;margin:5px 0}
.card small{font-size:12px;color:var(--muted)}section{padding:24px;margin-top:20px}h2{font-size:23px;letter-spacing:-.02em;margin:0 0 12px}h3{font-size:16px}
.muted,.note{color:var(--muted);font-size:14px}.note{margin-bottom:0}.decision{border-left:4px solid var(--accent);background:#edf8f4;padding:16px 20px}
.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:11px 12px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{font-size:12px;color:var(--muted);font-weight:700}th:first-child,td:first-child{text-align:left}tbody tr:last-child td{border-bottom:0}
.positive{color:var(--accent)}.negative{color:var(--red)}.steps{display:grid;grid-template-columns:repeat(7,1fr);gap:10px;margin:20px 0}.step{padding:14px 10px;background:var(--paper);border-radius:6px;min-width:0}
.step span{display:block;font-size:11px;color:var(--muted)}.step strong{font-size:18px}.downloads{display:flex;gap:16px;flex-wrap:wrap;font-size:14px}
.columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}dt{font-weight:700;font-size:14px}dd{margin:3px 0 14px;color:var(--muted);font-size:14px}
details{margin-top:20px}summary{cursor:pointer;font-weight:600}code{overflow-wrap:anywhere;font-size:12px}.status{color:var(--accent);font-weight:700}footer{color:var(--muted);font-size:13px;margin-top:24px}
@media(max-width:820px){.cards{grid-template-columns:repeat(2,1fr)}.steps{grid-template-columns:repeat(4,1fr)}.columns{grid-template-columns:1fr}}
@media(max-width:450px){header{padding-top:35px}.cards{gap:8px}.card{padding:14px}.card strong{font-size:25px}section{padding:16px}.steps{grid-template-columns:repeat(2,1fr)}select{max-width:100%}}
</style></head><body>
<header><div class="eyebrow">Finance Mart / Subscription analytics</div><h1>Revenue has a timeline.<br>Make every change explainable.</h1>
<p>Explain how recurring commitments, invoices and collected cash differ. Replay a close and its later corrections without rewriting what was known before.</p>
<span class="scope">SYNTHETIC EXERCISE · GBP · NO COMMERCIAL IMPACT CLAIM</span>
<p><a href="https://github.com/VasilenData/ecommerce-finance-mart/blob/main/docs/SUBSCRIPTIONS.md">Inspect the engineering →</a></p></header>
<main><div class="toolbar"><div><label for="scenario">Knowledge cutoff</label><select id="scenario"></select></div>
<div><label for="month">Reporting month</label><select id="month"></select></div><div class="muted" id="checks" aria-live="polite"></div></div>
<div class="cards" aria-live="polite"><div class="card"><span>Closing recurring run rate</span><strong id="mrr"></strong><small>Month-end MRR; not recognized revenue</small></div>
<div class="card"><span>Invoices issued this month</span><strong id="billed"></strong><small>Latest invoice versions at this cutoff</small></div>
<div class="card"><span>Net cash this month</span><strong id="cash"></strong><small>Payments received less cash refunded</small></div>
<div class="card"><span>Closing invoice balance</span><strong id="balance"></strong><small>Invoiced − payments + refunds</small></div></div>
<section><h2>Decision brief</h2><div class="decision" id="decision"></div><p class="note">Proposed actions for a simulated business. These are analytical recommendations, not observed commercial results.</p></section>
<section><h2>Explain the MRR movement</h2><p class="muted">Customer-level opening → closing bridge. A customer can hold several subscriptions; changes are classified after aggregation.</p>
<div class="steps" id="bridge"></div><div class="table-wrap"><table><thead><tr><th>Customer</th><th>Opening</th><th>New</th><th>Expansion</th><th>Reactivation</th><th>Contraction</th><th>Churn</th><th>Closing</th></tr></thead><tbody id="customers"></tbody></table></div>
<p class="note">Month-end states net intramonth changes. A start and cancellation within the same month can net to zero; a later return is a reactivation when prior positive history exists.</p></section>
<section><h2>Reconcile billed amounts to cash</h2><div class="table-wrap"><table><thead><tr><th>Month</th><th>Opening balance</th><th>Invoiced</th><th>Payments</th><th>Refunds</th><th>Net cash</th><th>Closing balance</th></tr></thead><tbody id="cash-table"></tbody></table></div>
<p class="note">Payments and refunds are aggregated separately before invoice joins. A cash refund returns money; it does not cancel an invoice obligation. Credit notes, tax and write-offs are outside this contract.</p></section>
<section><h2>What changed after the original close?</h2><div class="table-wrap"><table><thead><tr><th>Knowledge cutoff</th><th>Period-end MRR</th><th>Period invoices</th><th>Period net cash</th><th>Closing balance</th></tr></thead><tbody id="comparisons"></tbody></table></div>
<p class="note">Recorded-at filters select the latest visible revision before effective business dates are applied. Later corrections restate earlier business periods only in reports whose cutoff includes those corrections.</p></section>
<section><h2>Definitions that prevent bad decisions</h2><div class="columns"><dl><dt>MRR</dt><dd>Month-end contractual recurring run rate. No usage fees, proration, foreign exchange, free plans or annual billing normalization.</dd>
<dt>Billed amount</dt><dd>Latest visible invoice face value by issue month. A revised invoice replaces the earlier version at later knowledge cutoffs.</dd>
<dt>Net cash</dt><dd>Payments by paid date minus refunds by refunded date. Collection timing can differ from invoice timing.</dd></dl>
<dl><dt>Bookings</dt><dd>Not calculated. A signed contract's total commitment is not established by these monthly subscription events.</dd>
<dt>Recognized revenue</dt><dd>Not calculated. Performance obligations and recognition schedules are absent. This is not an accounting standard implementation.</dd>
<dt>Balance</dt><dd>Invoice face value − payments + refunds. Negative values represent credit balances, not errors to hide. This is a simplified invoice ledger, not a general ledger.</dd></dl></div></section>
<section><h2>Reproduce and inspect</h2><p class="muted">The complete report, exact input bytes and export tables belong to one immutable release. The manifest records executed code hashes and runtime versions.</p>
<div class="downloads"><a href="report.json" download>Report JSON</a><a href="monthly_mrr_bridge.csv" download>MRR CSV</a><a href="monthly_cash_bridge.csv" download>Cash CSV</a><a href="customer_mrr_bridge.csv" download>Customer CSV</a><a href="source.json" download>Source fixture</a><a href="manifest.json" download>Release manifest</a></div>
<details><summary>Checks and source identity</summary><p id="source-hash"></p><ul id="check-list"></ul></details></section>
<footer>Built with Python + DuckDB. Source data and incident scenarios are synthetic. The original historical retail workbook and explorer are a separate release.</footer>
</main><script id="report-data" type="application/json">__REPORT_JSON__</script><script>
'use strict';
const bundle=JSON.parse(document.getElementById('report-data').textContent),reports=bundle.reports;
const $=id=>document.getElementById(id),money=n=>new Intl.NumberFormat('en-GB',{style:'currency',currency:'GBP',maximumFractionDigits:2}).format(n/100);
const mrrFields=['opening_mrr_cents','new_mrr_cents','expansion_mrr_cents','reactivation_mrr_cents','contraction_mrr_cents','churn_mrr_cents','closing_mrr_cents'];
function row(target,values){const tr=document.createElement('tr');values.forEach(value=>{const td=document.createElement('td');td.textContent=value;tr.append(td)});$(target).append(tr)}
reports.forEach((r,i)=>{const o=document.createElement('option');o.value=i;o.textContent=r.knowledge_cutoff.replace('T',' ').replace('+00:00',' UTC');$('scenario').append(o)});
reports[0].monthly_mrr_bridge.forEach(r=>{const o=document.createElement('option');o.value=r.month;o.textContent=r.month.slice(0,7);$('month').append(o)});
$('scenario').value=String(reports.length-1);$('month').value=reports[0].monthly_mrr_bridge.at(-1).month;
function draw(){const r=reports[Number($('scenario').value)],month=$('month').value,m=r.monthly_mrr_bridge.find(x=>x.month===month),c=r.monthly_cash_bridge.find(x=>x.month===month);
  $('mrr').textContent=money(m.closing_mrr_cents);$('billed').textContent=money(c.invoiced_cents);$('cash').textContent=money(c.net_cash_cents);$('balance').textContent=money(c.closing_balance_cents);
  $('checks').textContent=r.checks.length+' reconciliation checks passed';$('checks').classList.add('status');
  const delta=m.closing_mrr_cents-m.opening_mrr_cents;
  $('decision').textContent=`Recurring run rate ${delta>=0?'increased':'decreased'} by ${money(Math.abs(delta))} this month. Churn removes ${money(m.churn_mrr_cents)}; reactivation adds ${money(m.reactivation_mrr_cents)}. Review lost recurring commitments separately from collections: ${money(c.closing_balance_cents)} remains on the invoice ledger. Validate the reasons for cancellations and unpaid or refunded balances before proposing retention or collections actions.`;
  $('bridge').replaceChildren();mrrFields.forEach((key,i)=>{const box=document.createElement('div');box.className='step';const label=document.createElement('span');label.textContent=['Opening','+ New','+ Expansion','+ Reactivation','− Contraction','− Churn','Closing'][i];const value=document.createElement('strong');value.textContent=money(m[key]);box.append(label,value);$('bridge').append(box)});
  $('customers').replaceChildren();r.customer_mrr_bridge.filter(x=>x.month===month).forEach(x=>row('customers',[x.customer_id,...mrrFields.map(k=>money(x[k]))]));
  $('cash-table').replaceChildren();r.monthly_cash_bridge.forEach(x=>row('cash-table',[x.month.slice(0,7),...['opening_balance_cents','invoiced_cents','paid_cents','refunded_cents','net_cash_cents','closing_balance_cents'].map(k=>money(x[k]))]));
  $('check-list').replaceChildren();r.checks.forEach(x=>{const item=document.createElement('li');item.textContent=x.check+' — '+x.actual+' (expected '+x.expected+')';$('check-list').append(item)});
}
reports.forEach(r=>row('comparisons',[r.knowledge_cutoff.slice(0,10),money(r.monthly_mrr_bridge.at(-1).closing_mrr_cents),money(r.monthly_cash_bridge.reduce((n,x)=>n+x.invoiced_cents,0)),money(r.monthly_cash_bridge.reduce((n,x)=>n+x.net_cash_cents,0)),money(r.monthly_cash_bridge.at(-1).closing_balance_cents)]));
$('source-hash').textContent='SHA-256 '+bundle.source_sha256;$('scenario').addEventListener('change',draw);$('month').addEventListener('change',draw);draw();
</script></body></html>'''
