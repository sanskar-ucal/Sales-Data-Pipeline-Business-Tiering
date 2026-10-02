-- Order-level net revenue must equal the sum of its lines.
select o.order_id, o.net_revenue, sum(l.net_revenue) as line_total
from {{ ref('fct_orders') }} o
join {{ ref('fct_order_lines') }} l on l.order_id = o.order_id
group by o.order_id, o.net_revenue
having abs(o.net_revenue - sum(l.net_revenue)) > 0.01
