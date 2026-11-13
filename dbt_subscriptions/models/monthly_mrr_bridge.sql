SELECT m.month, coalesce(sum(opening_mrr_cents), 0)::BIGINT opening_mrr_cents,
    coalesce(sum(new_mrr_cents), 0)::BIGINT new_mrr_cents,
    coalesce(sum(expansion_mrr_cents), 0)::BIGINT expansion_mrr_cents,
    coalesce(sum(reactivation_mrr_cents), 0)::BIGINT reactivation_mrr_cents,
    coalesce(sum(contraction_mrr_cents), 0)::BIGINT contraction_mrr_cents,
    coalesce(sum(churn_mrr_cents), 0)::BIGINT churn_mrr_cents,
    coalesce(sum(closing_mrr_cents), 0)::BIGINT closing_mrr_cents
FROM {{ ref('report_months') }} m LEFT JOIN {{ ref('customer_mrr_bridge') }} c ON c.month = m.month GROUP BY m.month
