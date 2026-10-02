select
    p.product_id,
    p.product_name,
    p.category,
    p.unit_cost,
    p.list_price,
    p.list_margin_pct,
    coalesce(sum(case when {{ is_recognized('l.status') }} then l.quantity end), 0)    as units_sold,
    coalesce(sum(case when {{ is_recognized('l.status') }} then l.net_revenue end), 0) as net_revenue
from {{ ref('stg_products') }} p
left join {{ ref('int_order_lines') }} l on l.product_id = p.product_id
group by 1, 2, 3, 4, 5, 6
