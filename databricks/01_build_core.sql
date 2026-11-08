-- Finance Mart ported from DuckDB SQL to Databricks SQL (Free Edition, serverless).
-- Source: github.com/VasilenData/ecommerce-finance-mart/sql. Same four layers, same logic.
-- Run after uploading raw_lines.parquet as workspace.finance_mart.raw_lines (all text columns).

-- Layer 1: staging
create or replace table stg_lines as
select
    line_id,
    trim(Invoice)                                                     as invoice_no,
    upper(trim(StockCode))                                            as stock_code,
    nullif(trim(regexp_replace(Description, '\\s+', ' ')), '')        as description_clean,
    try_cast(Quantity as int)                                         as quantity,
    try_cast(InvoiceDate as timestamp)                                as invoiced_at,
    try_cast(Price as decimal(18, 4))                                 as unit_price,
    try_cast(try_cast(Customer_ID as double) as bigint)               as customer_id,
    trim(Country)                                                     as country_raw,
    Invoice as invoice_raw, StockCode as stock_code_raw, Description as description_raw
from raw_lines;

-- Layer 2: classify (seed tables inlined)
create or replace table country_map (country_raw string, country string, region string);
insert into country_map values
  ('United Kingdom', 'United Kingdom', 'United Kingdom'),
  ('EIRE', 'Ireland', 'Europe (non-UK)'),
  ('Germany', 'Germany', 'Europe (non-UK)'),
  ('France', 'France', 'Europe (non-UK)'),
  ('Netherlands', 'Netherlands', 'Europe (non-UK)'),
  ('Spain', 'Spain', 'Europe (non-UK)'),
  ('Switzerland', 'Switzerland', 'Europe (non-UK)'),
  ('Belgium', 'Belgium', 'Europe (non-UK)'),
  ('Portugal', 'Portugal', 'Europe (non-UK)'),
  ('Australia', 'Australia', 'Rest of world'),
  ('Channel Islands', 'Channel Islands', 'Europe (non-UK)'),
  ('Italy', 'Italy', 'Europe (non-UK)'),
  ('Norway', 'Norway', 'Europe (non-UK)'),
  ('Sweden', 'Sweden', 'Europe (non-UK)'),
  ('Cyprus', 'Cyprus', 'Europe (non-UK)'),
  ('Finland', 'Finland', 'Europe (non-UK)'),
  ('Austria', 'Austria', 'Europe (non-UK)'),
  ('Denmark', 'Denmark', 'Europe (non-UK)'),
  ('Unspecified', 'Unspecified', 'Unknown'),
  ('Greece', 'Greece', 'Europe (non-UK)'),
  ('Japan', 'Japan', 'Rest of world'),
  ('USA', 'United States', 'Rest of world'),
  ('Poland', 'Poland', 'Europe (non-UK)'),
  ('United Arab Emirates', 'United Arab Emirates', 'Rest of world'),
  ('Israel', 'Israel', 'Rest of world'),
  ('Hong Kong', 'Hong Kong', 'Rest of world'),
  ('Singapore', 'Singapore', 'Rest of world'),
  ('Malta', 'Malta', 'Europe (non-UK)'),
  ('Iceland', 'Iceland', 'Europe (non-UK)'),
  ('Canada', 'Canada', 'Rest of world'),
  ('Lithuania', 'Lithuania', 'Europe (non-UK)'),
  ('RSA', 'South Africa', 'Rest of world'),
  ('Bahrain', 'Bahrain', 'Rest of world'),
  ('Brazil', 'Brazil', 'Rest of world'),
  ('Thailand', 'Thailand', 'Rest of world'),
  ('Korea', 'South Korea', 'Rest of world'),
  ('European Community', 'Unspecified', 'Unknown'),
  ('Lebanon', 'Lebanon', 'Rest of world'),
  ('West Indies', 'West Indies', 'Rest of world'),
  ('Bermuda', 'Bermuda', 'Rest of world'),
  ('Nigeria', 'Nigeria', 'Rest of world'),
  ('Czech Republic', 'Czech Republic', 'Europe (non-UK)'),
  ('Saudi Arabia', 'Saudi Arabia', 'Rest of world');
create or replace table stock_code_types (stock_code string, line_type string);
insert into stock_code_types values
  ('POST', 'shipping'),
  ('DOT', 'shipping'),
  ('C2', 'shipping'),
  ('CARRIAGE', 'shipping'),
  ('M', 'manual'),
  ('D', 'discount'),
  ('S', 'samples'),
  ('BANK CHARGES', 'fee'),
  ('AMAZONFEE', 'fee'),
  ('CRUK', 'fee'),
  ('ADJUST', 'adjustment'),
  ('ADJUST2', 'adjustment'),
  ('B', 'bad_debt'),
  ('TEST001', 'test'),
  ('TEST002', 'test'),
  ('GIFT', 'gift_voucher'),
  ('C3', 'adjustment'),
  ('GIFT_0001_10', 'gift_voucher'),
  ('GIFT_0001_20', 'gift_voucher'),
  ('GIFT_0001_30', 'gift_voucher'),
  ('GIFT_0001_40', 'gift_voucher'),
  ('GIFT_0001_50', 'gift_voucher'),
  ('GIFT_0001_60', 'gift_voucher'),
  ('GIFT_0001_70', 'gift_voucher'),
  ('GIFT_0001_80', 'gift_voucher'),
  ('GIFT_0001_90', 'gift_voucher');

