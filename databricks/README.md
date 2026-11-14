# Finance Mart on Databricks Free Edition

The four DuckDB SQL layers of Finance Mart, ported to Spark SQL and run on a Databricks Free Edition serverless SQL warehouse. Same public practice data (UCI Online Retail II, one UK retailer), same metric definitions, no cost.

This is a port of plain SQL scripts, not dbt models. The repo's pipeline is `mart/pipeline.py` running four SQL files on DuckDB.

## Result: matches DuckDB exactly

The table below records the original full-dataset comparison. Later fixes retain returns-only months, guard zero denominators and include observed zero-activity cohort months. Those changes are fixture-tested in DuckDB and mirrored in the SQL port; rerun the verification scripts on Databricks before claiming renewed parity for those edge cases.

The [current revalidation notebook](REVALIDATION.md) now packages the exact SQL port, raw edge-case fixtures and a fresh full-source DuckDB reference. It compares all 17 derived relation counts, exact money and classification groups, plus 27,394 rows across 11 full-data dimensions/marts. **Hosted execution is still pending**; the authenticated console rejects automated browser control, so an actual manual run or supported connector is required.

I ran the DuckDB build first and recorded the numbers, then ran the port and compared. All 20 table counts and money totals are identical, including the totals to 3 decimals.

| Table | DuckDB | Databricks |
|---|---:|---:|
| stg_lines | 1,067,371 | 1,067,371 |
| line_classified | 1,067,371 | 1,067,371 |
| quarantine_lines | 40,363 | 40,363 |
| fact_lines_base / fact_lines | 1,027,008 | 1,027,008 |
| dim_customer | 5,939 | 5,939 |
| dim_product | 4,739 | 4,739 |
| dim_date | 739 | 739 |
| fact_invoice | 48,360 | 48,360 |
| mart_monthly | 25 | 25 |
| mart_customer_rfm | 5,852 | 5,852 |
| mart_cohort | 325 | 325 |
| mart_product | 4,720 | 4,720 |
| mart_country | 42 | 42 |
| mart_explorer | 643 | 643 |
| mart_explorer_product | 4,369 | 4,369 |
| gross sales total | 19,643,861.637 | 19,643,861.637 |
| returns total | -716,462.570 | -716,462.570 |
| quick reversals total | -312,457.800 | -312,457.800 |
| fact_lines net | 18,855,330.198 | 18,855,330.198 |

Breakdowns (`04_verify_breakdowns.sql`) also match: ABC classes A 1,040 / B 1,260 / C 2,420, all 8 RFM segments (for example Champions 1,470, Hibernating 1,515), and the ten largest line verdicts (for example keep:sale 1,003,357, reject:exact_duplicate 34,335). The result had 25 rows, which equals 3 + 8 + 14 groups as in DuckDB. I did not read the four smallest verdict rows by eye.

## Run times (serverless 2X-Small warehouse, from the SQL editor)

| Script | Statements | Time |
|---|---:|---:|
| 01_build_core.sql | 13 | 44.3 s |
| 02_build_marts.sql | 8 | 27.7 s |
| 03_verify.sql | 1 | 3.7 s |

One run each, so treat these as rough.

## How to reproduce

1. Create a free account at databricks.com/learn/free-edition.
2. Export the raw table from DuckDB as all-text parquet with a `line_id` column (`COPY raw_lines TO 'raw_lines.parquet' (FORMAT PARQUET)`) and rename `Customer ID` to `Customer_ID`.
3. In Catalog, upload it as `workspace.finance_mart.raw_lines` (New, Add or upload data, Create or modify table).
4. In the SQL editor, pick schema `workspace.finance_mart`, then run `01_build_core.sql`, `02_build_marts.sql` and `03_verify.sql` in order.

## Dialect changes

- A correlated `EXISTS` in the select list became a join on a `reversal` CTE (return lines that mirror a sale within 60 minutes).
- `isoyear` became `extract(YEAROFWEEK ...)`, `dayname` became `date_format(d, 'EEEE')`, `isodow >= 6` became `dayofweek in (1, 7)`.
- `generate_series` became `explode(sequence(...))`.
- `date_diff('month', ...)` became a year and month formula; day diffs use `datediff`.
- `max(d) + 1` became `date_add(max(d), 1)`, and `::date` became `cast(... as date)`.
- Ratios are cast to double. `USING` joins became `ON` joins.
- CTE names `size` and `top` were renamed (`cohort_sizes`, `top_products`).
- Seed tables (line-type rules) are inlined as `INSERT ... VALUES`.
- `line_id` is carried over from the DuckDB export so duplicate tie-breaking is identical.

## BigQuery vs Databricks, from what I observed

- Databricks Free Edition ran the SQL transformation port shown here. The BigQuery experiment only loaded the final monthly table built by DuckDB; the transformation pipeline was not ported to BigQuery.
- The BigQuery load SQL and job results are not checked into this repository. The reproducible platform comparison here is the DuckDB/Databricks SQL and its count/total checks.
- In the separate [BigQuery Cost Lab](https://github.com/VasilenData/bigquery-cost-lab), I measured bytes scanned per query. I did not measure anything comparable on Databricks, so I make no cost comparison between the platforms.

## Limits

- Serverless SQL only: no jobs, no scheduling, no dbt, no Delta tuning, no tests beyond the count and total checks above.
- Free Edition has daily compute limits; I used a small fraction.
- Public practice data, not employer data. Raw data is loaded by a manual upload, not an automated ingest.
- Parity is checked on counts, totals and group sizes, not on every row.
