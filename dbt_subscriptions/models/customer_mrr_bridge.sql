WITH customers AS (SELECT DISTINCT customer_id FROM {{ ref('stg_subscription_changes') }}),
boundaries AS (
    SELECT m.month, c.customer_id,
        coalesce((SELECT sum(s.monthly_amount_cents) FROM {{ ref('subscription_intervals') }} s
                  WHERE s.customer_id = c.customer_id AND s.effective_date < m.month
                    AND (s.valid_to IS NULL OR s.valid_to >= m.month)), 0)::BIGINT opening_mrr_cents,
        coalesce((SELECT sum(s.monthly_amount_cents) FROM {{ ref('subscription_intervals') }} s
                  WHERE s.customer_id = c.customer_id AND s.effective_date < m.month + INTERVAL '1 month'
                    AND (s.valid_to IS NULL OR s.valid_to >= m.month + INTERVAL '1 month')), 0)::BIGINT closing_mrr_cents,
        EXISTS (SELECT 1 FROM {{ ref('stg_subscription_changes') }} s WHERE s.customer_id = c.customer_id
                AND s.effective_date < m.month AND s.monthly_amount_cents > 0) previously_paying
    FROM {{ ref('report_months') }} m CROSS JOIN customers c
)
SELECT *,
    CASE WHEN opening_mrr_cents = 0 AND closing_mrr_cents > 0 AND NOT previously_paying
         THEN closing_mrr_cents ELSE 0 END new_mrr_cents,
    CASE WHEN opening_mrr_cents = 0 AND closing_mrr_cents > 0 AND previously_paying
         THEN closing_mrr_cents ELSE 0 END reactivation_mrr_cents,
    CASE WHEN opening_mrr_cents > 0 AND closing_mrr_cents > opening_mrr_cents
         THEN closing_mrr_cents - opening_mrr_cents ELSE 0 END expansion_mrr_cents,
    CASE WHEN closing_mrr_cents > 0 AND closing_mrr_cents < opening_mrr_cents
         THEN opening_mrr_cents - closing_mrr_cents ELSE 0 END contraction_mrr_cents,
    CASE WHEN opening_mrr_cents > 0 AND closing_mrr_cents = 0
         THEN opening_mrr_cents ELSE 0 END churn_mrr_cents
FROM boundaries
