# A safe, opt-in invoice schema migration

The existing dbt `invoice_balances` exposes a signed `balance_cents`. That value is a net position: a credit on one invoice can hide another invoice's unpaid amount when a consumer aggregates it. The review explorer already distinguishes these concepts. This change gives warehouse consumers that same distinction without breaking their existing column names or changing the ledger.

**Scope:** synthetic fixtures in local DuckDB and a separate disposable PostgreSQL database. This is a model/output contract migration. The four source contracts, MRR definitions and existing historical retail/subscription downloads are unchanged. It is not a new Databricks run, managed warehouse deployment or stakeholder approval.

[Dated execution evidence](schema-migration/2026-11-14/) records **14 migration checks and four dbt builds per adapter**, with complete release rows equal between DuckDB 2.0.0 and PostgreSQL 17.11. The existing nine-build, 39-check regression also passed on both. Local repository checks passed 196 Python tests (one unchanged historical-data test skipped), 14 JavaScript tests and lint. Hosted CI for this change has not run; the separate Databricks manual result remains pending.

## Contract and migration plan

| Boundary | Before / preserved | Opt-in change |
|---|---|---|
| Grain and units | One selected invoice, integer GBP pence, original invoice/customer keys | Unchanged |
| Time | Business dates before the exclusive reporting end; highest revision known at the UTC cutoff | Unchanged; frozen source membership is also retained for historical reproduction |
| Version 1 | `invoice_id, customer_id, invoiced_cents, paid_cents, refunded_cents, balance_cents` | Exact column order, values and existing relation name retained |
| Version 2 | First five columns retained | `net_balance_cents`, `gross_unpaid_cents`, `credit_cents` replace the ambiguous signed field |
| Meaning | Signed balance = invoiced − paid + refunded | Gross unpaid = max(net, 0); credit = max(−net, 0), **per invoice before aggregation** |
| dbt consumer | Existing `ref('invoice_balances')` resolves to version 1 | Explicit `ref('invoice_balances', v=2)`; `latest_version` remains 1 |
| Published consumer | Stable v1 `invoice_balances` and three other existing mart contracts | Separate `invoice_balances_v2`; no silent column rename in an existing view |

1. **Expand:** add native dbt model version 2. Preserve version 1 as the default and test both in the same graph. Do not force all consumers onto the renamed column.
2. **Validate a candidate:** execute dbt in an isolated schema, freeze all four original marts and the v2 invoice mart into physical candidate tables, inspect exact ordered columns and integer money, and compare every row with the independent Python oracle. Invalid candidates never enter the release registry.
3. **Promote:** recheck the frozen row fingerprint, then replace all five stable consumer views and the active-release pointer in one database transaction. Explicit column projections preserve the v1 contract. The candidate's source JSON and knowledge cutoff stay in the registry.
4. **Recover:** a failure after actual view replacement rolls the transaction back. A prior validated release can be promoted again; rollback checks its snapshot fingerprint too. Old candidate tables are retained until the disposable exercise is cleaned up.
5. **Reproduce a close:** rebuild from that release's frozen source and cutoff in a fresh warehouse. A knowledge cutoff alone is insufficient after a late delivery with an older recorded timestamp; preserving the actual source membership prevents the original close from silently changing.
6. **Contract:** no v1 removal or default-version switch is performed. A real retirement needs an inventory of consumers and a communicated migration window. This exercise does not invent either.

The publisher is deliberately separate from `dbt build`: dbt itself does not give this project an atomic multi-model publication. The supplied **multi-query** consumer pins the active release ID and its immutable physical schema in one registry query, then reads only that schema. Repeatable-read isolation alone must not be assumed to pin mutable view definitions. The stable views are convenience endpoints; independent queries against them can cross a successful promotion. A deterministic two-connection exercise pauses the reader after pinning, commits a promotion, then verifies that the paused reader still returns its original complete release.

## Acceptance evidence

