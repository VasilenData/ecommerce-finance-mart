{{ config(materialized='incremental', incremental_strategy='append', on_schema_change='fail') }}
select record_id, revision, recorded_at, invoice_id, paid_date, amount_cents, ingestion_sequence from {{ source('journal', 'payments') }}
{% if is_incremental() %}
where ingestion_sequence > (select coalesce(max(ingestion_sequence), 0) from {{ this }})
{% endif %}
