-- Layer 4: marts, ported from DuckDB SQL to Databricks SQL. Metric definitions are unchanged
-- (see docs/metrics.md in the Finance Mart repo).

create or replace table as_of as select date_add(max(invoice_date), 1) as as_of_date from fact_lines;

create or replace table mart_monthly as
with m as (
    select invoice_month as month,
        coalesce(sum(amount) filter (where line_type = 'sale'), 0)     as gross_sales,
        coalesce(sum(amount) filter (where line_type = 'return'), 0)   as returns,
        coalesce(sum(amount) filter (where line_type = 'return' and is_quick_reversal), 0) as quick_reversals,
        coalesce(sum(amount) filter (where line_type = 'shipping'), 0) as shipping,
        coalesce(sum(amount) filter (where line_type = 'discount'), 0) as discounts,
        coalesce(sum(amount) filter (where line_type not in ('sale', 'return', 'shipping', 'discount')), 0) as other_items,
        count(distinct invoice_no) filter (where line_type = 'sale') as orders,
        coalesce(sum(quantity) filter (where line_type = 'sale'), 0) as units,
        count(distinct customer_id) filter (where line_type = 'sale') as active_customers,
        coalesce(sum(amount) filter (where line_type = 'sale' and customer_type = 'Guest'), 0) as guest_sales
    from fact_lines group by 1
    having count(*) filter (where line_type in ('sale', 'return')) > 0
),
n as (
    select cohort_month as month, count(*) as new_customers from dim_customer
    where cohort_month is not null group by 1
)
select m.month, m.gross_sales, m.returns, m.gross_sales + m.returns as net_product_revenue,
       m.shipping, m.discounts, m.other_items, m.orders, m.units,
       cast(m.gross_sales as double) / nullif(m.orders, 0) as avg_order_value,
       cast(-m.returns as double) / nullif(m.gross_sales, 0) as return_rate,
       m.quick_reversals,
       cast(-(m.returns - m.quick_reversals) as double) / nullif(m.gross_sales + m.quick_reversals, 0) as return_rate_ex_reversals,
       m.active_customers,
       case when m.month = (select min(month) from m) then null else coalesce(n.new_customers, 0) end as new_customers,
       case when m.month = (select min(month) from m) then null else m.active_customers - coalesce(n.new_customers, 0) end as returning_customers,
       cast(m.guest_sales as double) / nullif(m.gross_sales, 0) as guest_share
from m left join n on n.month = m.month order by m.month;

create or replace table mart_customer_rfm as
with base as (
    select f.customer_id,
        datediff((select as_of_date from as_of), max(f.invoice_date) filter (where f.line_type = 'sale')) as recency_days,
        count(distinct f.invoice_no) filter (where f.line_type = 'sale') as frequency,
        sum(f.amount) filter (where f.line_type in ('sale', 'return')) as monetary
    from fact_lines f where f.customer_id is not null group by 1
    having count(*) filter (where f.line_type = 'sale') > 0
),
scored as (
    select *,
        ntile(5) over (order by recency_days desc, customer_id) as r_score,
        ntile(5) over (order by frequency, monetary, customer_id) as f_score,
        ntile(5) over (order by monetary, customer_id) as m_score
    from base
)
select s.customer_id, c.country, c.region, c.cohort_month, s.recency_days, s.frequency, s.monetary,
       r_score, f_score, m_score,
       case
           when monetary <= 0                         then 'Net negative'
           when r_score >= 4 and f_score >= 4         then 'Champions'
           when r_score >= 3 and f_score >= 4         then 'Loyal'
           when r_score >= 4 and f_score >= 2         then 'Potential loyalists'
           when r_score = 5 and f_score = 1           then 'New'
           when r_score <= 2 and f_score >= 3         then 'At risk'
           when r_score <= 2 and f_score <= 2         then 'Hibernating'
           else 'Needs attention'
       end as segment
from scored s join dim_customer c on c.customer_id = s.customer_id;

create or replace table mart_cohort as
with first_m as (select customer_id, cohort_month from dim_customer where cohort_month is not null),
act as (
    select f.customer_id, f.invoice_month as month, sum(f.amount) as revenue
    from fact_lines f where f.line_type = 'sale' and f.customer_id is not null group by all
),
j as (
    select fm.cohort_month, a.customer_id, a.revenue,
           (year(a.month) - year(fm.cohort_month)) * 12 + (month(a.month) - month(fm.cohort_month)) as month_index
    from act a join first_m fm on fm.customer_id = a.customer_id
),
cohort_sizes as (select cohort_month, count(*) as cohort_size from first_m group by 1),
observed as (
    select j.cohort_month, j.month_index, s.cohort_size,
           count(distinct j.customer_id) as active_customers, sum(j.revenue) as revenue
    from j join cohort_sizes s on s.cohort_month = j.cohort_month
    group by j.cohort_month, j.month_index, s.cohort_size
),
periods as (
    select s.*, explode(sequence(0, (year(l.last_month) - year(s.cohort_month)) * 12
        + month(l.last_month) - month(s.cohort_month))) as month_index
    from cohort_sizes s cross join (select max(invoice_month) as last_month from fact_lines) l
)
select p.cohort_month, p.month_index, p.cohort_size,
       coalesce(o.active_customers, 0) as active_customers,
       cast(coalesce(o.active_customers, 0) as double) / p.cohort_size as retention,
       coalesce(o.revenue, 0) as revenue
