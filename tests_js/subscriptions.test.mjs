import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const html = await readFile(new URL('../docs/subscriptions/2026-11-12-restatement/index.html', import.meta.url), 'utf8');
const payload = html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const code = html.match(/<\/script><script>([\s\S]*?)<\/script>/)[1];

// Exercise the emitted script and real release payload; the small DOM supports
// only the APIs this static explorer uses. This is not a visual browser test.
function openExplorer() {
  const nodes = new Map();
  function element() {
    return {textContent:'', value:'', children:[], classList:{add(){}},
      append(...items){this.children.push(...items)}, replaceChildren(){this.children=[]},
      addEventListener(){}};
  }
  const document = {createElement:element, getElementById(id){
    if(!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  }};
  document.getElementById('report-data').textContent=payload;
  const context = vm.createContext({document, Intl});
  vm.runInContext(code, context);
  return {nodes, draw(scenario,month){nodes.get('scenario').value=String(scenario);nodes.get('month').value=month;vm.runInContext('draw()',context)}};
}

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
