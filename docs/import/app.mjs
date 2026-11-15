import {LIMITS, HEADERS, encode, validateImport, buildReport, artifacts} from './core.mjs';
import {SAMPLE_AS_OF, SAMPLE_INVOICES, SAMPLE_PAYMENTS} from './sample.mjs';
import {ENGINE_SHA256} from './identity.mjs';

const $ = id => document.getElementById(id);
let accepted = null, sampleInput = false, page = 0, busy = false;
const pageSize = 50;
const money = value => new Intl.NumberFormat('en-GB', {style: 'currency', currency: 'GBP'}).format(value / 100);
const sha256 = async bytes => [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(n => n.toString(16).padStart(2, '0')).join('');
const setText = (id, text) => { $(id).textContent = text; };

function stale(reason) {
  if (!accepted) return;
  setText('result-state', `Previous accepted snapshot · as of ${accepted.report.as_of}. ${reason} Downloads still belong to that previous snapshot.`);
  $('result-state').classList.add('stale');
}
function setBusy(value) {
  busy = value;
  for (const id of ['sample', 'reconcile', 'validate', 'clear', 'invoices-file', 'payments-file', 'as-of']) $(id).disabled = value;
  $('input-title').parentElement.parentElement.parentElement.setAttribute('aria-busy', String(value));
}
function showErrors(errors) {
  $('error-panel').hidden = false;
  setText('error-summary', `${errors.length} validation error${errors.length === 1 ? '' : 's'}. Record 1 is the header; record numbers count CSV records, not physical lines. Showing up to 100 errors. No new result was accepted.`);
  $('errors').replaceChildren();
  for (const error of errors.slice(0, 100)) {
    const li = document.createElement('li');
    li.textContent = `${error.file} · ${error.record ? `record ${error.record}` : 'file/settings'} · ${error.field}: ${error.message}`;
    $('errors').append(li);
  }
  setText('status', 'Import rejected. Correct the records and retry. Your previous accepted result, if any, is preserved.');
  stale('The replacement was rejected.');
}
async function readInputs() {
  if (sampleInput) return {invoices: encode(SAMPLE_INVOICES), payments: encode(SAMPLE_PAYMENTS), asOf: $('as-of').value};
  const input = {asOf: $('as-of').value};
  for (const name of Object.keys(HEADERS)) {
    const file = $(`${name}-file`).files[0];
    if (!file) throw {inputError: {file: name, record: 0, field: 'file', message: 'Select a CSV file. Payments may contain the header only.'}};
    if (file.size > LIMITS.bytes) throw {inputError: {file: name, record: 0, field: 'file', message: 'File exceeds 2 MiB.'}};
    input[name] = new Uint8Array(await file.arrayBuffer());
  }
  return input;
}
async function run(validateOnly = false) {
  if (busy) return;
  setBusy(true); $('error-panel').hidden = true;
  setText('status', validateOnly ? 'Validating the selected snapshot…' : 'Reading, validating and reconciling locally…');
  // Let the loading state paint before bounded parsing and reconciliation.
  await new Promise(resolve => setTimeout(resolve, 0));
  try {
    const input = await readInputs();
    if (validateOnly) {
      const validation = validateImport(input);
      if (!validation.ok) { showErrors(validation.errors); return; }
      const s = validation.stats;
      setText('status', `Validation passed: ${s.invoices.unique_records} unique invoices, ${s.payments.unique_records} unique payments; ${s.invoices.duplicate_records + s.payments.duplicate_records} identical retries collapsed. No report was generated. Choose Reconcile snapshot to accept it.`);
      stale('The selected inputs were validated only; no replacement report was generated.');
      return;
    }
    const result = await buildReport(input, sha256, ENGINE_SHA256);
    if (!result.ok) { showErrors(result.errors); return; }
    // Commit only after every validation, reconciliation and digest succeeds.
    accepted = {report: result.report, synthetic: sampleInput};
    page = 0; $('search').value = ''; $('balance-filter').value = 'all'; $('trace').hidden = true;
    drawReport();
    setText('status', `Accepted ${sampleInput ? 'synthetic sample' : 'local files'} as of ${input.asOf}. All four reconciliation checks passed.`);
    $('result-title').setAttribute('tabindex', '-1'); $('result-title').focus();
  } catch (error) {
    showErrors([error.inputError || {file: 'import', record: 0, field: 'processing', message: 'Could not complete the import. Reopen on HTTPS or localhost, verify the files are readable, and retry.'}]);
  } finally { setBusy(false); }
}
function drawReport() {
  const r = accepted.report, s = r.provenance.inputs;
  $('empty').hidden = true; $('result').hidden = false;
  setText('source-kind', accepted.synthetic ? 'Synthetic sample' : 'Your local files');
  setText('result-state', `Accepted snapshot · as of ${r.as_of} inclusive · ${accepted.synthetic ? 'synthetic example' : 'local CSV inputs'}.`);
  $('result-state').classList.remove('stale');
  setText('counts', `${r.counts.invoices} invoices · ${r.counts.payments} payments · ${r.counts.customers} customers included. ${s.invoices.duplicate_records} duplicate invoice records and ${s.payments.duplicate_records} duplicate payment records collapsed. ${r.counts.future_invoices} later invoices and ${r.counts.future_payments} later payments excluded.`);
  $('metrics').replaceChildren();
  for (const [key, label] of [['unpaid_minor', 'Gross unpaid'], ['overdue_minor', 'Of which overdue'], ['overpaid_minor', 'Invoice overpayments'], ['balance_minor', 'Net balance']]) {
    const div = document.createElement('div'), strong = document.createElement('strong'), span = document.createElement('span');
    div.className = 'metric'; strong.textContent = money(r.totals[key]); span.textContent = label; div.append(strong, span); $('metrics').append(div);
  }
  setText('bridge', `${money(r.totals.amount_minor)} invoiced − ${money(r.totals.paid_minor)} received = ${money(r.totals.balance_minor)} net. ${money(r.totals.unpaid_minor)} unpaid − ${money(r.totals.overpaid_minor)} overpaid = the same net.`);
  setText('checks', `${r.checks.filter(c => c.passed).length}/4 checks passed. Overpayments remain attached to their invoice; they do not hide another invoice’s unpaid balance.`);
  setText('identity', `Snapshot SHA-256: ${r.provenance.snapshot_sha256}\nCalculation engine SHA-256: ${r.provenance.engine_sha256}`);
  setText('input-hashes', `Invoices (${s.invoices.bytes} bytes): ${s.invoices.sha256}\nPayments (${s.payments.bytes} bytes): ${s.payments.sha256}`);
  drawInvoices();
}
function drawInvoices() {
  if (!accepted) return;
  const query = $('search').value.toLowerCase(), status = $('balance-filter').value;
  const rows = accepted.report.invoices.filter(row => `${row.invoice_id} ${row.customer_id}`.toLowerCase().includes(query) && (status === 'all' || row.status === status));
  const pages = Math.max(1, Math.ceil(rows.length / pageSize)); page = Math.min(page, pages - 1);
  $('invoice-rows').replaceChildren();
  for (const row of rows.slice(page * pageSize, (page + 1) * pageSize)) {
    const tr = document.createElement('tr');
    const values = [row.invoice_id, row.due_date, money(row.amount_minor), money(row.paid_minor), money(row.unpaid_minor), money(row.overpaid_minor), row.status];
    values.forEach((value, index) => { const td = document.createElement('td'); td.textContent = value;
      if (!index) { const small = document.createElement('small'); small.textContent = row.customer_id; td.append(small); } tr.append(td); });
    const td = document.createElement('td'), button = document.createElement('button'); button.type = 'button'; button.className = 'button'; button.textContent = 'Trace'; button.setAttribute('aria-label', `Trace invoice ${row.invoice_id}`); button.addEventListener('click', () => trace(row)); td.append(button); tr.append(td); $('invoice-rows').append(tr);
  }
  if (!rows.length) { const tr = document.createElement('tr'), td = document.createElement('td'); td.colSpan = 8; td.textContent = accepted.report.counts.invoices ? 'No invoices match these filters.' : 'No invoices were issued by this as-of date. Later records remain in provenance.'; tr.append(td); $('invoice-rows').append(tr); }
  setText('page-status', `${rows.length} matching invoices · page ${page + 1} of ${pages} · up to 50 rows per page`);
  $('previous').disabled = page === 0; $('next').disabled = page + 1 === pages;
}
function trace(row) {
  const r = accepted.report, customer = r.customers.find(c => c.customer_id === row.customer_id);
  const source = r.provenance.records.invoices.find(record => record.id === row.invoice_id);
  const sourceList = records => records.slice(0, 100).join(', ') + (records.length > 100 ? ` … (${records.length} total; complete list in JSON)` : '');
  setText('trace-title', `${row.invoice_id} · ${row.customer_id}`);
  setText('trace-summary', `${money(row.amount_minor)} invoiced − ${money(row.paid_minor)} allocated payments = ${money(row.balance_minor)} balance. Unpaid ${money(row.unpaid_minor)}; overpaid ${money(row.overpaid_minor)}; overdue ${money(row.overdue_minor)}. Issued ${row.issued_date}; due ${row.due_date}.`);
  setText('trace-customer', `Customer ${customer.customer_id}: ${customer.invoice_count} included invoices, ${money(customer.amount_minor)} invoiced − ${money(customer.paid_minor)} paid = ${money(customer.balance_minor)} net. Gross unpaid ${money(customer.unpaid_minor)}; overpaid ${money(customer.overpaid_minor)}. Search this customer ID above to inspect all their invoices.`);
  setText('trace-source', `Invoice source records: ${sourceList(source.records)}. Record 1 is the header; identical retries contribute only once. Source file fingerprints are in Snapshot identity below.`);
  $('trace-payments').replaceChildren();
  const paymentById = new Map(r.payments.map(p => [p.payment_id, p]));
  const sourceById = new Map(r.provenance.records.payments.map(p => [p.id, p]));
  for (const id of row.payment_ids.slice(0, 100)) {
    const payment = paymentById.get(id), source = sourceById.get(id), li = document.createElement('li');
    li.textContent = `${id} · ${payment.paid_date} · ${money(payment.amount_minor)} · payment source records ${sourceList(source.records)}`; $('trace-payments').append(li);
  }
  if (row.payment_ids.length > 100) { const li = document.createElement('li'); li.textContent = `Showing the first 100 of ${row.payment_ids.length} payments. Download the included payments CSV or JSON for all records.`; $('trace-payments').append(li); }
  if (!row.payment_ids.length) { const li = document.createElement('li'); li.textContent = 'No payments included through this as-of date.'; $('trace-payments').append(li); }
  $('trace').hidden = false; $('trace').focus();
}
function download(name) {
  if (!accepted) return;
  const contents = artifacts(accepted.report)[name], blob = new Blob([contents], {type: name.endsWith('.json') ? 'application/json' : 'text/csv;charset=utf-8'});
  const link = document.createElement('a'), url = URL.createObjectURL(blob);
  link.href = url; link.download = `finance-${accepted.report.as_of}-${accepted.report.provenance.snapshot_sha256.slice(0, 12)}-${name}`;
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
for (const name of Object.keys(HEADERS)) $(`${name}-file`).addEventListener('change', () => {
  if (sampleInput) { sampleInput = false; for (const other of Object.keys(HEADERS)) setText(`${other}-name`, $(`${other}-file`).files[0]?.name || 'No file selected'); }
  setText(`${name}-name`, $(`${name}-file`).files[0]?.name || 'No file selected'); stale('Selected inputs have changed.');
});
$('as-of').addEventListener('change', () => stale('The as-of date has changed.'));
$('sample').addEventListener('click', () => { sampleInput = true; $('as-of').value = SAMPLE_AS_OF;
  for (const name of Object.keys(HEADERS)) { $(`${name}-file`).value = ''; setText(`${name}-name`, `Synthetic sample ${name}.csv`); } stale('The synthetic sample is being loaded.'); run(); });
$('reconcile').addEventListener('click', () => run()); $('validate').addEventListener('click', () => run(true));
$('clear').addEventListener('click', () => { accepted = null; sampleInput = false; page = 0;
  for (const name of Object.keys(HEADERS)) { $(`${name}-file`).value = ''; setText(`${name}-name`, 'No file selected'); }
  $('result').hidden = true; $('empty').hidden = false; $('error-panel').hidden = true;
  for (const id of ['invoice-rows', 'trace-payments', 'metrics', 'errors']) $(id).replaceChildren();
  for (const id of ['trace-title', 'trace-summary', 'trace-customer', 'trace-source', 'identity', 'input-hashes', 'bridge', 'counts']) setText(id, '');
  setText('status', 'Files and accepted result cleared from this tab. Downloaded files remain on your device.'); });
$('search').addEventListener('input', () => { page = 0; drawInvoices(); }); $('balance-filter').addEventListener('change', () => { page = 0; drawInvoices(); });
$('previous').addEventListener('click', () => { page--; drawInvoices(); }); $('next').addEventListener('click', () => { page++; drawInvoices(); });
for (const name of ['invoices', 'customers', 'payments', 'report']) $(`download-${name}`).addEventListener('click', () => download(`${name}.${name === 'report' ? 'json' : 'csv'}`));
