import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile, writeFile, mkdtemp, mkdir, readdir, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import test from 'node:test';
import {HEADERS, LIMITS, encode, parseCSV, validateImport, reconcile, buildReport, csvCell, artifacts, validDate} from '../docs/import/core.mjs';
import {SAMPLE_INVOICES, SAMPLE_PAYMENTS, SAMPLE_AS_OF} from '../docs/import/sample.mjs';
import {ENGINE_SHA256} from '../docs/import/identity.mjs';

const root = new URL('../', import.meta.url), sha = bytes => createHash('sha256').update(bytes).digest('hex');
const sample = () => ({invoices: encode(SAMPLE_INVOICES), payments: encode(SAMPLE_PAYMENTS), asOf: SAMPLE_AS_OF});
const invoiceHeader = HEADERS.invoices.join(',') + '\n', paymentHeader = HEADERS.payments.join(',') + '\n';
const basic = () => ({invoices: encode(invoiceHeader + 'I1,C1,GBP,2026-01-01,2026-01-31,10000\n'), payments: encode(paymentHeader), asOf: '2026-01-31'});
const report = input => buildReport(input, sha, ENGINE_SHA256);
const cli = args => spawnSync(process.execPath, [new URL('../scripts/import-finance.mjs', import.meta.url).pathname.replace(/^\/(.:\/)/, '$1'), ...args], {encoding: 'utf8'});

test('independent hand arithmetic: split cash, unpaid and excess cash remain separate', async () => {
  const {report: r} = await report(sample());
  assert.deepEqual(r.totals, {amount_minor: 27000, paid_minor: 19000, balance_minor: 8000, unpaid_minor: 10000, overpaid_minor: 2000, overdue_minor: 5000});
  assert.deepEqual(r.counts, {invoices: 4, payments: 4, customers: 2, future_invoices: 1, future_payments: 1});
  assert.deepEqual(r.invoices.map(i => [i.invoice_id, i.balance_minor, i.status]), [['INV-001', 5000, 'overdue'], ['INV-002', 5000, 'open'], ['INV-003', -2000, 'overpaid'], ['INV-004', 0, 'settled']]);
  assert.deepEqual(r.invoices[0].payment_ids, ['PAY-001', 'PAY-002']);
  assert.deepEqual(r.customers, [
    {customer_id: 'CEDAR', currency: 'GBP', invoice_count: 2, amount_minor: 15000, paid_minor: 5000, balance_minor: 10000, unpaid_minor: 10000, overpaid_minor: 0, overdue_minor: 5000},
    {customer_id: 'LARK', currency: 'GBP', invoice_count: 2, amount_minor: 12000, paid_minor: 14000, balance_minor: -2000, unpaid_minor: 0, overpaid_minor: 2000, overdue_minor: 0},
  ]);
  assert.equal(r.provenance.inputs.invoices.duplicate_records, 1);
  assert.equal(r.provenance.inputs.payments.duplicate_records, 1);
  assert.deepEqual(r.provenance.records.invoices[0], {id: 'INV-001', records: [2, 7]});
  assert.ok(r.checks.every(check => check.passed));
});

test('a customer with equal unpaid and overpaid invoices exposes both despite zero net', () => {
  const input = basic(); input.invoices = encode(invoiceHeader + 'I1,C1,GBP,2026-01-01,2026-01-31,10000\nI2,C1,GBP,2026-01-01,2026-01-31,10000\n');
  input.payments = encode(paymentHeader + 'P1,I2,2026-01-15,20000\n');
  const r = reconcile(validateImport(input));
  assert.deepEqual([r.totals.unpaid_minor, r.totals.overpaid_minor, r.totals.balance_minor], [10000, 10000, 0]);
  assert.deepEqual([r.customers[0].unpaid_minor, r.customers[0].overpaid_minor], [10000, 10000]);
});

test('inclusive business date excludes later cash; due today is not overdue', () => {
  const input = basic(); input.payments = encode(paymentHeader + 'P1,I1,2026-02-01,2500\n');
  let r = reconcile(validateImport(input)); assert.equal(r.totals.overdue_minor, 0); assert.equal(r.totals.paid_minor, 0); assert.equal(r.counts.future_payments, 1);
  input.asOf = '2026-02-01'; r = reconcile(validateImport(input)); assert.equal(r.totals.overdue_minor, 7500); assert.equal(r.totals.paid_minor, 2500);
  input.asOf = '2025-12-31'; r = reconcile(validateImport(input)); assert.equal(r.invoices.length, 0); assert.equal(r.totals.balance_minor, 0); assert.equal(r.counts.future_invoices, 1);
});

