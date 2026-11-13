import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const html = await readFile(new URL('../docs/subscriptions/2026-11-13-review/index.html', import.meta.url), 'utf8');
const source = await readFile(new URL('../mart/subscription_report.py', import.meta.url), 'utf8');
const payload = html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const code = html.match(/<\/script><script>([\s\S]*?)<\/script>/)[1];

// Exercise the emitted script and real release payload; the small DOM supports
// only the APIs this static explorer uses. This is not a visual browser test.
function openExplorer(changeFixture=()=>{}) {
  const nodes = new Map();
  const downloads=[], revoked=[];
  function element() {
    return {textContent:'', value:'', children:[], classList:{add(){}},
      append(...items){this.children.push(...items)}, replaceChildren(){this.children=[]},
      addEventListener(){}, click(){}, remove(){}};
  }
  const document = {body:element(), createElement:element, getElementById(id){
    if(!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  }};
  const fixture=JSON.parse(payload);changeFixture(fixture);
  document.getElementById('report-data').textContent=JSON.stringify(fixture);
  const context = vm.createContext({document, Intl, Blob,
    URL:{createObjectURL(blob){downloads.push(blob);return 'blob:test'},revokeObjectURL(url){revoked.push(url)}},
    setTimeout(callback){callback()}});
  vm.runInContext(code, context);
  return {nodes,downloads,revoked,
    draw(scenario,month){nodes.get('scenario').value=String(scenario);nodes.get('month').value=month;vm.runInContext('draw()',context)},
    policy(id){nodes.get('review-policy').value=id;vm.runInContext('review()',context)},
    request(){return JSON.parse(vm.runInContext('JSON.stringify(selectedReviewRequest())',context))},
    download(){vm.runInContext('downloadReview()',context)}};
}

test('published explorer is rendered from the current template',()=>{
  const template=source.match(/TEMPLATE = r'''([\s\S]*)'''\s*$/)[1];
  const [before,after]=template.split('__REPORT_JSON__');
  assert.ok(html.startsWith(before));assert.ok(html.endsWith(after));
});

test('restatement explorer separates customer MRR and period invoice changes',()=>{
  const app=openExplorer();
  app.draw(2,'2026-03-01');
  assert.match(app.nodes.get('audit-summary').textContent,/2 source records changed/);
  const rows=app.nodes.get('audit-deltas').children.map(row=>row.children.map(cell=>cell.textContent));
  assert.ok(rows.some(row=>row[0]==='Customer MRR'&&row[1]==='B'&&row[2]==='closing mrr'&&row[5]==='+£10.00'));
  assert.ok(rows.some(row=>row[0]==='Period-end invoice'&&row[1]==='invoice-B-2'&&row[2]==='balance'&&row[5]==='+£10.00'));
  assert.ok(!rows.some(row=>row[2]==='net cash'));
});

test('changing cutoff clears later source changes and delta rows',()=>{
  const app=openExplorer();
  app.draw(1,'2026-02-01');
  assert.equal(app.nodes.get('audit-sources').children.length,1);
  assert.match(app.nodes.get('audit-sources').children[0].children[0].textContent,/b-contract/);
  app.draw(0,'2026-02-01');
  assert.equal(app.nodes.get('audit-sources').children.length,0);
  assert.equal(app.nodes.get('audit-deltas').children.length,0);
  assert.match(app.nodes.get('audit-summary').textContent,/Original close selected/);
});

test('unchanged month is distinguished from missing audit evidence',()=>{
  const app=openExplorer();
  app.draw(1,'2026-01-01');
  assert.match(app.nodes.get('audit-summary').textContent,/0 monetary differences/);
  assert.match(app.nodes.get('audit-deltas').children[0].children[0].textContent,/No monetary differences/);
  assert.equal(app.nodes.get('audit-sources').children.length,1);
});

test('materiality changes the review queue without changing ledger amounts or approval status',()=>{
  const app=openExplorer(),original=app.nodes.get('balance').textContent;
  assert.match(app.nodes.get('review-summary').textContent,/6 of 8 changed metric cells/);
  app.policy('absolute-25');
  assert.match(app.nodes.get('review-summary').textContent,/0 of 8 changed metric cells/);
  assert.equal(app.request().review_status,'pending_human_review');
  app.policy('every-change');
  assert.match(app.nodes.get('review-summary').textContent,/8 of 8 changed metric cells/);
  assert.equal(app.nodes.get('balance').textContent,original);
  assert.equal(app.request().feedback_template.reviewer,null);
});

test('period-end exposure never masquerades as the selected month',()=>{
  const app=openExplorer();
  app.draw(2,'2026-01-01');
  assert.equal(app.nodes.get('balance').textContent,'£0.00');
  assert.equal(app.nodes.get('unpaid').textContent,'£50.00');
  assert.match(app.nodes.get('review-window').textContent,/2026-04-01 exclusive/);
  assert.match(app.nodes.get('review-window').textContent,/does not change with the reporting-month filter/);
  app.draw(0,'2026-01-01');
  assert.equal(app.nodes.get('unpaid').textContent,'£40.00');
  assert.match(app.nodes.get('review-summary').textContent,/Pending human review. 0 of 0/);
});

test('net zero decision brief exposes £100 unpaid and £100 credits separately',()=>{
  // Independently specified presentation case. The Python test runs this same
  // two-invoice/one-payment example through SQL and exposure reconciliation.
  const app=openExplorer(bundle=>{
    bundle.reports.forEach(r=>r.monthly_cash_bridge.forEach(row=>row.closing_balance_cents=0));
    bundle.review_packets.forEach(packet=>Object.assign(packet.period_end_exposure,
      {gross_unpaid_cents:10000,credit_magnitude_cents:10000,net_balance_cents:0}));
  });
  assert.equal(app.nodes.get('exposure-net').textContent,'£0.00');
  assert.equal(app.nodes.get('unpaid').textContent,'£100.00');
  assert.equal(app.nodes.get('credits').textContent,'£100.00');
  assert.match(app.nodes.get('decision').textContent,/gross unpaid invoice amounts are £100.00 and credit balances are £100.00/);
  assert.doesNotMatch(app.nodes.get('decision').textContent,/£0.00 remains/);
});

test('download exports the selected source-bound pending request and releases its URL',async()=>{
  const app=openExplorer();app.draw(1,'2026-01-01');app.policy('every-change');app.download();
  assert.equal(app.downloads.length,1);
  const exported=JSON.parse(await app.downloads[0].text());
  assert.deepEqual(exported,app.request());
  assert.equal(exported.proposed_policy.id,'every-change');
  assert.equal(exported.candidate_cutoff,'2026-04-03T00:00:00+00:00');
  assert.equal(exported.review_status,'pending_human_review');
  assert.match(exported.source_sha256,/^[0-9a-f]{64}$/);
  assert.deepEqual(app.revoked,['blob:test']);
});
