-- Layer 1: staging. Type the raw text columns and normalize obvious noise.
-- Nothing is dropped here; every raw row keeps its line_id.
create or replace table raw_lines as
select row_number() over () as line_id, *
from read_csv('{{RAW}}', all_varchar = true, header = true);

create or replace table stg_lines as
select
    line_id,
    trim(Invoice)                                                   as invoice_no,
    upper(trim(StockCode))                                          as stock_code,
    nullif(trim(regexp_replace(Description, '\s+', ' ', 'g')), '')  as description_clean,
    try_cast(Quantity as integer)                                   as quantity,
    try_cast(InvoiceDate as timestamp)                              as invoiced_at,
    try_cast(Price as decimal(18, 4))                               as unit_price,
    try_cast(try_cast("Customer ID" as double) as bigint)           as customer_id,
    trim(Country)                                                   as country_raw,
    Invoice as invoice_raw, StockCode as stock_code_raw, Description as description_raw
from raw_lines;
