-- Opt-in schema: a credit on one invoice must not hide another invoice's unpaid amount.
SELECT invoice_id, customer_id, invoiced_cents, paid_cents, refunded_cents,
    balance_cents AS net_balance_cents,
    greatest(balance_cents, 0)::BIGINT AS gross_unpaid_cents,
    greatest(-balance_cents, 0)::BIGINT AS credit_cents
FROM {{ ref('invoice_balances', v=1) }}
