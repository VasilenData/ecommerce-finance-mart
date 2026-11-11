# Management decision brief

Historical decision support for one UCI online retailer; not current trading or measured business impact.

Latest complete month: **2011-11**. Declared coverage: 2009-12-01 to 2011-12-09 (exclusive). Partial boundary months are excluded.

## What changed

| Metric | Latest complete month | Previous complete month |
|---|---:|---:|
| Gross product sales | GBP 1,452,115.98 | GBP 1,103,314.25 |
| Product returns (negative) | GBP -24,991.44 | GBP -41,613.03 |
| Net product revenue | GBP 1,427,124.54 | GBP 1,061,701.22 |
| Gross average order value | GBP 527.85 | GBP 550.28 |
| Sale invoices | 2,751 | 2005 |
| Gross return rate | 1.7% | 3.8% |

Month over month net product revenue change: GBP 365,423.32 (34.4%).

Year over year net product revenue change: GBP 32,522.74 (2.3%).

## Descriptive drivers

Gross sales changed by GBP 348,801.73; the change in negative returns contributed GBP 16,621.59. Together they reconcile to GBP 365,423.32 net revenue change.

The symmetric decomposition attributes GBP 402,143.11 to order-volume differences and GBP -53,341.38 to average-basket differences. These are arithmetic contributions, not causes. Components are unavailable if either month has no sale invoices.

Largest country contributions by absolute change (all countries reconcile in JSON):

| Country | Net revenue change |
|---|---:|
| United Kingdom | GBP 381,981.44 |
| Netherlands | GBP -14,738.64 |
| Australia | GBP -10,384.49 |
| Ireland | GBP 6,695.43 |
| Japan | GBP 6,512.04 |

## Proposed decisions, evidence and missing context

**Review the largest country revenue changes with commercial and operations owners.** Reconciled country contributions and order/basket decomposition. Needed before action: Traffic, conversion, campaign spend, stock availability and contribution margins. Investigate the driver before allocating budget; revenue alone does not establish profitable demand.

**Audit returns and same-hour reversals separately before changing fulfillment or retention policy.** Negative return amounts, gross return rate and separately flagged quick reversals. Needed before action: Return reasons, product defects, dispatch dates, customer contacts and cost to serve. Confirm mechanisms and costs; aggregate associations do not identify an intervention effect.

**Use customer-value scores only to propose a controlled retention pilot.** Separate historical 90-day repeat-purchase evaluation; past-spend baseline won revenue selection. Needed before action: Consent, offer costs, randomized holdout, margins and incremental net contribution. Do not treat predicted repeat purchase or high spend as incremental offer response.

## Limits

- Historical transaction totals are descriptive; no sampling confidence interval or causal uplift is claimed.
- Zero-sale months inside declared coverage are zero observations; a missing source feed must not be declared complete.
- Country allocation uses existing full-history finance definitions, not point-in-time model features.
- Guest sales contribute to revenue but cannot support identified-customer retention actions.
- No margin, acquisition cost, inventory, traffic or intervention outcome is available; revenue is not profit or lifetime value.

## Reproduction

Source CSV SHA256: `05086ff204eaa3333090a82e6a02cd3eee881ebbb0264eff4a8730cdf81b3dd1`. The JSON records the exact input-byte identity and LF-normalized code hashes (CRLF is converted to LF only for code hashing). No input CSV is included.
