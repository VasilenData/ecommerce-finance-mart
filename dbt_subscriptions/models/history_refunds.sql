{{ config(materialized='incremental', incremental_strategy='append', on_schema_change='fail') }}
select record_id, revision, recorded_at, payment_id, refunded_date, amount_cents, ingestion_sequence from {{ source('journal', 'refunds') }}
{% if is_incremental() %}
where ingestion_sequence > (select coalesce(max(ingestion_sequence), 0) from {{ this }})
{% endif %}
