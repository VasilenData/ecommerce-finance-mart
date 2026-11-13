{% set definition = var('growth_definition') | int %}
{% if definition not in [1, 2] %}
  {{ exceptions.raise_compiler_error('growth_definition must be 1 or 2') }}
{% endif %}
select m.month, {{ definition }}::integer as growth_definition_version,
    m.closing_mrr_cents, c.invoiced_cents, c.net_cash_cents, c.closing_balance_cents,
    (m.new_mrr_cents{% if definition == 2 %} + m.reactivation_mrr_cents{% endif %})::bigint as growth_mrr_cents
from {{ ref('monthly_mrr_bridge') }} m join {{ ref('monthly_cash_bridge') }} c using (month)
