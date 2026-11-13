select 'mrr' as violated_contract, cast(month as varchar) as identity
from {{ ref('monthly_mrr_bridge') }}
where opening_mrr_cents + new_mrr_cents + expansion_mrr_cents + reactivation_mrr_cents - contraction_mrr_cents - churn_mrr_cents <> closing_mrr_cents
union all
select 'cash', cast(month as varchar) from {{ ref('monthly_cash_bridge') }}
where opening_balance_cents + invoiced_cents - net_cash_cents <> closing_balance_cents
union all
select 'invoice', invoice_id from {{ ref('invoice_balances') }}
where invoiced_cents - paid_cents + refunded_cents <> balance_cents
union all
select 'customer grain', cast(month as varchar) || customer_id from {{ ref('customer_mrr_bridge') }}
group by month, customer_id having count(*) <> 1
