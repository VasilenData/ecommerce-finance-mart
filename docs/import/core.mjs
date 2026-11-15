// Shared browser/Node contract. No network, filesystem, storage or DOM access.
export const CONTRACT = 'finance-local-import/v1';
export const LIMITS = Object.freeze({bytes: 2 * 1024 * 1024, records: 10000, cell: 256, amount: 100000000000});
export const HEADERS = Object.freeze({
  invoices: ['invoice_id', 'customer_id', 'currency', 'issued_date', 'due_date', 'amount_minor'],
  payments: ['payment_id', 'invoice_id', 'paid_date', 'amount_minor'],
});
const encoder = new TextEncoder();
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const issue = (file, record, field, message) => ({file, record, field, message});

// RFC-style quoted cells, doubled quotes and CRLF/LF. Fail closed on ambiguous CSV.
export function parseCSV(bytes, file) {
  const errors = [];
  const fail = (record, field, message) => ({rows: [], errors: [issue(file, record, field, message)]});
  if (!(bytes instanceof Uint8Array)) return fail(0, 'file', 'Expected UTF-8 file bytes.');
  if (bytes.byteLength > LIMITS.bytes) return fail(0, 'file', 'File exceeds 2 MiB.');
  let text;
  try { text = new TextDecoder('utf-8', {fatal: true}).decode(bytes); }
  catch { return fail(0, 'encoding', 'Save as UTF-8 CSV; invalid UTF-8 bytes found.'); }
  if (!text.length) return fail(1, 'header', 'File is empty; include the exact header.');
  let row = [], cell = '', quoted = false, closed = false, rows = [];
  const record = () => rows.length + 1;
  const field = () => HEADERS[file]?.[row.length] || `column_${row.length + 1}`;
  const endCell = () => { row.push(cell); cell = ''; closed = false; };
  const endRow = () => { endCell(); rows.push(row); row = []; };
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++; }
        else { quoted = false; closed = true; }
      } else cell += ch;
    } else if (ch === ',') endCell();
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] !== '\n') return fail(record(), field(), 'Use LF or CRLF line endings.');
      if (ch === '\r') i++;
      endRow();
    } else if (closed) return fail(record(), field(), 'Unexpected text after a closing quote.');
    else if (ch === '"') {
      if (cell.length) return fail(record(), field(), 'A quote must start a cell; double embedded quotes.');
      quoted = true;
    } else cell += ch;
    if (cell.length > LIMITS.cell) return fail(record(), field(), 'Cell exceeds 256 characters.');
    if (row.length > HEADERS[file].length) return fail(record(), 'columns', 'Too many columns.');
    if (rows.length > LIMITS.records + 1) return fail(record(), 'file', 'File exceeds 10,000 data records.');
  }
  if (quoted) return fail(record(), field(), 'Quoted cell is not closed.');
  if (cell.length || row.length || closed) endRow();
  if (rows.length > LIMITS.records + 1) return fail(record(), 'file', 'File exceeds 10,000 data records.');
  if (JSON.stringify(rows[0]) !== JSON.stringify(HEADERS[file])) {
    errors.push(issue(file, 1, 'header', `Expected exactly: ${HEADERS[file].join(',')}`));
  }
  return {rows, errors};
}