test('row permutations and repeated identical identities preserve financial output and semantic identity', async () => {
  const original = (await report(sample())).report;
  // Deterministic independent permutations, including removing and multiplying replay copies.
  for (let offset = 0; offset < 6; offset++) {
    const changed = sample();
    for (const [name, text] of [['invoices', SAMPLE_INVOICES], ['payments', SAMPLE_PAYMENTS]]) {
      const [header, ...rows] = text.trimEnd().split('\n');
      const unique = [...new Set(rows)];
      const permuted = [...unique.slice(offset % unique.length), ...unique.slice(0, offset % unique.length)].reverse();
      changed[name] = encode([header, ...permuted, ...permuted.slice(0, offset)].join('\n') + '\n');
    }
    const r = (await report(changed)).report;
    assert.deepEqual(r.totals, original.totals); assert.deepEqual(r.invoices, original.invoices); assert.deepEqual(r.customers, original.customers);
    assert.equal(r.provenance.snapshot_sha256, original.provenance.snapshot_sha256);
    assert.notEqual(r.provenance.inputs.invoices.sha256, original.provenance.inputs.invoices.sha256);
  }
});

test('same-ID changed amounts, references and dates reject the complete replacement with record and field', () => {
  for (const [name, conflicting] of [
    ['invoices', 'I1,C1,GBP,2026-01-01,2026-01-31,10001\n'],
    ['invoices', 'I1,C2,GBP,2026-01-01,2026-01-31,10000\n'],
    ['payments', 'P1,I1,2026-01-16,1000\n'],
  ]) {
    const input = basic(); if (name === 'payments') input.payments = encode(paymentHeader + 'P1,I1,2026-01-15,1000\n');
    input[name] = encode(new TextDecoder().decode(input[name]) + conflicting);
    const v = validateImport(input); assert.equal(v.ok, false); assert.equal(v.errors[0].record, 3); assert.equal(v.errors[0].field, name === 'invoices' ? 'invoice_id' : 'payment_id'); assert.match(v.errors[0].message, /Conflicting retry/);
    assert.throws(() => reconcile(v), /Validation must pass/);
  }
});

test('strict schema rejects ambiguity, merchant formats, credit/refund values and malicious cells', () => {
  const cases = [
    ['currency', 'GBP', 'USD'], ['amount_minor', '10000', '0'], ['amount_minor', '10000', '-100'],
    ['amount_minor', '10000', '+100'], ['amount_minor', '10000', '01'], ['amount_minor', '10000', '10.00'],
    ['amount_minor', '10000', '1e3'], ['amount_minor', '10000', '100000000001'], ['amount_minor', '10000', ' 10000'],
    ['invoice_id', 'I1', '=HYPERLINK("evil")'], ['invoice_id', 'I1', '<img src=x onerror=alert(1)>'],
    ['customer_id', 'C1', 'customer name'], ['customer_id', 'C1', 'é'],
    ['issued_date', '2026-01-01', '2026-02-30'], ['issued_date', '2026-01-01', '01/01/2026'],
    ['issued_date', '2026-01-01', '2026-01-01T00:00:00Z'], ['due_date', '2026-01-31', '2025-12-31'],
  ];
  for (const [field, from, to] of cases) {
    const input = basic(); input.invoices = encode(new TextDecoder().decode(input.invoices).replace(from, to));
    const v = validateImport(input); assert.equal(v.ok, false, `${field}: ${to}`); assert.equal(v.errors[0].record, 2); assert.equal(v.errors[0].field, field);
  }
  for (const text of ['', invoiceHeader, invoiceHeader.replace('amount_minor', 'total'), invoiceHeader + 'I1,C1,GBP,2026-01-01,2026-01-31,10000,extra\n', invoiceHeader + '\n']) {
    assert.equal(validateImport({...basic(), invoices: encode(text)}).ok, false);
  }
  assert.equal(validateImport({...basic(), asOf: '2026-02-30'}).errors[0].field, 'as_of');
  assert.equal(validDate('2024-02-29'), true); assert.equal(validDate('1900-02-29'), false); assert.equal(validDate('0001-01-01'), false);
});

test('unknown invoice references, prepayments and negative refunds are rejected', () => {
  for (const [line, field] of [['P1,UNKNOWN,2026-01-10,100\n', 'invoice_id'], ['P1,I1,2025-12-31,100\n', 'paid_date'], ['P1,I1,2026-01-10,-100\n', 'amount_minor']]) {
    const input = {...basic(), payments: encode(paymentHeader + line)}, v = validateImport(input);
    assert.equal(v.ok, false); assert.equal(v.errors[0].file, 'payments'); assert.equal(v.errors[0].record, 2); assert.equal(v.errors[0].field, field);
  }
});

