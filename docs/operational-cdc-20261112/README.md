# Recorded operational CDC exercise - 12 November 2026

[Successful hosted run](https://github.com/VasilenData/ecommerce-finance-mart/actions/runs/43272540136)
used real PostgreSQL 16.6, Debezium Connect 3.0.0.Final and Kafka 3.9.1 containers
on a GitHub-hosted Linux runner. **All six acceptance checks passed**, with
**11 captured Kafka records**. Order values and faults were generated for the lab;
they are unrelated to the historical UCI invoice data or any employer system.

| Check | Observed result |
| --- | --- |
| Initial snapshot | Two source orders and GBP 150.00 reconciled |
| Changes and deletion | Insert, correction, refund, cancellation, delete and tombstone reconciled against PostgreSQL |
| Consumer exit before commit | Prior orders, mart, audit and offset preserved; same event consumed after restart |
| Connector SIGKILL | Changes made during the outage caught up and reconciled; observed restart-to-reconciliation time 13.521 seconds |
| Replay | All 11 original offsets recognized as duplicates; no new applications or changed results |
| Rebuild | Empty DuckDB target reconstructed the same final rows, totals and offsets |

The final generated net is GBP 130.00 across two days. The 13.521-second value is
one controlled observation, measured from initiating connector restart through
source/target reconciliation. It excludes the earlier outage interval and is not
an SLA or a claim about recovery after disk loss. The 0.655-second consumer
exercise measurement includes the deliberate process exit and successful retry.

## Provenance

- Pull-request head: `5c723d5ce0809e2bfceb2ac40c949731d6406376`.
- Executed GitHub pull-request merge commit, recorded in the report:
  `2f6136e00787b9e3e96d336f2a4c5b6de018f133`.
- Started `2026-11-12T17:44:54.836707+00:00`; finished
  `2026-11-12T17:45:22.522422+00:00`.
- The four files below are byte-for-byte copies from the successful workflow
  artifact. Their implementation hashes match the exercised source files.
  The full workflow artifact also includes container logs and both DuckDB files;
  its retention is 30 days. These small committed files remain available afterward.

| Retained file | SHA-256 |
| --- | --- |
| [evidence.json](evidence.json) | `815cefd27b6d6b54b8893adc402c4b8c31a384ef3a64c1e0b293bdb0770ade4f` |
| [events.jsonl](events.jsonl) | `da5033798a555477c8045ddf1e3a23d7bc79daa5486a95b0f8d5e58a68cee6f3` |
| [images.json](images.json) | `f136a1d4308e9966943a34f5ad94c53ed9aef0e078cdfbf94f72fc1c1d877f1a` |
| [docker-version.txt](docker-version.txt) | `8402d8b7679b4087fa4fb62264e49054f974146c5d134195d8b7a341747ab4ed` |

The JSON retains each independently reconciled source/target state, event offsets,
runtime versions and code hashes. Image IDs identify the downloaded container
images; the Compose file still uses version tags rather than digest pins.

## Scope

One writer, one source table, one Kafka partition and single-node services with
retained disk. The sink commits its order state, finance totals, audit and Kafka
offset together; there is no distributed exactly-once transaction or atomic
replication of a multi-row source transaction. Connector redelivery can produce
an intermediate stale view before ordered catch-up. This exercise establishes
the final reconciliation shown here, not continuous operation, high availability
or achieved business savings. See the [contract and runbook](../OPERATIONAL_CDC.md).