The raw hand-calculated fixture has two invoices for the same customer. Invoice 1 bills £100, receives £120 and refunds £5: credit £15. Invoice 2 bills £50 and receives £10: gross unpaid £40. The net position is £25; calling it £25 unpaid would hide £15 of exposure.

A delayed £15 payment against invoice 2 and a later revision changing the first refund to £10 produce £25 unpaid, £10 credit and £15 net. The late payment has an older recorded timestamp than the existing refund. The existing ingestion-sequence path must still include it.

The dedicated exercise checks:

- Both hand-computed results and complete mart rows against the Python arithmetic oracle.
- Exact v1 compatibility while v2 exposes the distinct amounts.
- A new dbt build cannot mutate a previously published physical snapshot.
- A deliberately corrupted candidate is rejected without changing a published row or pointer.
- Incremental backfill and full refresh produce identical complete row sets.
- Failure after the second view replacement preserves the prior canonical output bytes and active pointer.
- Successful promotion switches both consumer contracts together; rollback restores the original bytes.
- A fresh database build from the frozen source reproduces the historical close's canonical row bytes.

Focused tests also interrupt each of the five view-replacement boundaries, reject column drift and reject a snapshot changed after validation. Canonical-byte equality means deterministic JSON content, not equality of physical database files or Excel internals.

The pre-existing nine-scenario dbt exercise remains in CI. It already proved transport replay, cutoff changes, late backfill, full refresh and metric-definition effects; those are not claimed as newly added capabilities here.

## Run and inspect

Use the pinned [dbt requirements](../requirements-dbt.txt). The commands create isolated schemas and a new evidence directory, then remove only their own database schemas.

```bash
python -m pytest -q tests/test_schema_migration.py
python -m mart.schema_migration --adapter duckdb --output dist/migration-duckdb
# FINANCE_TEST_DATABASE_URL must identify a dedicated database ending in _test.
python -m mart.schema_migration --adapter postgres --output dist/migration-postgres
```

Each evidence bundle includes the baseline/changed synthetic sources, source file fingerprints, actual dbt node statuses, complete release rows and passing assertions. The [two-adapter workflow](../.github/workflows/dbt-warehouse.yml) compares both release contents exactly and rejects mismatched fingerprints. Failed dbt logs remain local under ignored `dist/private-dbt-failures/`.

## Limits

- One ingestion and publication writer; no concurrent publisher or distributed locking protocol is claimed.
- Snapshots are frozen by this workflow and fingerprinted before promotion. A database administrator can still change them; production grants are not demonstrated.
- A controlled exception after observed DDL tests transaction rollback. It is not a process-kill, power-loss or availability benchmark.
- Disposable schemas and bounded fixture outputs do not establish production scale, latency, cloud cost or real consumer adoption.
- Databricks hosted revalidation remains pending its separate manual notebook output.

## Delivery prompt and primary references

> Treat the model as a consumer API. Preserve the exact old column contract, units and ledger semantics while introducing one justified opt-in version. Freeze a source-bound candidate, reconcile every row against an independent oracle, and publish all related consumer pointers in one transaction. Inject a failure after an observed mutation and prove the previous release survives. Rebuild an original close from its actual source membership, not an assumed timestamp watermark. Execute both supported adapters; report scope, source identity and limits without inventing production use or stakeholder sign-off.

The approach uses [dbt model versions](https://docs.getdbt.com/docs/mesh/govern/model-versions), PostgreSQL's [view replacement contract](https://www.postgresql.org/docs/17/sql-createview.html) and [catalog/MVCC caveats](https://www.postgresql.org/docs/17/mvcc-caveats.html), and DuckDB's [transaction](https://duckdb.org/docs/stable/sql/statements/transactions) and [view](https://duckdb.org/docs/stable/sql/statements/create_view) behavior. The PostgreSQL caveat is why the multi-query reader pins an immutable physical schema rather than assuming isolation freezes view definitions. These are implementation references, not external reviews.
