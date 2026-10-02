-- Customer x month revenue (sparse: only months with at least one order).
select
    customer_id,
    order_month                                         as month,
    count(*)                                            as orders,
    sum(case when {{ is_recognized('status') }} then 1 else 0 end) as completed_orders,
    sum(recognized_revenue)                             as net_revenue,
    sum(recognized_margin)                              as gross_margin,
    {{ safe_divide('sum(recognized_margin)', 'sum(recognized_revenue)') }} as gross_margin_pct
from {{ ref('fct_orders') }}
group by customer_id, order_month
