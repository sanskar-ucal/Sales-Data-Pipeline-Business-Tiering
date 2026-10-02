-- Order lines priced net of order-level discount, with standard cost and margin.
select
    i.order_item_id,
    i.order_id,
    o.customer_id,
    o.order_date,
    o.order_month,
    o.status,
    i.product_id,
    p.category,
    i.quantity,
    i.unit_price,
    i.line_amount                                         as gross_revenue,
    i.line_amount * (1 - o.discount_pct)                  as net_revenue,
    i.quantity * p.unit_cost                              as cost,
    i.line_amount * (1 - o.discount_pct) - i.quantity * p.unit_cost as gross_margin
from {{ ref('stg_order_items') }} i
join {{ ref('stg_orders') }} o on o.order_id = i.order_id
join {{ ref('stg_products') }} p on p.product_id = i.product_id
