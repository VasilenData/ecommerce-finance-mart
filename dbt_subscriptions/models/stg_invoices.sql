select record_id, revision, recorded_at, customer_id, issued_date, amount_cents from (
 select *, row_number() over (partition by record_id order by revision desc) as revision_rank
 from {{ ref('history_invoices') }}
 where recorded_at <= cast('{{ var('knowledge_cutoff') }}' as timestamptz)
) ranked where revision_rank = 1
