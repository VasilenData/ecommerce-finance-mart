WITH movements AS (
    SELECT issued_date business_date, amount_cents invoiced_cents, 0::BIGINT paid_cents, 0::BIGINT refunded_cents FROM {{ ref('stg_invoices') }}
    UNION ALL SELECT paid_date, 0, amount_cents, 0 FROM {{ ref('stg_payments') }}
    UNION ALL SELECT refunded_date, 0, 0, amount_cents FROM {{ ref('stg_refunds') }}
)
SELECT m.month,
    coalesce(sum(v.invoiced_cents - v.paid_cents + v.refunded_cents)
             FILTER (WHERE v.business_date < m.month), 0)::BIGINT opening_balance_cents,
    coalesce(sum(v.invoiced_cents) FILTER (WHERE v.business_date >= m.month), 0)::BIGINT invoiced_cents,
    coalesce(sum(v.paid_cents) FILTER (WHERE v.business_date >= m.month), 0)::BIGINT paid_cents,
    coalesce(sum(v.refunded_cents) FILTER (WHERE v.business_date >= m.month), 0)::BIGINT refunded_cents,
    coalesce(sum(v.paid_cents - v.refunded_cents) FILTER (WHERE v.business_date >= m.month), 0)::BIGINT net_cash_cents,
    coalesce(sum(v.invoiced_cents - v.paid_cents + v.refunded_cents), 0)::BIGINT closing_balance_cents
FROM {{ ref('report_months') }} m LEFT JOIN movements v ON v.business_date < m.month + INTERVAL '1 month'
GROUP BY m.month
