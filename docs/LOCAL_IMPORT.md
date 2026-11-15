# Reconcile your local invoice and payment files

Open the [local invoice workbench](https://vasilendata.github.io/ecommerce-finance-mart/import/), try the synthetic sample, then choose two local CSVs and an as-of date. **Validate only** checks the selected files without changing the accepted report. **Reconcile snapshot** accepts a complete valid snapshot and shows invoice balances, customer totals, source records and downloads. A failed replacement leaves the last accepted result visible with a clear “previous accepted snapshot” label.

The hosted page performs all file reading, validation, hashing and arithmetic in your browser. There is no file upload, telemetry, remote font, application storage or hosted computation. Its content security policy blocks connections from the workbench. Files and results remain in tab memory until cleared/reloaded; browser downloads remain on your device. This does not control your browser extensions, operating system or backups. Use a current browser with JavaScript and Web Crypto on HTTPS or localhost. No installation is needed for the hosted workflow.

This is a new, separate `finance-local-import/v1` contract. It does **not** change the historical retail contract, the synthetic GBP subscription contract, or the synthetic USD Meterwork receiver. Examples are synthetic. No real private user files were supplied for the release; your own files can be processed when they satisfy this documented contract. There is no merchant-specific mapping or inferred currency conversion.

## Supported task and financial scope

Provide a **complete snapshot**, not a change feed. Invoice totals are the total receivable in pence, including any tax already included by your source; the tool does not derive tax. Payments are positive receipts already allocated to a single invoice. Several distinct payments can settle one invoice. One payment split across invoices must be supplied as independently identified allocations; this importer cannot detect whether your source duplicated the same bank receipt under different IDs.

- **Supported:** positive invoices, positive allocated payments, partial settlement, multiple payments, invoice overpayments, gross unpaid and overdue balances, customer rollups.
- **Excluded:** credit notes, negative/zero invoices, refunds, chargebacks, prepayments, unallocated receipts, FX, invoice lines, bank reconciliation, revenue recognition, tax calculation, balances carried from omitted records and source revision history. Do not omit a refund or credit and treat the resulting unsupported snapshot as complete.
- **Overpayment:** `max(paid − invoiced, 0)` per invoice. It stays attached to that invoice and never hides another invoice’s unpaid amount, even for the same customer. It is not a posted credit note.
- **Unpaid:** `max(invoiced − paid, 0)` per invoice. **Overdue:** unpaid amount where `due_date < as_of`; due today is not overdue. Net balance is unpaid minus overpaid.
- **As-of:** includes invoices issued on/before the date and payments paid on/before the date. Later dates are valid but excluded from amounts and counted separately. All rows, including later dates, must validate. Dates are calendar business dates with no time-of-day/time-zone conversion. This is **not knowledge-time reporting**: a file corrected today can change an earlier as-of result. Retain the original files and report if you need the original snapshot.

The importer cannot prove that your source is complete, that IDs correspond to real-world unique receipts, or that cash settled at a bank. It never merges into previous imports. Select both full files again to replace a snapshot.

## CSV contract

Download [invoice template](import/invoices-template.csv), [payment template](import/payments-template.csv), [synthetic invoices](import/sample-invoices.csv) and [synthetic payments](import/sample-payments.csv).

Files must be UTF-8 (optional BOM), comma-delimited, with the **exact ordered header**, no additional columns and no whitespace normalization. LF and CRLF are accepted. Quoted cells and doubled quotes follow CSV syntax; record numbers count parsed CSV records, including header record 1, not physical lines. Extra blank records and malformed quoting are rejected. A trailing line ending is allowed. An empty file is rejected; a payment file containing its header only explicitly means no payments. At least one invoice is required, although all invoices may be later than the selected date.

Invoices:

```csv
invoice_id,customer_id,currency,issued_date,due_date,amount_minor
INV-001,CEDAR,GBP,2026-03-01,2026-03-15,10000
```

| Field | Type and rule |
|---|---|
| `invoice_id` | Case-sensitive ID, unique after exact retries collapse |
| `customer_id` | Case-sensitive customer ID; repeated across invoices is expected |
| `currency` | Exactly `GBP` |
| `issued_date` | Real date, `YYYY-MM-DD`, years 1900–9999 |
| `due_date` | Same date type; must be on/after issued date |
| `amount_minor` | Positive integer pence, 1–100000000000 (£0.01–£1 billion) |

Payments:

```csv
payment_id,invoice_id,paid_date,amount_minor
PAY-001,INV-001,2026-03-12,3000
```

| Field | Type and rule |
|---|---|
| `payment_id` | Case-sensitive ID, unique after exact retries collapse |
| `invoice_id` | Must match an invoice in the same snapshot; inherits GBP |
| `paid_date` | Real date, `YYYY-MM-DD`, years 1900–9999; on/after that invoice’s issued date |
| `amount_minor` | Same positive integer pence type as invoices |

All IDs must match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. There are no free-text customer names. Amounts cannot contain signs, decimal points, thousands separators, exponent notation, leading zeros or spaces. £100 is `10000`, not `100.00`.

Each file is bounded to **2 MiB, 10,000 data records (including retries), and 256 characters per cell**. With a maximum 100000000000 pence per record, each file’s total is bounded to 1000000000000000 pence, below JavaScript’s exact integer limit of 9007199254740991. Financial arithmetic uses integer pence throughout. Only the UI converts pence to formatted pounds; CSV/JSON export exact integer units.

### Duplicate and retry semantics

Within each file, an identical ID and identical parsed field values is an exact retry: retain one record and count the duplicates. CSV quoting, BOM and line-ending differences do not alter parsed values. Source record numbers retain every occurrence. The same ID with any changed value is a **conflicting retry** and rejects the entire snapshot, even when the changed row is after the as-of date. IDs are not scoped by customer or date. Distinct IDs are distinct business records; the tool does not guess economic duplicates.

Reordering rows, removing exact replay copies or adding more exact copies preserves calculated rows and the semantic snapshot hash. It changes raw file fingerprints and record-number provenance. Changing a record or the as-of date changes the snapshot identity. Missing records in a new complete snapshot are absent from that snapshot; they are not retained implicitly.

## Reports and traceability

The workbench shows gross unpaid, overdue, overpaid and net balances, visible inclusion/exclusion/retry counts and four conservation checks. Search by invoice/customer and filter by status. The invoice table is paginated at 50 rows. **Trace** explains invoice arithmetic, customer totals, included payment IDs/dates/amounts and source record numbers. Trace displays at most 100 payments and 100 record numbers per identity, with explicit total counts when truncated. Downloads contain all included rows regardless of the current search/filter.

- `invoices.csv`: one included invoice per row, original fields, allocated payment total, net/unpaid/overpaid/overdue amounts, status and semicolon-delimited payment IDs.
- `customers.csv`: one included customer per row; sum of invoice amounts and exposures, without netting exposure between invoices.
- `payments.csv`: one included payment per row.
- `report.json`: the same totals and rows, checks, contract/as-of identity, SHA-256 of exact input bytes, byte/record counts, source record mappings (including later records), and calculation-engine SHA-256.

CSV exports quote all cells, double embedded quotes and prefix formula-looking text with an apostrophe defensively. IDs are restricted further by the contract. User strings are inserted into the browser using text nodes, never HTML. Validation messages report file role, record and field without echoing raw cell values. The UI displays at most 100 errors; fix those and validate again. The CLI emits the bounded complete error list.

The JSON is an audit report, not a backup of the full source: retain both original CSVs to reproduce later/excluded records. No filename, absolute path, processing timestamp or user identity is exported. The semantic snapshot hash binds every unique accepted source record and the as-of date. Raw SHA-256 fingerprints distinguish file bytes independently. The engine hash binds the arithmetic/parser implementation; it is not a claim of digital signing or an external audit.

## Local command (Node 22 or later, no packages)

The CLI shares the browser’s exact parser, validation and reconciliation engine. Node 24.22.1 was used for the local release verification; hosted CI uses Node 22. Run from the repository root. For a synthetic first run:

```bash
node scripts/import-finance.mjs --invoices docs/import/sample-invoices.csv --payments docs/import/sample-payments.csv --as-of 2026-03-31 --validate-only
node scripts/import-finance.mjs --invoices docs/import/sample-invoices.csv --payments docs/import/sample-payments.csv --as-of 2026-03-31 --output dist/my-first-import
```

For your own files, replace the two input paths. Keep private inputs outside the repository and use an ignored `dist/` output or a private directory outside the checkout. Never put private input, exports or screenshots into `docs/` or commit them. Both files must describe one coordinated source snapshot; reading two files is not a transaction with an external producer. The CLI reads bounded byte copies before processing.

The output directory **must be new**, including for a retry. The CLI writes and checksums a complete sibling staging directory, then renames it into the destination. `manifest.json` records exact checksums and lengths for every exported artifact plus engine and CLI source hashes. Successful repeated runs to separate destinations yield identical artifact bytes. Validation failures produce exit code 2; usage/filesystem failures produce exit code 1; success is 0. No output is published on a validation failure, and an existing directory is never overwritten.

A sibling `.lock` file serializes CLI writers targeting the same destination. Use a trusted local parent directory; non-cooperating external writers, unusual/network filesystem rename behavior and power-loss durability are not covered. Process death can leave a `.lock` and `.finance-stage-*` directory while preserving existing results. After verifying the old process has stopped, remove only those abandoned artifacts or choose a fresh destination; rerun from the two original files. There is no automatic deletion or replacement of an accepted result.

For a local browser preview, run `python -m http.server 8791 --directory docs` and visit `http://localhost:8791/import/`. Do not serve a directory containing private input or output. Opening `index.html` directly as a `file://` URL is not supported because browser module restrictions vary.

## Independent example and verification

At `2026-03-31`, the synthetic sample has four included invoices:

| Invoice | Invoiced | Paid | Unpaid | Overpaid | Overdue |
|---|---:|---:|---:|---:|---:|
| INV-001 | £100 | £50 (two payments) | £50 | £0 | £50 |
| INV-002 | £50 | £0 | £50 | £0 | £0 |
| INV-003 | £80 | £100 | £0 | £20 | £0 |
| INV-004 | £40 | £40 | £0 | £0 | £0 |
| **Total** | **£270** | **£190** | **£100** | **£20** | **£50** |

Hand arithmetic: £270 − £190 = £80 = £100 − £20. Customer CEDAR has £100 unpaid; LARK has £20 overpaid. One later invoice and one later payment are excluded. One exact invoice retry and one exact payment retry collapse. All sample names/records are synthetic. These expectations are independently asserted in the tests, alongside a separate case where one customer’s £100 unpaid and £100 overpaid must both remain visible at net zero.

```bash
node scripts/build-import.mjs --check
node --test tests_js/*.test.mjs
python -m ruff check .
python -m pytest -q
python -m mart.meterwork_bridge_exercise --output dist/meterwork-bridge
python -m mart.bridge_recovery --output dist/bridge-recovery
python -m mart.subscriptions --output dist/subscriptions
```

Tests cover independent arithmetic, inclusive cutoffs, dates, schema/UTF-8, input/aggregate bounds, exact/conflicting retries, permutations, unknown references, unsupported refunds/prepayments, malicious/formula cells, deterministic exports, source hashes, CLI validation/export/rerun, prior output preservation and browser acceptance/rejection state. The generated identity and sample/template assets are checked in CI; after changing the engine or samples run `node scripts/build-import.mjs`. Existing dbt DuckDB/PostgreSQL hosted equivalence and immutable historical receipts retain their original scope. A tested synthetic workflow does not establish user adoption, a timed user study or results on an unsupplied private dataset.

The task-specific [delivery prompt](USABILITY_DELIVERY_PROMPT.md) records the implementation brief and completion criteria.
