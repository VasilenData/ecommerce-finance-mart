SELECT *, lead(effective_date) OVER (PARTITION BY subscription_id ORDER BY effective_date) valid_to
FROM {{ ref('stg_subscription_changes') }}