from periods p left join observed o on p.cohort_month = o.cohort_month and p.month_index = o.month_index
order by 1, 2;

create or replace table mart_product as
with p as (
    select stock_code,
        coalesce(sum(amount) filter (where line_type = 'sale'), 0)
            + coalesce(sum(amount) filter (where line_type = 'return' and is_quick_reversal), 0) as sales_revenue,
        coalesce(sum(quantity) filter (where line_type = 'sale'), 0)
            + coalesce(sum(quantity) filter (where line_type = 'return' and is_quick_reversal), 0) as units_sold,
        count(distinct invoice_no) filter (where line_type = 'sale') as orders,
        -coalesce(sum(quantity) filter (where line_type = 'return' and not is_quick_reversal), 0) as units_returned,
        -coalesce(sum(amount) filter (where line_type = 'return' and not is_quick_reversal), 0) as returned_value
    from fact_lines where line_type in ('sale', 'return') group by 1
    having coalesce(sum(amount) filter (where line_type = 'sale'), 0)
         + coalesce(sum(amount) filter (where line_type = 'return' and is_quick_reversal), 0) > 0
),
r as (
    select *, rank() over (order by sales_revenue desc) as revenue_rank,
              sum(sales_revenue) over (order by sales_revenue desc, stock_code) / sum(sales_revenue) over () as cum_share
    from p
)
select r.stock_code, d.description, r.sales_revenue, r.units_sold, r.orders,
       r.units_returned, r.returned_value,
       cast(r.units_returned as double) / nullif(r.units_sold, 0) as return_rate_units,
       r.revenue_rank, r.cum_share,
       case when r.cum_share <= 0.8 then 'A' when r.cum_share <= 0.95 then 'B' else 'C' end as abc_class
from r join dim_product d on d.stock_code = r.stock_code order by r.revenue_rank;

create or replace table mart_country as
select country, any_value(region) as region,
    sum(amount) filter (where line_type = 'sale') as gross_sales,
    sum(amount) filter (where line_type = 'return') as returns,
    count(distinct invoice_no) filter (where line_type = 'sale') as orders,
    count(distinct customer_id) filter (where line_type = 'sale') as customers,
    sum(amount) filter (where line_type = 'sale') / nullif(count(distinct invoice_no) filter (where line_type = 'sale'), 0) as avg_order_value
from fact_lines group by country order by gross_sales desc nulls last;

create or replace table mart_explorer as
select invoice_month as month, country, region, customer_type,
    coalesce(sum(amount) filter (where line_type = 'sale'), 0)   as gross_sales,
    coalesce(sum(amount) filter (where line_type = 'return'), 0) as returns,
    coalesce(sum(amount) filter (where line_type = 'return' and is_quick_reversal), 0) as quick_reversals,
    count(distinct invoice_no) filter (where line_type = 'sale') as orders,
    coalesce(sum(quantity) filter (where line_type = 'sale'), 0) as units,
    count(distinct customer_id) filter (where line_type = 'sale') as customers
from fact_lines group by all having count(*) filter (where line_type in ('sale', 'return')) > 0;

create or replace table mart_explorer_product as
with top_products as (select stock_code, description from mart_product where revenue_rank <= 200),
x as (
    select invoice_month as month, coalesce(t.stock_code, 'OTHER') as stock_code,
           coalesce(t.description, 'All other products') as description,
           sum(f.amount) filter (where f.line_type = 'sale')
               + coalesce(sum(f.amount) filter (where f.line_type = 'return' and f.is_quick_reversal), 0) as sales_revenue,
           sum(f.quantity) filter (where f.line_type = 'sale')
               + coalesce(sum(f.quantity) filter (where f.line_type = 'return' and f.is_quick_reversal), 0) as units
    from fact_lines f left join top_products t on t.stock_code = f.stock_code
    where f.line_type in ('sale', 'return') group by all
    having sum(f.amount) filter (where f.line_type = 'sale') is not null
)
select * from x;
