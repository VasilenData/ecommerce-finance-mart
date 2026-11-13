{{ config(materialized='incremental', incremental_strategy='append', on_schema_change='fail') }}
select record_id, revision, recorded_at, subscription_id, customer_id, effective_date, status, monthly_amount_cents, ingestion_sequence from {{ source('journal', 'subscription_changes') }}
{% if is_incremental() %}
where ingestion_sequence > (select coalesce(max(ingestion_sequence), 0) from {{ this }})
{% endif %}