export function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value < '1900-01-01') return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function validateImport({invoices, payments, asOf}) {
  const errors = [], data = {}, stats = {};
  if (!validDate(asOf)) errors.push(issue('settings', 0, 'as_of', 'Choose a real date in YYYY-MM-DD format, year 1900–9999.'));
  for (const [file, bytes] of Object.entries({invoices, payments})) {
    const parsed = parseCSV(bytes, file);
    errors.push(...parsed.errors);
    if (parsed.errors.length) continue;
    const unique = new Map();
    let duplicates = 0;
    parsed.rows.slice(1).forEach((cells, index) => {
      const record = index + 2, before = errors.length;
      const add = (field, message) => errors.push(issue(file, record, field, message));
      if (cells.length !== HEADERS[file].length) { add('columns', `Expected ${HEADERS[file].length} cells; found ${cells.length}.`); return; }
      const row = Object.fromEntries(HEADERS[file].map((name, i) => [name, cells[i]]));
      for (const field of HEADERS[file]) {
        const value = row[field];
        if (field.endsWith('_id') && !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value))
          add(field, 'Use 1–64 ASCII letters/digits, dot, underscore or hyphen; start with a letter/digit.');
        else if (field.endsWith('_date') && !validDate(value))
          add(field, 'Use a real calendar date YYYY-MM-DD, year 1900–9999.');
        else if (field === 'currency' && value !== 'GBP') add(field, 'Only GBP is supported; do not convert silently.');
        else if (field === 'amount_minor' && (!/^[1-9]\d{0,11}$/.test(value) || Number(value) > LIMITS.amount))
          add(field, 'Use positive integer pence, 1–100000000000; no decimals, signs or leading zeros.');
      }
      if (file === 'invoices' && validDate(row.issued_date) && validDate(row.due_date) && row.due_date < row.issued_date)
        add('due_date', 'Due date cannot precede issued_date.');
      if (errors.length !== before) return;
      const id = row[HEADERS[file][0]], previous = unique.get(id);
      if (previous && JSON.stringify(previous.values) !== JSON.stringify(row)) {
        add(HEADERS[file][0], `Conflicting retry; this ID first appeared at record ${previous.source_records[0]}. Correct the snapshot.`);
      } else if (previous) { previous.source_records.push(record); duplicates++; }
      else unique.set(id, {values: row, source_records: [record]});
    });
    if (file === 'invoices' && parsed.rows.length === 1) errors.push(issue(file, 2, 'invoice_id', 'Include at least one invoice.'));
    data[file] = [...unique.values()].sort((a, b) => compare(a.values[HEADERS[file][0]], b.values[HEADERS[file][0]]));
    stats[file] = {input_records: parsed.rows.length - 1, unique_records: unique.size, duplicate_records: duplicates};
  }
  if (!errors.length) {
    const byId = new Map(data.invoices.map(row => [row.values.invoice_id, row.values]));
    for (const row of data.payments) {
      const invoice = byId.get(row.values.invoice_id);
      if (!invoice) errors.push(issue('payments', row.source_records[0], 'invoice_id', 'No matching invoice in this snapshot.'));
      else if (row.values.paid_date < invoice.issued_date)
        errors.push(issue('payments', row.source_records[0], 'paid_date', 'Payment cannot precede the invoice issued_date; prepayments are outside v1.'));
    }
  }
  return {ok: !errors.length, errors, data, stats, asOf};
}

