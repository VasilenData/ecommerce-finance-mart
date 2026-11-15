# Trace a usage correction into a reconciled finance balance

**Problem:** a retried delivery or corrected usage statement must not silently double invoice totals, erase the original close or offset unpaid exposure with an unrelated credit.

[View the result](https://vasilendata.github.io/ecommerce-finance-mart/meterwork-bridge/2026-11-15/) · [Raw evidence](meterwork-bridge/2026-11-15/evidence.json) · [Receiver](../mart/meterwork_bridge.py) · [Producer contract](https://github.com/VasilenData/usage-metering-platform/blob/main/docs/FINANCE_BRIDGE.md)

## Observed scope and result

On 15 November 2026 the receiver consumed two immutable bundles produced by Meterwork's actual loopback HTTP API and restricted PostgreSQL runtime. The accounts and usage are synthetic. Finance's payment/refund snapshot is explicitly simulated; neither application collected money or issued invoices.

The receiver imports **no Meterwork rating code**. It recomputes each statement from exported source events, prices and its original knowledge cutoff, then compares line items, units, monetary amounts and source fingerprints. A separate SQL calculation derives invoice balances. Literal hand calculations provide an additional oracle.

| USD cents, unless labelled | Original release | Corrected release |
|---|---:|---:|
| Latest invoice projections | 5,450 | 5,300 |
| Simulated payments | 4,500 | 4,500 |
| Simulated refunds | 200 | 200 |
| Gross unpaid | 1,450 | 1,300 |
| Customer credits | 300 | 300 |
| Net balance | 1,150 | 1,000 |
| Retained residual, microUSD | 1,550 | 3,050 |

The **30-check** exercise includes eight rejected deliveries: missing input, corrupt bytes, duplicate statement identity, missing revision, sequence gap, wrong currency, altered monetary amount and an injected failure between insertion and promotion. Each leaves the previous report hash unchanged and creates a visible exception. The original release remains queryable after correction; replaying it does not roll the active release backwards.

The correction changes raw charges by -1,498,500 microUSD. The -150-cent invoice change plus the +1,500-microUSD residual change reconciles exactly: `-150 * 10,000 + 1,500 = -1,498,500`.

## Publication and financial definitions

- Contract: `meterwork-finance/v1`; one immutable directory contains accounts, statements, source snapshots and a manifest. Cumulative statement revisions must be contiguous and preserve old records.
- The caller supplies the expected bundle SHA-256. File hashes and manifest checks detect drift against that trusted expectation. They are not signatures or a substitute for authenticating a producer.
- A delivery advances exactly one sequence and names its predecessor. Identical whole-bundle replay is idempotent; duplicate logical records within a bundle are rejected.
- The latest account/month revision becomes the current invoice projection. Prior revisions are retained as history and are never summed as current revenue.
- USD statement amounts remain integer microUSD. The scenario rounds once at the nonnegative statement total, half-up, and retains the signed residual. There is no FX conversion and no combination with the historical GBP data.
- Each invoice's unpaid and credit positions are calculated before customer aggregation. Net balance equals gross unpaid minus credits. Refunds reduce simulated collected cash.
- A DuckDB transaction inserts the validated immutable report and moves the active pointer together. Exceptions are recorded after rollback. Consumers receive one complete report document.

The isolated schema is `meterwork_usd`. Sentinel checks show that the existing GBP historical and v1/v2 subscription relations are untouched.

## Reproduce

```sh
pip install -r requirements-dev.txt
python -m pytest -q tests/test_meterwork_bridge.py
python -m mart.meterwork_bridge_exercise --output local-evidence/meterwork-bridge
```

The output directory must be new. The exercise reads the checked-in producer bundles in `fixtures/meterwork-bridge/2026-11-15`, writes the original and corrected reports, the explicit collection fixture and every rejection receipt. The producer provenance includes actual HTTP observations and source fingerprints. Generate new bundles with Meterwork's documented PostgreSQL exercise when testing the producer again.

CI reruns this receiver exercise, validates the checked-in evidence and retains fresh outputs as artifacts. It does not call a hosted billing system.

## Boundaries

This is a sequential single-writer receiver with two synthetic accounts and a fixed collection snapshot. Cash changes require a future versioned contract; the current comparison deliberately rejects them. Per-account knowledge cutoffs do not establish a global source transaction. SHA-256 does not protect against a trusted producer rewriting both source data and its manifest. The test does not prove machine power-loss recovery, concurrent publishers, production throughput, managed cloud operation or Databricks parity. Implementation and review are AI-assisted.
