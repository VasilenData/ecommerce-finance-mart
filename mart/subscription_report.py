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
button{font:inherit;cursor:pointer;border:0;border-radius:6px;padding:12px 18px;background:var(--ink);color:white}button:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid #087f74;outline-offset:3px}.review-cards{grid-template-columns:repeat(3,1fr)}.pending{font-weight:700;color:#795200}.wrap{overflow-wrap:anywhere}
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
<section><h2>Trace the restatement to its evidence</h2><p class="muted" id="audit-summary" aria-live="polite"></p>
<div class="table-wrap"><table><thead><tr><th>Scope</th><th>Entity</th><th>Measure</th><th>Original close</th><th>Selected cutoff</th><th>Change</th></tr></thead><tbody id="audit-deltas"></tbody></table></div>
<details><summary>Inspect the newly visible source versions</summary><ul id="audit-sources"></ul></details>
<p class="note">Changes are compared with the earliest displayed knowledge cutoff. Monthly rows use the selected month; invoice balances are for the entire reporting window. Source revisions and net report differences are evidence, not a causal allocation among simultaneous changes.</p></section>
<section aria-labelledby="review-title"><h2 id="review-title">Prepare a close-review request</h2><p class="pending">Pending human review · no stakeholder approval or real adoption claimed</p>
<p class="muted" id="review-window"></p><p>Separate unpaid invoice amounts from credits before proposing an action. The net ledger balance can hide both; it does not authorize collections, refunds or transfers between invoices.</p>
<div class="cards review-cards"><div class="card"><span>Gross unpaid invoice amounts</span><strong id="unpaid"></strong><small>Sum of positive invoice balances</small></div><div class="card"><span>Credit balances</span><strong id="credits"></strong><small>Magnitude of negative invoice balances</small></div><div class="card"><span>Net period-end balance</span><strong id="exposure-net"></strong><small>Gross unpaid minus credit balances</small></div></div>
<div class="table-wrap"><table><thead><tr><th>Invoice</th><th>Customer</th><th>Review category</th><th>Period-end balance</th></tr></thead><tbody id="exposure-invoices"></tbody></table></div>
<h3>Compare proposed review policies</h3><div class="toolbar"><div><label for="review-policy">Illustrative triage threshold</label><select id="review-policy"><option value="every-change">Every changed penny</option><option value="absolute-25">At least £25</option><option value="absolute-25-or-5pct">At least £25 or 5%</option></select></div><button id="download-review" type="button">Download review request</button></div>
<p id="review-summary" aria-live="polite"></p><p class="muted">Thresholds use absolute change and are inclusive. The combined policy flags £25 <em>or</em> 5%; a nonzero change from zero is flagged because its percentage is undefined. Below threshold does not mean approved. The policy changes the review queue only; the reported money never changes.</p>
<div class="table-wrap"><table><thead><tr><th>Scope / entity</th><th>Measure</th><th>Original</th><th>Selected cutoff</th><th>Change</th><th>Triage</th></tr></thead><tbody id="review-changes"></tbody></table></div>
<details><summary>Questions for a genuine reviewer</summary><ol id="review-questions"></ol><p class="muted">Download the source-bound request and share it with a reviewer yourself. Record their actual challenges and your response in a separate feedback record. A generated request or a passed test is not evidence of collaboration.</p><p><a href="https://github.com/VasilenData/ecommerce-finance-mart/blob/main/docs/STAKEHOLDER_REVIEW.md">Reviewer worksheet and change workflow →</a></p><p class="wrap"><code id="request-identity"></code></p></details></section>
<section><h2>Definitions that prevent bad decisions</h2><div class="columns"><dl><dt>MRR</dt><dd>Month-end contractual recurring run rate. No usage fees, proration, foreign exchange, free plans or annual billing normalization.</dd>
<dt>Billed amount</dt><dd>Latest visible invoice face value by issue month. A revised invoice replaces the earlier version at later knowledge cutoffs.</dd>
<dt>Net cash</dt><dd>Payments by paid date minus refunds by refunded date. Collection timing can differ from invoice timing.</dd></dl>
<dl><dt>Bookings</dt><dd>Not calculated. A signed contract's total commitment is not established by these monthly subscription events.</dd>
<dt>Recognized revenue</dt><dd>Not calculated. Performance obligations and recognition schedules are absent. This is not an accounting standard implementation.</dd>
<dt>Balance and exposure</dt><dd>Invoice face value − payments + refunds. Negative values are credits; they do not erase another invoice's unpaid amount. Period-end gross unpaid and credits are shown separately. Due dates and credit-note policy are absent, so unpaid does not imply overdue or collectible. This is a simplified invoice ledger, not a general ledger.</dd></dl></div></section>
<section><h2>Reproduce and inspect</h2><p class="muted">The complete report, exact input bytes and export tables belong to one immutable release. The manifest records executed code hashes and runtime versions.</p>
<div class="downloads"><a href="report.json" download>Report JSON</a><a href="monthly_mrr_bridge.csv" download>MRR CSV</a><a href="monthly_cash_bridge.csv" download>Cash CSV</a><a href="customer_mrr_bridge.csv" download>Customer CSV</a><a href="restatements.json" download>Restatement audit</a><a href="restatement_deltas.csv" download>Changes CSV</a><a href="review_requests.json" download>All review requests</a><a href="source.json" download>Source fixture</a><a href="manifest.json" download>Release manifest</a></div>
<details><summary>Checks and source identity</summary><p id="source-hash"></p><ul id="check-list"></ul></details></section>
<footer>Built with Python + DuckDB. Source data and incident scenarios are synthetic. The original historical retail workbook and explorer are a separate release.</footer>
</main><script id="report-data" type="application/json">__REPORT_JSON__</script><script>
'use strict';
const bundle=JSON.parse(document.getElementById('report-data').textContent),reports=bundle.reports;
const $=id=>document.getElementById(id),money=n=>new Intl.NumberFormat('en-GB',{style:'currency',currency:'GBP',maximumFractionDigits:2}).format(n/100);
const mrrFields=['opening_mrr_cents','new_mrr_cents','expansion_mrr_cents','reactivation_mrr_cents','contraction_mrr_cents','churn_mrr_cents','closing_mrr_cents'];
function row(target,values){const tr=document.createElement('tr');values.forEach(value=>{const td=document.createElement('td');td.textContent=value;tr.append(td)});$(target).append(tr)}
function audit(cutoff,month){
  const item=(bundle.restatements||[]).find(x=>x.candidate_cutoff===cutoff);
  $('audit-deltas').replaceChildren();$('audit-sources').replaceChildren();
  if(!item){$('audit-summary').textContent='Original close selected. Choose a later knowledge cutoff to inspect revisions.';return;}
  const shown=item.monetary_deltas.filter(x=>!x.month||x.month===month);
  $('audit-summary').textContent=`${item.source_changes.length} source records changed since ${item.baseline_cutoff.slice(0,10)}. ${shown.length} monetary differences are shown; ${item.checks.length} restatement reconciliation checks passed.`;
  const labels={monthly_mrr_bridge:'Monthly MRR',customer_mrr_bridge:'Customer MRR',monthly_cash_bridge:'Monthly cash',invoice_balances:'Period-end invoice'};
  shown.forEach(x=>row('audit-deltas',[labels[x.table],x.customer_id||x.invoice_id||'All customers',x.metric.replace(/_cents$/,'').replaceAll('_',' '),money(x.before_cents),money(x.after_cents),(x.delta_cents>0?'+':'')+money(x.delta_cents)]));
  if(!shown.length)row('audit-deltas',['No monetary differences in this selection.','','','','','']);
  item.source_changes.forEach(x=>{const li=document.createElement('li'),title=document.createElement('strong');title.textContent=x.feed.replaceAll('_',' ')+' / '+x.record_id;li.append(title);const detail=document.createElement('p');detail.className='muted';detail.textContent=x.changed_fields.map(key=>key+': '+JSON.stringify(x.before?.[key]??null)+' → '+JSON.stringify(x.after?.[key]??null)).join('; ');li.append(detail);$('audit-sources').append(li)});
}
function selectedReviewRequest(){const cutoff=reports[Number($('scenario').value)].knowledge_cutoff;return bundle.review_packets.find(x=>x.candidate_cutoff===cutoff&&x.proposed_policy.id===$('review-policy').value)}
function review(){const packet=selectedReviewRequest(),exposure=packet.period_end_exposure;
  $('review-window').textContent=`Entire reporting window: ${packet.period_start} inclusive to ${packet.period_end_exclusive} exclusive. Knowledge cutoff: ${packet.candidate_cutoff}. This section does not change with the reporting-month filter.`;
  $('unpaid').textContent=money(exposure.gross_unpaid_cents);$('credits').textContent=money(exposure.credit_magnitude_cents);$('exposure-net').textContent=money(exposure.net_balance_cents);
  $('exposure-invoices').replaceChildren();exposure.invoices.forEach(x=>row('exposure-invoices',[x.invoice_id,x.customer_id,x.classification,money(x.balance_cents)]));if(!exposure.invoices.length)row('exposure-invoices',['No nonzero invoice balances.','','','']);
  $('review-summary').textContent=`Pending human review. ${packet.flagged_metric_cell_count} of ${packet.changed_metric_cells.length} changed metric cells meet this proposed threshold; ${packet.source_record_change_count} source records changed. Cells may describe the same underlying correction at several grains. Unchanged values still require definition and source review.`;
  $('review-changes').replaceChildren();packet.changed_metric_cells.forEach(x=>row('review-changes',[x.grain+' / '+x.entity,x.metric.replace(/_cents$/,'').replaceAll('_',' '),money(x.before_cents),money(x.after_cents),(x.delta_cents>0?'+':'')+money(x.delta_cents),x.triage==='review_requested'?'Review requested: '+x.reasons.join('; '):'Below proposed threshold']));if(!packet.changed_metric_cells.length)row('review-changes',['No changed cells in the monitored measures.','','','','','Pending definition/source review']);
  $('review-questions').replaceChildren();packet.questions.forEach(q=>{const li=document.createElement('li');li.textContent=q;$('review-questions').append(li)});$('request-identity').textContent='Request SHA-256 '+packet.request_sha256;
}
function downloadReview(){const packet=selectedReviewRequest(),blob=new Blob([JSON.stringify(packet,null,2)+'\n'],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='close-review-'+packet.candidate_cutoff.slice(0,10)+'-'+packet.proposed_policy.id+'.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
reports.forEach((r,i)=>{const o=document.createElement('option');o.value=i;o.textContent=r.knowledge_cutoff.replace('T',' ').replace('+00:00',' UTC');$('scenario').append(o)});
reports[0].monthly_mrr_bridge.forEach(r=>{const o=document.createElement('option');o.value=r.month;o.textContent=r.month.slice(0,7);$('month').append(o)});
$('scenario').value=String(reports.length-1);$('month').value=reports[0].monthly_mrr_bridge.at(-1).month;$('review-policy').value='absolute-25-or-5pct';
function draw(){const r=reports[Number($('scenario').value)],month=$('month').value,m=r.monthly_mrr_bridge.find(x=>x.month===month),c=r.monthly_cash_bridge.find(x=>x.month===month);
  $('mrr').textContent=money(m.closing_mrr_cents);$('billed').textContent=money(c.invoiced_cents);$('cash').textContent=money(c.net_cash_cents);$('balance').textContent=money(c.closing_balance_cents);
  $('checks').textContent=r.checks.length+' reconciliation checks passed';$('checks').classList.add('status');
  const delta=m.closing_mrr_cents-m.opening_mrr_cents;
  const exposure=selectedReviewRequest().period_end_exposure;
  $('decision').textContent=`Recurring run rate ${delta>=0?'increased':'decreased'} by ${money(Math.abs(delta))} this month. Churn removes ${money(m.churn_mrr_cents)}; reactivation adds ${money(m.reactivation_mrr_cents)}. The selected month's net invoice balance is ${money(c.closing_balance_cents)}. At the full reporting-window end, gross unpaid invoice amounts are ${money(exposure.gross_unpaid_cents)} and credit balances are ${money(exposure.credit_magnitude_cents)}. Review these separately; a net balance does not establish what can be collected. Due dates and credit-note policy require source evidence.`;
  $('bridge').replaceChildren();mrrFields.forEach((key,i)=>{const box=document.createElement('div');box.className='step';const label=document.createElement('span');label.textContent=['Opening','+ New','+ Expansion','+ Reactivation','− Contraction','− Churn','Closing'][i];const value=document.createElement('strong');value.textContent=money(m[key]);box.append(label,value);$('bridge').append(box)});
  $('customers').replaceChildren();r.customer_mrr_bridge.filter(x=>x.month===month).forEach(x=>row('customers',[x.customer_id,...mrrFields.map(k=>money(x[k]))]));
  $('cash-table').replaceChildren();r.monthly_cash_bridge.forEach(x=>row('cash-table',[x.month.slice(0,7),...['opening_balance_cents','invoiced_cents','paid_cents','refunded_cents','net_cash_cents','closing_balance_cents'].map(k=>money(x[k]))]));
  $('check-list').replaceChildren();r.checks.forEach(x=>{const item=document.createElement('li');item.textContent=x.check+' — '+x.actual+' (expected '+x.expected+')';$('check-list').append(item)});
  audit(r.knowledge_cutoff,month);
  review();
}
reports.forEach(r=>row('comparisons',[r.knowledge_cutoff.slice(0,10),money(r.monthly_mrr_bridge.at(-1).closing_mrr_cents),money(r.monthly_cash_bridge.reduce((n,x)=>n+x.invoiced_cents,0)),money(r.monthly_cash_bridge.reduce((n,x)=>n+x.net_cash_cents,0)),money(r.monthly_cash_bridge.at(-1).closing_balance_cents)]));
$('source-hash').textContent='SHA-256 '+bundle.source_sha256;$('scenario').addEventListener('change',draw);$('month').addEventListener('change',draw);$('review-policy').addEventListener('change',review);$('download-review').addEventListener('click',downloadReview);draw();
</script></body></html>'''
