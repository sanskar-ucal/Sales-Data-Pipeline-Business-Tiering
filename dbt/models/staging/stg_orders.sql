with latest as (
    {{ latest_record(source('raw', 'orders'), 'order_id') }}
),

typed as (
    select
        order_id,
        customer_id,
        order_date,
        lower(status)                                                   as status,
        channel,
        cast(discount_pct as {{ dbt.type_float() }})                    as discount_pct,
        payment_terms_days,
        cast({{ dbt.dateadd('day', 'payment_terms_days', 'order_date') }} as date) as due_date,
        paid_date,
        updated_at,
        _loaded_at
    from latest
)

select
    *,
    {{ month_start('order_date') }}                                     as order_month,
    paid_date is not null                                               as is_paid,
    case when paid_date is not null
         then {{ dbt.datediff('order_date', 'paid_date', 'day') }} end  as days_to_pay,
    case when paid_date is not null then paid_date > due_date end       as is_late_payment
from typed