test('CSV parsing accepts quoted/CRLF/BOM and rejects broken quoting, bad UTF-8, width and limits', () => {
  const quoted = '\uFEFF' + invoiceHeader.trimEnd().split(',').map(c => `"${c}"`).join(',') + '\r\n"I1","C1","GBP","2026-01-01","2026-01-31","10000"\r\n';
  assert.equal(validateImport({...basic(), invoices: encode(quoted)}).ok, true);
  for (const text of [invoiceHeader + '"unterminated', invoiceHeader + '"I1"tail,C1,GBP,2026-01-01,2026-01-31,10000', invoiceHeader + 'I"1,C1,GBP,2026-01-01,2026-01-31,10000', invoiceHeader.replace('\n', '\r'), invoiceHeader + 'x'.repeat(257)]) {
    assert.ok(parseCSV(encode(text), 'invoices').errors.length);
  }
  assert.equal(parseCSV(new Uint8Array([0xff, 0xfe]), 'invoices').errors[0].field, 'encoding');
  assert.match(parseCSV(new Uint8Array(LIMITS.bytes + 1), 'invoices').errors[0].message, /2 MiB/);
  const tooMany = invoiceHeader + 'I1,C1,GBP,2026-01-01,2026-01-31,1\n'.repeat(LIMITS.records + 1);
  assert.match(parseCSV(encode(tooMany), 'invoices').errors[0].message, /10,000/);
  assert.equal(parseCSV(encode(invoiceHeader + '"a\nb",C1,GBP,2026-01-01,2026-01-31,100\n'), 'invoices').rows[1][0], 'a\nb');
});

test('maximum admitted aggregate remains an exact safe integer', () => {
  assert.ok(LIMITS.records * LIMITS.amount < Number.MAX_SAFE_INTEGER);
  const input = basic();
  input.invoices = encode(invoiceHeader + Array.from({length: 10000}, (_, i) => `I${i},C,GBP,2026-01-01,2026-01-31,100000000000`).join('\n'));
  const v = validateImport(input); assert.equal(v.ok, true);
  const r = reconcile(v); assert.equal(r.totals.amount_minor, 1000000000000000); assert.ok(Number.isSafeInteger(r.totals.amount_minor));
});

