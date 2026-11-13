select 'subscription_changes' as feed, record_id, revision from {{ ref('history_subscription_changes') }} group by record_id, revision having count(*) <> 1
union all
select 'invoices' as feed, record_id, revision from {{ ref('history_invoices') }} group by record_id, revision having count(*) <> 1
union all
select 'payments' as feed, record_id, revision from {{ ref('history_payments') }} group by record_id, revision having count(*) <> 1
union all
select 'refunds' as feed, record_id, revision from {{ ref('history_refunds') }} group by record_id, revision having count(*) <> 1
