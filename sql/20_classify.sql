-- Layer 2: classify every raw line as kept (with a line type) or quarantined (with a reason).
create or replace table country_map as select * from read_csv('{{SEEDS}}/country_map.csv', header = true);
create or replace table stock_code_types as select * from read_csv('{{SEEDS}}/stock_code_types.csv', header = true);

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
