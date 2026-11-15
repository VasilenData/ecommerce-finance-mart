# A close remains trustworthy after interruption

The receiver already rolled back an injected Python exception. This upgrade tests
actual process termination and checks persisted reports on every read.

## Reproduced defect

The accepted baseline invoice total is **5,450 USD cents**. Before this change,
editing its stored JSON total to **105,450 cents** caused `Bridge.current()` to
return the altered report without checking its stored `report_sha`. Historical
reads and successful replay responses had the same integrity gap.

Current and historical reads now check the accepted logical report digest and its
bundle, sequence and cash identities. A replay also validates its old release.
`Bridge.audit()` checks every retained report. Missing or orphaned active pointers
fail explicitly; they do not masquerade as an empty account history. A corrupt
database raises `StoredReleaseError` and is not overwritten with a new delivery.

This detects drift against the stored accepted digest. The digest shares the
database trust boundary: an administrator can rewrite both data and fingerprints.
It is not a signature or an independent financial audit.

## Observed crash boundaries

The [dated evidence](bridge-recovery/2026-11-15/evidence.json) records **32 passing
checks** over two isolated synthetic runs:

1. Insert the fully validated candidate inside a transaction. Read its presence
   through that transaction, signal the parent, and kill the child process.
2. Repeat with the active pointer changed inside the transaction, immediately
   before commit; again signal only after observing the actual SQL state, then kill.

After each stop, a new connection reopens the database, the exact old report remains
readable, the uncommitted candidate is absent, and all retained report digests pass.
A new delivery attempt produces the independently hand-calculated corrected total
of **5,300 cents**. Replaying it does not change the result or remove the old close.
The exercise also refuses a corrupted stored report and verifies exact restoration.

Recovery timing is measured from the kill request to verification of the previous
report, including process exit and database reopen. A bounded retry is used only if
the OS actually reports that the terminated worker's file lock still exists. These
single-machine observations do not establish a recovery SLA, hardware power-loss
durability, backup restoration, concurrent writer support or production operation.

## Reproduce

```sh
python -m pytest -q tests/test_bridge_recovery.py tests/test_meterwork_bridge.py
python -m mart.bridge_recovery --output dist/new-receiver-recovery
```

The exercise creates its own temporary synthetic database and kills only its own
child processes. Existing evidence directories are refused. Readiness comes from
an observed transaction boundary rather than a guessed delay. The existing real
Meterwork API-produced synthetic bundles remain unchanged.

For an existing receiver, `Bridge(path).audit()` validates all retained reports.
If it fails, stop serving the affected close, retain a diagnostic copy, and restore
the exact trusted database/receipt from backup. Do not recompute a new digest over
the corrupted report to make the check pass. Rerun the audit and source-bundle
reconciliation before reopening access. This document describes that operator
procedure; the current exercise does not claim to execute a full backup restore.

## Evidence preservation

The original 30-check integration report remains immutable. CI checks its original
source against commit `cbc8d335c84c914b702752834bfe8b2267913eaa`, whose files match
that receipt, rather than pretending it measured the newly hardened receiver.
Every CI run separately executes the current integration and crash exercises and
retains their artifacts. Git attributes preserve exact fixture and fingerprinted
integration-source bytes across Windows and Linux checkouts.

The historical GBP retail reports, synthetic subscription definitions and
Databricks verification status are unchanged by this receiver upgrade.
