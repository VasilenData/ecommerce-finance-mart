WITH paid AS (
    SELECT invoice_id, sum(amount_cents)::BIGINT paid_cents FROM {{ ref('stg_payments') }}
    WHERE paid_date < (SELECT period_end FROM {{ ref('report_parameters') }}) GROUP BY invoice_id
), refunded AS (
    SELECT p.invoice_id, sum(r.amount_cents)::BIGINT refunded_cents
    FROM {{ ref('stg_refunds') }} r JOIN {{ ref('stg_payments') }} p ON p.record_id = r.payment_id
    WHERE r.refunded_date < (SELECT period_end FROM {{ ref('report_parameters') }}) GROUP BY p.invoice_id
)
SELECT i.record_id invoice_id, i.customer_id, i.amount_cents invoiced_cents,
    coalesce(p.paid_cents, 0)::BIGINT paid_cents, coalesce(r.refunded_cents, 0)::BIGINT refunded_cents,
    (i.amount_cents - coalesce(p.paid_cents, 0) + coalesce(r.refunded_cents, 0))::BIGINT balance_cents
FROM {{ ref('stg_invoices') }} i LEFT JOIN paid p ON p.invoice_id = i.record_id
LEFT JOIN refunded r ON r.invoice_id = i.record_id
WHERE i.issued_date < (SELECT period_end FROM {{ ref('report_parameters') }})
