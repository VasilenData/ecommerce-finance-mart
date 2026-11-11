# Customer future value: the decision and the evidence

The finance mart now supports a second question: **which existing customers are likely to buy in the next 90 days, and how much gross product spend should we expect?** This is a historical public-data study and batch-scoring tool. It is not contractual churn, lifetime profit, a causal retention model or a deployed marketing campaign.

Open the [readable report](customer-value/index.html), [machine-readable evidence](customer-value/report.json), [all held-out predictions](customer-value/test_predictions.csv) and [source/code/artifact manifest](customer-value/manifest.json).

## Contract

| Item | Definition |
|---|---|
| Unit | One registered customer at one prediction-date cutoff |
| Eligibility | At least one positive product purchase during the preceding 180 days |
| Features | Recency, observed age, distinct invoices/days/products, gross spend over 30/90/180 days, historical return value |
| Targets | Any product purchase and gross product spend in the following 90 days |
| Timing | Features strictly before cutoff; outcomes at/after cutoff and before cutoff + 90 days |
| Money | GBP, positive product sales only; no shipping, fees, future returns or profit margins |
| History | Declared complete UCI export starts 2009-12-01; cutoffs without 180 days of source history fail |
| Last day | December 9, 2011 is partial; the latest scoring cutoff excludes that day |

The point-in-time SQL reads `fact_lines_base`, not the all-history customer/RFM dimensions. The latter are excellent retrospective marts but their latest dates, country choices and full-period totals are not prediction-time features. Guests are retained by finance reporting but excluded from identified-customer modeling. A customer with only returns is not made eligible. Future first-time customers are not silently added to a past customer snapshot.

## Frozen chronological protocol

1. Train on customer snapshots at 2010-07-01 and 2010-10-01. Each 90-day outcome window matures before the next phase.
2. Select fixed candidate models on 2011-01-01. Classification selection minimizes Brier score; revenue selection minimizes GBP mean absolute error.
3. Freeze the selected models and evaluate at 2011-04-01 and 2011-07-01. These dates were not used for fitting or selection.
4. Batch-score the historical 2011-12-09 snapshot without future labels. This is shadow inference, not measured live performance.

Customers can recur across snapshots. This tests future behaviour of an existing customer base; it does not estimate performance for unseen customers. The data covers one wholesaler and does not support claims of broad market generalization. Repeat purchases in a 90-day window do not establish long-term survival.

## Observed results, 11 November 2026

The full 1,067,371-line source passed the finance build's blocking checks. Validation selected **logistic regression**, with Brier 0.2219 versus 0.2428 for a smoothed recency-bucket baseline. Histogram gradient boosting had 0.2534 and was not selected. Probability calibration is assessed with Brier score and reliability bins; probabilities are not claimed to be perfectly calibrated.

The learned revenue candidate **failed to beat the simple baseline**: validation MAE was £808.55 versus £599.12 for repeating the customer's past 90-day spend. The system therefore kept the simple spend baseline. This result is deliberately preserved; model complexity is not a success criterion.

| Untouched prediction window | Customers | Logistic ROC-AUC | Logistic Brier | Recency Brier | Selected spend MAE | Top 20% revenue share |
|---|---:|---:|---:|---:|---:|---:|
| Apr–Jun 2011 |3,299|0.7478|0.2130|0.2288|£352.42|69.0%|
| Jul–Sep 2011 |2,722|0.7616|0.2013|0.2317|£463.01|71.0%|

The mean paired Brier difference (selected minus baseline) has exploratory customer-bootstrap 95% intervals of [-0.0212, -0.0103] and [-0.0355, -0.0255]. Each interval samples customers within one window, uses 1,000 draws with seed 17, and is not adjusted for multiple comparisons. It describes sampling uncertainty within that customer window, not uncertainty across future markets or seasons. No pooled independent-sample claim is made across the two windows because customers can repeat.

Reliability bins show some overprediction in the middle probability range. This is visible in the report and is a limitation of using a fixed model across changing periods. The customer selection uses **predicted gross spend**, which in this run is the selected past-spend baseline, not the repeat-purchase classifier. The top 20% figures describe the share of subsequent observed revenue associated with those customers. They are not incremental revenue or retention uplift.

Invoice timestamps define availability in this historical study. The source has no ingestion or revision timestamps, so it cannot establish whether an invoice was actually received promptly by a live system. A production integration would require arrival-time or bitemporal records before claiming protection against delayed or backdated records.

## Reproduce and score

```bash
pip install -r requirements-value.txt
python -m mart.value_cli --download --output dist/customer-value

# Reuse only a trusted model bundle; version/checksum and prediction-date guards apply.
python -m mart.value_score --bundle dist/customer-value \
  --raw data/raw_online_retail_II.csv --cutoff 2011-12-09 \
  --coverage-end 2011-12-09 --output dist/shadow-score
```

Both commands require a new output directory. Files are built in a sibling staging directory and published together only after generation completes; existing evidence versions cannot be overwritten. The manual `customer-value-evidence` GitHub Actions workflow rebuilds real data and retains the versioned artifact for 30 days. It does not automatically overwrite the public report or represent a scheduled production feed.

The bundle includes test predictions, shadow scores, report, model artifact and hashes. `manifest.json` records the raw CSV hash, Python/library versions, exact modeling/SQL/seed file hashes and each output hash. The Git revision can predate an uncommitted build; the file hashes identify the actual implementation. Model artifacts use joblib and must not be loaded from untrusted sources.

Regression tests cover prediction-date boundaries, invoice/customer grain, returns and guests, future-data invariance, mature/disjoint labels, complete observation history, exact capacity selection, prediction identity, source-model metadata checks and failed bundle publication. Existing finance reconciliation and dashboard checks remain in place.
