select month, growth_definition_version, growth_mrr_cents, closing_mrr_cents,
    net_cash_cents, closing_balance_cents from {{ ref('monthly_subscription_metrics') }}
