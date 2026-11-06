-- Layer 3: core star schema built only from kept lines.
create or replace table quarantine_lines as
select line_id, invoice_raw, stock_code_raw, description_raw, quantity, invoiced_at, unit_price, customer_id,
       country_raw, replace(verdict, 'reject:', '') as reject_reason,
       quantity * unit_price as amount
from line_classified where verdict like 'reject:%';

create or replace table fact_lines_base as
select line_id, invoice_no, stock_code, description_clean, quantity, invoiced_at,
       invoiced_at::date as invoice_date, date_trunc('month', invoiced_at)::date as invoice_month,
       unit_price, quantity * unit_price as amount, customer_id, country as invoice_country, region as invoice_region,
       replace(verdict, 'keep:', '') as line_type
from line_classified where verdict like 'keep:%';

create or replace table dim_customer as
with c as (
    select customer_id, invoice_country as country, count(*) n from fact_lines_base
    where customer_id is not null group by all
),
pick as (
    select customer_id, country from (
        select *, row_number() over (partition by customer_id order by n desc, country) rn from c) where rn = 1
)
select p.customer_id, p.country, m.region,
       min(f.invoice_date) filter (where f.line_type = 'sale') as first_sale_date,
       max(f.invoice_date) filter (where f.line_type = 'sale') as last_sale_date,
       date_trunc('month', min(f.invoice_date) filter (where f.line_type = 'sale'))::date as cohort_month
from pick p
join fact_lines_base f using (customer_id)
left join (select distinct country, region from country_map) m on m.country = p.country
group by p.customer_id, p.country, m.region;

create or replace table fact_lines as
select b.line_id, b.invoice_no, b.stock_code, b.quantity, b.invoiced_at, b.invoice_date, b.invoice_month,
       b.unit_price, b.amount, b.customer_id, b.line_type,
       coalesce(c.country, b.invoice_country) as country,
       coalesce(c.region, b.invoice_region)   as region,
       case when b.customer_id is null then 'Guest' else 'Registered' end as customer_type,
       -- A return that exactly mirrors a sale by the same customer within 60 minutes is an order-entry
       -- reversal, not a real customer return. Flagged, never deleted.
       (b.line_type = 'return' and exists (
           select 1 from fact_lines_base s
           where s.line_type = 'sale' and s.customer_id = b.customer_id and s.stock_code = b.stock_code
             and s.quantity = -b.quantity and s.unit_price = b.unit_price
             and s.invoiced_at <= b.invoiced_at and b.invoiced_at - s.invoiced_at <= interval 60 minute
       )) as is_quick_reversal
from fact_lines_base b left join dim_customer c using (customer_id);

create or replace table dim_product as
with d as (
    select stock_code, description_clean, count(*) n, max(invoiced_at) last_seen
    from line_classified
    where verdict in ('keep:sale', 'keep:return') and description_clean is not null group by all
),
r as (
    select *, row_number() over (partition by stock_code order by n desc, last_seen desc, description_clean) rn,
              count(*) over (partition by stock_code) description_variants
    from d
),
p as (
    select stock_code,
           min(invoice_date) filter (where line_type = 'sale') as first_sold,
           max(invoice_date) filter (where line_type = 'sale') as last_sold,
           median(unit_price) filter (where line_type = 'sale') as median_unit_price
    from fact_lines where line_type in ('sale', 'return') group by stock_code
)
select p.stock_code,
       coalesce(r.description_clean, '(no description)') as description,
       coalesce(r.description_variants, 0) as description_variants,
       p.first_sold, p.last_sold, p.median_unit_price
from p left join r on r.stock_code = p.stock_code and r.rn = 1;

create or replace table dim_date as
select d::date as date, date_trunc('month', d)::date as month, year(d) as year, quarter(d) as quarter,
       isoyear(d) as iso_year, weekofyear(d) as iso_week, dayname(d) as day_name, isodow(d) >= 6 as is_weekend
from generate_series((select min(invoice_date) from fact_lines), (select max(invoice_date) from fact_lines), interval 1 day) t(d);

create or replace table fact_invoice as
select invoice_no, min(invoiced_at) as invoiced_at, min(invoice_date) as invoice_date,
       any_value(customer_id) as customer_id, any_value(country) as country, any_value(customer_type) as customer_type,
       invoice_no like 'C%' as is_credit_note, count(*) as n_lines,
       coalesce(sum(amount) filter (where line_type = 'sale'), 0)   as sale_amount,
       coalesce(sum(amount) filter (where line_type = 'return'), 0) as return_amount,
       coalesce(sum(amount) filter (where line_type not in ('sale', 'return')), 0) as other_amount,
       sum(amount) as net_amount
from fact_lines group by invoice_no;
