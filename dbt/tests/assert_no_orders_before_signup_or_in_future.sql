-- Orders cannot predate the customer's signup or be dated after the load time.
select o.order_id, o.order_date, c.signup_date
from {{ ref('fct_orders') }} o
join {{ ref('dim_customers') }} c on c.customer_id = o.customer_id
where o.order_date < c.signup_date
   or o.order_date > cast({{ dbt.current_timestamp() }} as date)