test('exports escape formula-looking strings, quotes and newlines; arithmetic negative cells stay numeric', () => {
  for (const text of ['=SUM(A1)', '+cmd', '-cmd', '@SUM(A1)', '\t=1', '\r\n+1', '\uFEFF=1']) assert.match(csvCell(text), /^"'/);
  assert.equal(csvCell(-100), '"-100"'); assert.equal(csvCell('safe"\ntext'), '"safe""\ntext"');
});

test('raw fingerprints, deterministic bundle and generated implementation identity agree', async () => {
  const input = sample(), a = (await report(input)).report, b = (await report(input)).report;
  assert.deepEqual(artifacts(a), artifacts(b));
  assert.equal(a.provenance.inputs.invoices.sha256, sha(input.invoices));
  assert.equal(ENGINE_SHA256, sha(await readFile(new URL('docs/import/core.mjs', root))));
  assert.equal((await readFile(new URL('docs/import/sample-invoices.csv', root), 'utf8')), SAMPLE_INVOICES);
  assert.equal((await readFile(new URL('docs/import/sample-payments.csv', root), 'utf8')), SAMPLE_PAYMENTS);
  const csv = artifacts(a)['invoices.csv']; assert.match(csv, /"INV-003","LARK","GBP","2026-03-05","2026-03-20","8000","10000","-2000","0","2000"/);
});

test('CLI fresh-user validate, export, deterministic retry and failed replacement preserve prior bytes', async t => {
  const dir = await mkdtemp(path.join(tmpdir(), 'finance-import-test-')), invoices = path.join(dir, 'invoices.csv'), payments = path.join(dir, 'payments.csv');
  t.after(() => rm(dir, {recursive: true, force: true}));
  await writeFile(invoices, SAMPLE_INVOICES); await writeFile(payments, SAMPLE_PAYMENTS);
  const args = ['--invoices', invoices, '--payments', payments, '--as-of', SAMPLE_AS_OF];
  let result = cli([...args, '--validate-only']); assert.equal(result.status, 0, result.stderr); assert.equal(JSON.parse(result.stdout).validated_only, true);
  const output = path.join(dir, 'accepted'); result = cli([...args, '--output', output]); assert.equal(result.status, 0, result.stderr);
  const before = await readFile(path.join(output, 'report.json')); const manifest = JSON.parse(await readFile(path.join(output, 'manifest.json')));
  assert.deepEqual(JSON.parse(before).totals, {amount_minor: 27000, paid_minor: 19000, balance_minor: 8000, unpaid_minor: 10000, overpaid_minor: 2000, overdue_minor: 5000});
  for (const [name, meta] of Object.entries(manifest.artifacts)) { const bytes = await readFile(path.join(output, name)); assert.equal(sha(bytes), meta.sha256); assert.equal(bytes.length, meta.bytes); }
  const retry = path.join(dir, 'retry'); result = cli([...args, '--output', retry]); assert.equal(result.status, 0, result.stderr);
  for (const name of await readdir(output)) assert.deepEqual(await readFile(path.join(output, name)), await readFile(path.join(retry, name)));
  result = cli([...args, '--output', output]); assert.equal(result.status, 1); assert.match(result.stderr, /already exists/); assert.deepEqual(await readFile(path.join(output, 'report.json')), before);
  const emptyExisting = path.join(dir, 'empty-existing'); await mkdir(emptyExisting); result = cli([...args, '--output', emptyExisting]); assert.equal(result.status, 1); assert.deepEqual(await readdir(emptyExisting), []);
  await writeFile(invoices, SAMPLE_INVOICES.replace('10000', '-10000'));
  result = cli([...args, '--output', output]); assert.equal(result.status, 2); assert.equal(JSON.parse(result.stderr).errors[0].field, 'amount_minor'); assert.deepEqual(await readFile(path.join(output, 'report.json')), before);
  assert.equal((await readdir(dir)).some(name => name.endsWith('.lock') || name.startsWith('.finance-stage-')), false);
});

test('CLI bounded reads and missing inputs fail without publishing or logging private paths', async t => {
  const dir = await mkdtemp(path.join(tmpdir(), 'finance-import-bad-')), invoices = path.join(dir, 'private-name.csv'), payments = path.join(dir, 'payments.csv'), output = path.join(dir, 'output');
  t.after(() => rm(dir, {recursive: true, force: true}));
  await writeFile(payments, paymentHeader); await writeFile(invoices, Buffer.alloc(LIMITS.bytes + 1, 32));
  const args = ['--invoices', invoices, '--payments', payments, '--as-of', SAMPLE_AS_OF, '--output', output];
  let result = cli(args); assert.equal(result.status, 2); assert.doesNotMatch(result.stderr, /private-name/); assert.equal((await readdir(dir)).includes('output'), false);
  result = cli(args.map(arg => arg === invoices ? path.join(dir, 'private-missing.csv') : arg)); assert.equal(result.status, 1); assert.match(result.stderr, /ENOENT/); assert.doesNotMatch(result.stderr, /private-missing/);
});

test('an existing writer lock prevents any promotion and is never removed by a second writer', async t => {
  const dir = await mkdtemp(path.join(tmpdir(), 'finance-import-lock-'));
  t.after(() => rm(dir, {recursive: true, force: true}));
  const invoices = path.join(dir, 'invoices.csv'), payments = path.join(dir, 'payments.csv'), output = path.join(dir, 'accepted');
  await writeFile(invoices, SAMPLE_INVOICES); await writeFile(payments, SAMPLE_PAYMENTS); await writeFile(`${output}.lock`, 'existing-writer');
  const result = cli(['--invoices', invoices, '--payments', payments, '--as-of', SAMPLE_AS_OF, '--output', output]);
  assert.equal(result.status, 1); assert.match(result.stderr, /EEXIST/);
  assert.equal(await readFile(`${output}.lock`, 'utf8'), 'existing-writer'); assert.equal((await readdir(dir)).includes('accepted'), false);
  assert.equal((await readdir(dir)).some(name => name.startsWith('.finance-stage-')), false);
});

test('browser private data path has no network/storage/eval sink and a restrictive CSP', async () => {
  const app = await readFile(new URL('docs/import/app.mjs', root), 'utf8'), html = await readFile(new URL('docs/import/index.html', root), 'utf8'), css = await readFile(new URL('docs/import/workbench.css', root), 'utf8');
  assert.doesNotMatch(app, /\b(fetch|XMLHttpRequest|WebSocket|sendBeacon|localStorage|sessionStorage|indexedDB|innerHTML|eval)\b/);
  assert.match(html, /connect-src 'none'/); assert.doesNotMatch(css, /https?:/);
  assert.match(app, /errors\.slice\(0, 100\)/);
});
