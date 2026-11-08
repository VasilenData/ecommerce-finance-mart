select 'stg_lines' t, count(*) n from stg_lines union all
select 'line_classified', count(*) from line_classified union all
select 'quarantine_lines', count(*) from quarantine_lines union all
select 'fact_lines_base', count(*) from fact_lines_base union all
select 'dim_customer', count(*) from dim_customer union all
select 'fact_lines', count(*) from fact_lines union all
select 'dim_product', count(*) from dim_product union all
select 'dim_date', count(*) from dim_date union all
select 'fact_invoice', count(*) from fact_invoice union all
select 'mart_monthly', count(*) from mart_monthly union all
select 'mart_customer_rfm', count(*) from mart_customer_rfm union all
select 'mart_cohort', count(*) from mart_cohort union all
select 'mart_product', count(*) from mart_product union all
select 'mart_country', count(*) from mart_country union all
select 'mart_explorer', count(*) from mart_explorer union all
select 'mart_explorer_product', count(*) from mart_explorer_product union all
select 'monthly_gross_total', round(sum(gross_sales), 3) from mart_monthly union all
select 'monthly_returns_total', round(sum(returns), 3) from mart_monthly union all
select 'quick_reversals_total', round(sum(quick_reversals), 3) from mart_monthly union all
select 'fact_lines_net', round(sum(amount), 3) from fact_lines;
