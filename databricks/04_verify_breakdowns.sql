select 'verdict' k, verdict v, count(*) n from line_classified group by verdict
union all select 'segment', segment, count(*) from mart_customer_rfm group by segment
union all select 'abc', abc_class, count(*) from mart_product group by abc_class
order by k, n desc;