create or replace table line_classified as
with dup as (
    select *,
        row_number() over (
            partition by invoice_no, stock_code, description_clean, quantity,
                         invoiced_at, unit_price, customer_id, country_raw
            order by line_id) as dup_rank
    from stg_lines
),
typed as (
    select d.*, coalesce(t.line_type, 'product') as code_type
    from dup d left join stock_code_types t using (stock_code)
)
select
    t.*,
    coalesce(m.country, 'Unspecified') as country,
    coalesce(m.region, 'Unknown')      as region,
    case
        when dup_rank > 1                                                    then 'reject:exact_duplicate'
        when invoice_no is null or quantity is null
             or unit_price is null or invoiced_at is null                    then 'reject:missing_key_field'
        when code_type = 'test'                                              then 'reject:test_product'
        when code_type = 'bad_debt'                                          then 'keep:bad_debt'
        when code_type <> 'product' and unit_price <= 0                      then 'reject:zero_price_non_product'
        when code_type <> 'product'                                          then 'keep:' || code_type
        when unit_price <= 0                                                 then 'reject:non_positive_price'
        when invoice_no like 'C%' and quantity < 0                           then 'keep:return'
        when invoice_no like 'C%'                                            then 'reject:credit_note_positive_quantity'
        when quantity <= 0                                                   then 'reject:non_positive_quantity'
        else 'keep:sale'
    end as verdict
from typed t left join country_map m on t.country_raw = m.country_raw;

-- Layer 3: core star schema
create or replace table quarantine_lines as
select line_id, invoice_raw, stock_code_raw, description_raw, quantity, invoiced_at, unit_price, customer_id,
       country_raw, replace(verdict, 'reject:', '') as reject_reason,
       quantity * unit_price as amount
from line_classified where verdict like 'reject:%';

create or replace table fact_lines_base as
select line_id, invoice_no, stock_code, description_clean, quantity, invoiced_at,
       cast(invoiced_at as date) as invoice_date, cast(date_trunc('month', invoiced_at) as date) as invoice_month,
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
       cast(date_trunc('month', min(f.invoice_date) filter (where f.line_type = 'sale')) as date) as cohort_month
from pick p
join fact_lines_base f using (customer_id)
left join (select distinct country, region from country_map) m on m.country = p.country
group by p.customer_id, p.country, m.region;

-- Port change: DuckDB used a correlated EXISTS in the select list. Here the same rule is a join on the
-- set of returns that mirror a sale (same customer, product, quantity, price) within 60 minutes.
create or replace table fact_lines as
with reversal as (
    select distinct r.line_id
    from fact_lines_base r join fact_lines_base s
      on s.customer_id = r.customer_id and s.stock_code = r.stock_code
     and s.quantity = -r.quantity and s.unit_price = r.unit_price
    where r.line_type = 'return' and s.line_type = 'sale'
      and s.invoiced_at <= r.invoiced_at
      and r.invoiced_at - s.invoiced_at <= interval 60 minutes
)
select b.line_id, b.invoice_no, b.stock_code, b.quantity, b.invoiced_at, b.invoice_date, b.invoice_month,
       b.unit_price, b.amount, b.customer_id, b.line_type,
       coalesce(c.country, b.invoice_country) as country,
       coalesce(c.region, b.invoice_region)   as region,
       case when b.customer_id is null then 'Guest' else 'Registered' end as customer_type,
       (b.line_type = 'return' and rv.line_id is not null) as is_quick_reversal
from fact_lines_base b
left join dim_customer c using (customer_id)
left join reversal rv on rv.line_id = b.line_id;

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
select d as date, cast(date_trunc('month', d) as date) as month, year(d) as year, quarter(d) as quarter,
       extract(YEAROFWEEK from d) as iso_year, weekofyear(d) as iso_week, date_format(d, 'EEEE') as day_name,
       dayofweek(d) in (1, 7) as is_weekend
from (select explode(sequence((select min(invoice_date) from fact_lines),
                              (select max(invoice_date) from fact_lines), interval 1 day)) as d);

create or replace table fact_invoice as
select invoice_no, min(invoiced_at) as invoiced_at, min(invoice_date) as invoice_date,
       any_value(customer_id) as customer_id, any_value(country) as country, any_value(customer_type) as customer_type,
       invoice_no like 'C%' as is_credit_note, count(*) as n_lines,
       coalesce(sum(amount) filter (where line_type = 'sale'), 0)   as sale_amount,
       coalesce(sum(amount) filter (where line_type = 'return'), 0) as return_amount,
       coalesce(sum(amount) filter (where line_type not in ('sale', 'return')), 0) as other_amount,
       sum(amount) as net_amount
from fact_lines group by invoice_no;
