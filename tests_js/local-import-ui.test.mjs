import assert from 'node:assert/strict';
import test from 'node:test';
import {encode, HEADERS} from '../docs/import/core.mjs';

// This exercises the actual event handlers with a small DOM double, not visual browser QA.
test('browser acceptance, source trace, validate-only, invalid replacement, empty state and clear', async () => {
  const nodes = new Map(); let focused;
  function element() {
    const events = new Map(), classes = new Set();
    return {textContent: '', value: '', files: [], children: [], hidden: false, disabled: false, attributes: {},
      parentElement: {parentElement: {parentElement: {setAttribute(){}}}},
      classList: {add(name){classes.add(name)}, remove(name){classes.delete(name)}, contains(name){return classes.has(name)}},
      append(...items){this.children.push(...items)}, replaceChildren(...items){this.children = items},
      addEventListener(name, handler){events.set(name, handler)}, fire(name){return events.get(name)?.()},
      setAttribute(key, value){this.attributes[key] = value}, focus(){focused = this}, remove(){}, click(){return this.fire('click')},
    };
  }
  const document = {body: element(), createElement: element, getElementById(id){if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id)}};
  const $ = id => document.getElementById(id), previousDocument = globalThis.document;
  globalThis.document = document;
  try {
    $('as-of').value = '2026-03-31'; $('balance-filter').value = 'all';
    await import(`../docs/import/app.mjs?test=${Date.now()}`);
    $('sample').click();
    const until = Date.now() + 2000;
    while (!$('status').textContent.startsWith('Accepted') && Date.now() < until) await new Promise(resolve => setTimeout(resolve, 5));
    assert.match($('status').textContent, /Accepted synthetic sample/);
    assert.equal($('result').hidden, false); assert.equal(focused, $('result-title'));
    assert.equal($('metrics').children[0].children[0].textContent, '£100.00');
    assert.equal($('metrics').children[2].children[0].textContent, '£20.00');
    const first = $('invoice-rows').children[0]; first.children[7].children[0].click();
    assert.match($('trace-summary').textContent, /£100.00 invoiced − £50.00 allocated payments = £50.00 balance/);
    assert.match($('trace-source').textContent, /2, 7/);
    assert.match($('trace-payments').children[0].textContent, /PAY-001.*records 2, 7/);
    assert.equal(focused, $('trace'));
    $('search').value = 'no-match'; $('search').fire('input');
    assert.equal($('invoice-rows').children[0].children[0].textContent, 'No invoices match these filters.');
    $('as-of').value = '2026-04-30'; $('as-of').fire('change');
    assert.match($('result-state').textContent, /Previous accepted snapshot.*as-of date has changed/);
    await $('validate').click(); assert.match($('status').textContent, /Validation passed/);
    assert.equal($('metrics').children[0].children[0].textContent, '£100.00');
    assert.match($('result-state').textContent, /validated only/);
    const invoiceBytes = encode(HEADERS.invoices.join(',') + '\nI1,C1,GBP,2026-01-01,2026-01-31,-100\n');
    const paymentBytes = encode(HEADERS.payments.join(',') + '\n');
    $('invoices-file').files = [{name: '<img onerror=bad>.csv', size: invoiceBytes.length, arrayBuffer: async () => invoiceBytes.buffer}];
    $('payments-file').files = [{name: 'payments.csv', size: paymentBytes.length, arrayBuffer: async () => paymentBytes.buffer}];
    $('invoices-file').fire('change'); $('payments-file').fire('change');
    assert.equal($('invoices-name').textContent, '<img onerror=bad>.csv');
    await $('reconcile').click();
    assert.equal($('error-panel').hidden, false); assert.match($('errors').children[0].textContent, /invoices · record 2 · amount_minor/);
    assert.match($('result-state').textContent, /Previous accepted snapshot.*replacement was rejected/);
    assert.equal($('metrics').children[0].children[0].textContent, '£100.00');
    assert.equal($('reconcile').disabled, false);
    $('clear').click(); assert.equal($('result').hidden, true); assert.equal($('empty').hidden, false); assert.equal($('invoice-rows').children.length, 0);
    assert.equal($('identity').textContent, ''); assert.match($('status').textContent, /cleared/);
  } finally { globalThis.document = previousDocument; }
});
