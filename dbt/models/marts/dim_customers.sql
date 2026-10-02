with order_stats as (
    select
        customer_id,
        min(order_date)                                          as first_order_date,
        max(case when {{ is_recognized('status') }} then order_date end) as last_order_date,
        sum(case when {{ is_recognized('status') }} then 1 else 0 end)   as lifetime_orders,
        sum(recognized_revenue)                                  as lifetime_revenue,
        sum(recognized_margin)                                   as lifetime_margin
    from {{ ref('fct_orders') }}
    group by customer_id
),

as_of as (
    select max(order_date) as as_of_date from {{ ref('fct_orders') }}
)

select
    c.customer_id,
    c.business_name,
    c.industry,
    c.region,
    c.employee_count,
    c.company_size_band,
    c.account_manager,
    c.signup_date,
    s.first_order_date,
    s.last_order_date,
    coalesce(s.lifetime_orders, 0)                               as lifetime_orders,
    coalesce(s.lifetime_revenue, 0)                              as lifetime_revenue,
    coalesce(s.lifetime_margin, 0)                               as lifetime_margin,
    case when s.last_order_date is not null
         then {{ dbt.datediff('s.last_order_date', 'a.as_of_date', 'day') }} end as days_since_last_order,
    coalesce(
        s.last_order_date > cast({{ dbt.dateadd('day', -var('active_window_days'), 'a.as_of_date') }} as date),
        false)                                                   as is_active
from {{ ref('stg_customers') }} c
cross join as_of a
left join order_stats s on s.customer_id = c.customer_id
