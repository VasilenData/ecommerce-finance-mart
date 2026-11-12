-- Bitemporal source selection: recorded_at is knowledge time; business dates
-- decide which closed month a record affects. No current-state overwrite.
CREATE VIEW subscription_changes AS
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (PARTITION BY record_id ORDER BY revision DESC) rn
    FROM subscription_changes_raw WHERE recorded_at <= (SELECT knowledge_cutoff FROM report_parameters)
) WHERE rn = 1;
CREATE VIEW invoices AS
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (PARTITION BY record_id ORDER BY revision DESC) rn
    FROM invoices_raw WHERE recorded_at <= (SELECT knowledge_cutoff FROM report_parameters)
) WHERE rn = 1;
CREATE VIEW payments AS
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (PARTITION BY record_id ORDER BY revision DESC) rn
    FROM payments_raw WHERE recorded_at <= (SELECT knowledge_cutoff FROM report_parameters)
) WHERE rn = 1;
CREATE VIEW refunds AS
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (PARTITION BY record_id ORDER BY revision DESC) rn
    FROM refunds_raw WHERE recorded_at <= (SELECT knowledge_cutoff FROM report_parameters)
) WHERE rn = 1;

CREATE VIEW subscription_intervals AS
SELECT *, lead(effective_date) OVER (PARTITION BY subscription_id ORDER BY effective_date) valid_to
FROM subscription_changes;

CREATE TABLE report_months AS
SELECT CAST(month AS DATE) AS month FROM generate_series(
    (SELECT period_start FROM report_parameters),
    (SELECT period_end - INTERVAL '1 month' FROM report_parameters), INTERVAL '1 month'
) AS series(month);

CREATE TABLE customer_mrr_bridge AS
WITH customers AS (SELECT DISTINCT customer_id FROM subscription_changes),
boundaries AS (
    SELECT m.month, c.customer_id,
        coalesce((SELECT sum(s.monthly_amount_cents) FROM subscription_intervals s
                  WHERE s.customer_id = c.customer_id AND s.effective_date < m.month
                    AND (s.valid_to IS NULL OR s.valid_to >= m.month)), 0)::BIGINT opening_mrr_cents,
        coalesce((SELECT sum(s.monthly_amount_cents) FROM subscription_intervals s
                  WHERE s.customer_id = c.customer_id AND s.effective_date < m.month + INTERVAL '1 month'
                    AND (s.valid_to IS NULL OR s.valid_to >= m.month + INTERVAL '1 month')), 0)::BIGINT closing_mrr_cents,
        EXISTS (SELECT 1 FROM subscription_changes s WHERE s.customer_id = c.customer_id
                AND s.effective_date < m.month AND s.monthly_amount_cents > 0) previously_paying
    FROM report_months m CROSS JOIN customers c
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
FROM boundaries;

CREATE TABLE monthly_mrr_bridge AS
SELECT m.month, coalesce(sum(opening_mrr_cents), 0)::BIGINT opening_mrr_cents,
    coalesce(sum(new_mrr_cents), 0)::BIGINT new_mrr_cents,
    coalesce(sum(expansion_mrr_cents), 0)::BIGINT expansion_mrr_cents,
    coalesce(sum(reactivation_mrr_cents), 0)::BIGINT reactivation_mrr_cents,
    coalesce(sum(contraction_mrr_cents), 0)::BIGINT contraction_mrr_cents,
    coalesce(sum(churn_mrr_cents), 0)::BIGINT churn_mrr_cents,
    coalesce(sum(closing_mrr_cents), 0)::BIGINT closing_mrr_cents
FROM report_months m LEFT JOIN customer_mrr_bridge c ON c.month = m.month GROUP BY m.month;

-- Aggregate sources separately before joining: multiple payments and refunds
-- must not multiply invoice amounts.
CREATE TABLE invoice_balances AS
WITH paid AS (
    SELECT invoice_id, sum(amount_cents)::BIGINT paid_cents FROM payments
    WHERE paid_date < (SELECT period_end FROM report_parameters) GROUP BY invoice_id
), refunded AS (
    SELECT p.invoice_id, sum(r.amount_cents)::BIGINT refunded_cents
    FROM refunds r JOIN payments p ON p.record_id = r.payment_id
    WHERE r.refunded_date < (SELECT period_end FROM report_parameters) GROUP BY p.invoice_id
)
SELECT i.record_id invoice_id, i.customer_id, i.amount_cents invoiced_cents,
    coalesce(p.paid_cents, 0)::BIGINT paid_cents, coalesce(r.refunded_cents, 0)::BIGINT refunded_cents,
    (i.amount_cents - coalesce(p.paid_cents, 0) + coalesce(r.refunded_cents, 0))::BIGINT balance_cents
FROM invoices i LEFT JOIN paid p ON p.invoice_id = i.record_id
LEFT JOIN refunded r ON r.invoice_id = i.record_id
WHERE i.issued_date < (SELECT period_end FROM report_parameters);

CREATE TABLE monthly_cash_bridge AS
WITH movements AS (
    SELECT issued_date business_date, amount_cents invoiced_cents, 0::BIGINT paid_cents, 0::BIGINT refunded_cents FROM invoices
    UNION ALL SELECT paid_date, 0, amount_cents, 0 FROM payments
    UNION ALL SELECT refunded_date, 0, 0, amount_cents FROM refunds
)
SELECT m.month,
    coalesce(sum(v.invoiced_cents - v.paid_cents + v.refunded_cents)
             FILTER (WHERE v.business_date < m.month), 0)::BIGINT opening_balance_cents,
    coalesce(sum(v.invoiced_cents) FILTER (WHERE v.business_date >= m.month), 0)::BIGINT invoiced_cents,
    coalesce(sum(v.paid_cents) FILTER (WHERE v.business_date >= m.month), 0)::BIGINT paid_cents,
    coalesce(sum(v.refunded_cents) FILTER (WHERE v.business_date >= m.month), 0)::BIGINT refunded_cents,
    coalesce(sum(v.paid_cents - v.refunded_cents) FILTER (WHERE v.business_date >= m.month), 0)::BIGINT net_cash_cents,
    coalesce(sum(v.invoiced_cents - v.paid_cents + v.refunded_cents), 0)::BIGINT closing_balance_cents
FROM report_months m LEFT JOIN movements v ON v.business_date < m.month + INTERVAL '1 month'
GROUP BY m.month;