export function reconcile(validation) {
  if (!validation.ok) throw new Error('Validation must pass before reconciliation.');
  const {data, asOf} = validation;
  const paidByInvoice = new Map();
  const paymentRows = data.payments.filter(row => row.values.paid_date <= asOf).map(row => ({...row.values, amount_minor: Number(row.values.amount_minor)}));
  for (const payment of paymentRows) {
    const current = paidByInvoice.get(payment.invoice_id) || {amount: 0, ids: []};
    current.amount += payment.amount_minor; current.ids.push(payment.payment_id);
    paidByInvoice.set(payment.invoice_id, current);
  }
  const invoiceRows = data.invoices.filter(row => row.values.issued_date <= asOf).map(row => {
    const values = row.values, amount = Number(values.amount_minor);
    const paid = paidByInvoice.get(values.invoice_id) || {amount: 0, ids: []};
    const balance = amount - paid.amount, unpaid = Math.max(balance, 0);
    return {...values, amount_minor: amount, paid_minor: paid.amount, balance_minor: balance,
      unpaid_minor: unpaid, overpaid_minor: Math.max(-balance, 0),
      overdue_minor: values.due_date < asOf ? unpaid : 0,
      status: balance < 0 ? 'overpaid' : balance === 0 ? 'settled' : values.due_date < asOf ? 'overdue' : 'open',
      payment_ids: paid.ids};
  });
  const sumFields = ['amount_minor', 'paid_minor', 'balance_minor', 'unpaid_minor', 'overpaid_minor', 'overdue_minor'];
  const empty = () => Object.fromEntries(sumFields.map(field => [field, 0]));
  const total = empty(), customers = new Map();
  for (const row of invoiceRows) {
    const customer = customers.get(row.customer_id) || {customer_id: row.customer_id, currency: 'GBP', invoice_count: 0, ...empty()};
    customer.invoice_count++;
    for (const field of sumFields) { total[field] += row[field]; customer[field] += row[field]; }
    customers.set(row.customer_id, customer);
  }
  const checks = [
    {name: 'invoice less cash equals net balance', passed: total.amount_minor - total.paid_minor === total.balance_minor},
    {name: 'unpaid less overpaid equals net balance', passed: total.unpaid_minor - total.overpaid_minor === total.balance_minor},
    {name: 'allocated payments equal received cash', passed: paymentRows.reduce((sum, row) => sum + row.amount_minor, 0) === total.paid_minor},
    {name: 'overdue is contained in gross unpaid', passed: total.overdue_minor <= total.unpaid_minor},
  ];
  if (checks.some(check => !check.passed)) throw new Error('Internal reconciliation failed; no result accepted.');
  return {contract: CONTRACT, as_of: asOf, currency: 'GBP', totals: total,
    counts: {invoices: invoiceRows.length, payments: paymentRows.length, customers: customers.size,
      future_invoices: data.invoices.length - invoiceRows.length, future_payments: data.payments.length - paymentRows.length},
    invoices: invoiceRows, payments: paymentRows,
    customers: [...customers.values()].sort((a, b) => compare(a.customer_id, b.customer_id)), checks};
}

export async function buildReport(input, sha256, engineSha256) {
  const validation = validateImport(input);
  if (!validation.ok) return {ok: false, errors: validation.errors};
  const report = reconcile(validation);
  // Semantic identity excludes source ordering/duplicate copies; raw fingerprints retain both.
  const canonical = JSON.stringify({contract: CONTRACT, as_of: input.asOf,
    invoices: validation.data.invoices.map(row => row.values), payments: validation.data.payments.map(row => row.values)});
  report.provenance = {
    engine_sha256: engineSha256,
    snapshot_sha256: await sha256(encoder.encode(canonical)),
    inputs: {
      invoices: {sha256: await sha256(input.invoices), bytes: input.invoices.length, ...validation.stats.invoices},
      payments: {sha256: await sha256(input.payments), bytes: input.payments.length, ...validation.stats.payments},
    },
    records: Object.fromEntries(Object.entries(validation.data).map(([file, rows]) =>
      [file, rows.map(row => ({id: row.values[HEADERS[file][0]], records: row.source_records}))])),
  };
  return {ok: true, report};
}

// Formula-safe spreadsheet cells, even if future contracts permit free text.
export function csvCell(value) {
  let text = Array.isArray(value) ? value.join(';') : String(value ?? '');
  if (/^[\s\uFEFF]*[=+@-]/u.test(text) && typeof value !== 'number') text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}
export function exportCSV(rows, columns) {
  return [columns, ...rows.map(row => columns.map(column => row[column]))].map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
}
export const INVOICE_EXPORT = [...HEADERS.invoices, 'paid_minor', 'balance_minor', 'unpaid_minor', 'overpaid_minor', 'overdue_minor', 'status', 'payment_ids'];
export const CUSTOMER_EXPORT = ['customer_id', 'currency', 'invoice_count', 'amount_minor', 'paid_minor', 'balance_minor', 'unpaid_minor', 'overpaid_minor', 'overdue_minor'];
export const encode = text => encoder.encode(text);
export function artifacts(report) {
  return {
    'report.json': JSON.stringify(report, null, 2) + '\n',
    'invoices.csv': exportCSV(report.invoices, INVOICE_EXPORT),
    'customers.csv': exportCSV(report.customers, CUSTOMER_EXPORT),
    'payments.csv': exportCSV(report.payments, HEADERS.payments),
  };
}
