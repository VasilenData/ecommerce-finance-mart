# Revalidate the current SQL port

**Hosted status: awaiting an actual Databricks run.** The current DuckDB reference and the importable notebook are prepared. The authenticated console rejected automated browser control; no local test is presented as a renewed Databricks comparison. The historical results in [README.md](README.md) retain their original scope.

[Download/import the prepared notebook](Finance_Mart_Revalidation.ipynb) · [local preparation record](../docs/databricks-revalidation/2026-11-14/local-preparation.json) · [generator and comparison contract](../mart/databricks_validation.py)

## What the notebook verifies

The notebook embeds the current two SQL build scripts, their LF-normalized SHA-256 hashes, and a freshly executed DuckDB reference. It uses no downloaded code, credentials, pip installation or paid compute setup. It creates three uniquely named `workspace.finance_verify_*` schemas and never overwrites `workspace.finance_mart`.

1. **Raw return/cohort fixture:** a January £6 sale, February £3 return and March £3 sale. The February monthly row must remain, net revenue must be −£3, denominator-zero ratios must be NULL, and cohort activity must be 1/0/1 across exactly the three observed months. Both engines execute the entire staging/classification/core/mart port, rather than injecting preclassified facts.
2. **Raw reversal fixture:** a £6 sale reversed in full 15 minutes later. The adjusted return-rate denominator is zero; the result must be NULL. Both fixture references first pass independently hand-computed assertions in DuckDB.
3. **Historical full source:** one `CREATE TABLE AS SELECT` snapshots the existing `workspace.finance_mart.raw_lines` into an isolated schema. This keeps the source consistent across subsequent builds. All 17 derived relation counts, exact money totals and classification groups are compared with the same local source. All 27,394 rows across 11 dimensions/marts/`as_of` are compared as multisets, retaining duplicate multiplicity.

The source identity guard checks row count, unique/min/max line IDs and eight sums of 32-bit chunks of each raw row's SHA-256. The row digest uses all nine logical raw values, including nulls and the exported line ID. This is an order-independent fingerprint of the imported logical records, **not a byte-for-byte hash of a cloud CSV or a proof that collisions are impossible**. The original local CSV SHA-256 is separately recorded. Line IDs and text/null representations must be preserved in the upload; a mismatched source is rejected before transformation.

Decimal amounts, integers, dates, strings and nulls compare exactly. Floating ratios use absolute tolerance `1e-9` and relative tolerance `1e-12`. The comparator rejects altered money, missing/duplicated rows, and NULL ratios replaced by zero. Large full-data staging/fact relations are covered by counts, financial totals and verdict groups; their individual rows are not all exported or compared. Full row comparisons on every relation are performed for both small fixtures.

## Run in the existing Free Edition workspace

1. Import `Finance_Mart_Revalidation.ipynb` using **Workspace → Import**.
2. Select the existing Free Edition **Serverless** compute and **Run all**. Do not upgrade the account or create paid compute.
3. Wait for the final cell. A successful result must say `PASS`, with no failed check. Download the actual JSON using **Download validation JSON**; the same JSON is printed in the output for inspection.
4. Return that JSON together with the notebook run result. A failed run must be investigated, not relabelled as parity. The uniquely named result schemas are retained for inspection and can be removed later through normal account controls.

If `workspace.finance_mart.raw_lines` is absent or differs from the reference, the full comparison intentionally fails. Follow the original upload instructions in [README.md](README.md); do not replace an unrelated source table to make a check pass. The synthetic scenarios remain separately identifiable in the failed report.

## Regenerate from a chosen source snapshot

Use the repository's normal development dependencies and a new output directory:

```sh
python -m mart.databricks_validation --raw data/raw_online_retail_II.csv --output dist/databricks-review
python -m pytest -q tests/test_databricks_validation.py tests/test_edge_cases.py
```

The output contains an importable notebook, full DuckDB reference and short instructions. The notebook is self-contained; the reference is compressed inside it. Existing output directories are refused, keeping prepared bundles immutable. Local reference generation does not connect to Databricks or change cloud state.

## Delivery prompt and primary references

> Preserve the published metric definitions and historical evidence. Freeze the exact raw source and both SQL implementations. Execute the entire port on independent raw edge-case fixtures and the same full-source snapshot. Compare rows, counts and exact money without excluding returns-only months, zero denominators or zero-activity cohort periods. Reject missing, duplicated or altered rows. Keep all writes in a fresh schema. Record source identities, runtime, observations and failed checks. Publish hosted parity only after the Databricks engine has actually executed and the downloaded evidence has been inspected.

This follows the repository's evidence-focused delivery approach. Relevant official references are [Databricks notebook import/export](https://docs.databricks.com/aws/en/notebooks/notebook-export-import), [serverless limitations](https://docs.databricks.com/aws/en/compute/serverless/limitations), [SHA-2](https://docs.databricks.com/aws/en/sql/language-manual/functions/sha2) and [JSON serialization](https://docs.databricks.com/aws/en/sql/language-manual/functions/to_json). The notebook uses the workspace's existing Spark session and records its actual version; the reference records the actual DuckDB/Python versions.

The exercise does not establish scheduled operation, dbt on Databricks, production access controls, warehouse tuning, customer adoption or commercial impact.
