select
    order_id,
    count(*)                      as line_count,
    count(distinct category)      as category_count,
    sum(quantity)                 as units,
    sum(gross_revenue)            as gross_revenue,
    sum(net_revenue)              as net_revenue,
    sum(cost)                     as cost,
    sum(gross_margin)             as gross_margin
from {{ ref('int_order_lines') }}
group by order_id
