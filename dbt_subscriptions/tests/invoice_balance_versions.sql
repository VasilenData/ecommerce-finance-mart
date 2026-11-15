SELECT coalesce(old.invoice_id, new.invoice_id) AS invoice_id
FROM {{ ref('invoice_balances', v=1) }} old
FULL OUTER JOIN {{ ref('invoice_balances', v=2) }} new ON old.invoice_id = new.invoice_id
WHERE old.invoice_id IS NULL OR new.invoice_id IS NULL
   OR old.balance_cents <> new.net_balance_cents
   OR new.net_balance_cents <> new.gross_unpaid_cents - new.credit_cents
   OR new.gross_unpaid_cents < 0 OR new.credit_cents < 0
   OR (new.gross_unpaid_cents > 0 AND new.credit_cents > 0)
