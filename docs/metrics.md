# Metric definitions

One definition per metric. The SQL marts, the Excel workbook and the explorer all use these.
Currency is GBP, as in the source data.

| Metric | Definition | Where it is computed |
|---|---|---|
| Sale line | Numeric invoice, quantity > 0, price > 0, stock code is a product | `sql/20_classify.sql` |
| Gross sales | Sum of quantity x price on sale lines | `mart_monthly.gross_sales` |
| Return | Credit-note line: invoice starts with `C`, quantity < 0. Stored negative | `mart_monthly.returns` |
| Net product revenue | Gross sales + returns | `mart_monthly.net_product_revenue` |
| Same-hour reversal | A return that exactly mirrors a sale (same customer, product, quantity, unit price) made 0 to 60 minutes earlier. A matching rule used for adjustment; the source does not establish why it was reversed | `fact_lines.is_quick_reversal` |
| Return rate (reported) | -returns / gross sales | `mart_monthly.return_rate` |
| Return rate (excluding same-hour reversals) | -(returns - reversals) / (gross sales + reversals), with reversals stored negative. Removes the reversed order from both sides | `mart_monthly.return_rate_ex_reversals` |
| Orders | Distinct invoice numbers that contain at least one sale line | `mart_monthly.orders` |
| Average order value | Gross sales / orders | `mart_monthly.avg_order_value` |
| Units | Sum of quantity on sale lines | `mart_monthly.units` |
| Active customers | Distinct customer ids with a sale line in the month. Guests have no id and are not counted | `mart_monthly.active_customers` |
| New customers | Customers whose first sale falls in the month. NULL for the first data month, which cannot be told apart from existing customers | `mart_monthly.new_customers` |
| Guest share | Sale value with no customer id / gross sales | `mart_monthly.guest_share` |
| Cohort retention | Customers from a first-sale month who bought again N months later / cohort size | `mart_cohort.retention` |
| Product revenue | Sale lines minus reversed sales, per stock code | `mart_product.sales_revenue` |
| ABC class | A: products up to 80% of cumulative revenue, B: up to 95%, C: the rest | `mart_product.abc_class` |

## Non-product lines

These are kept in the fact table but kept out of product revenue. They are listed in `seeds/stock_code_types.csv`.

| Type | Examples | Notes |
|---|---|---|
| shipping | POST, DOT, C2 | Postage charged to customers |
| discount | D | Negative amount |
| fee | BANK CHARGES, AMAZONFEE, CRUK | Marketplace and bank costs, negative |
| manual, adjustment, samples | M, ADJUST, S | Hand-keyed lines |
| gift_voucher | GIFT_0001_* | Voucher sales |
| bad_debt | B (invoice prefix A) | Large write-offs, kept visible |

## RFM segments

Registered customers with at least one sale. Each of recency (days since last order), frequency (orders) and monetary (net spend) is split into quintiles, 5 is best. Recency and monetary ties are broken by customer id; frequency ties are broken by monetary value and then customer id so the result is repeatable.

| Segment | Rule |
|---|---|
| Net negative | Net spend <= 0 |
| Champions | R >= 4 and F >= 4 |
| Loyal | R = 3 and F >= 4 |
| Potential loyalists | R >= 4 and F 2 to 3 |
| New | R = 5 and F = 1 |
| At risk | R <= 2 and F >= 3 |
| Hibernating | R <= 2 and F <= 2 |
| Needs attention | Everyone else |

Rules are applied top to bottom. Because many customers share the same frequency, quintile edges are approximate, so treat segment sizes as a ranking aid, not a measurement.
