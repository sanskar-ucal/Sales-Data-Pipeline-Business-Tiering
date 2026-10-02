with totals as (
    select sum(monetary_12m) as total_revenue, count(*) as total_customers
    from {{ ref('mart_customer_tiers') }}
)

select
    t.tier,
    t.tier_rank,
    count(*)                                                     as customers,
    {{ safe_divide('count(*)', 'max(tot.total_customers)') }}    as customer_share,
    avg(t.composite_score)                                       as avg_composite_score,
    min(t.composite_score)                                       as min_composite_score,
    sum(t.monetary_12m)                                          as revenue_12m,
    {{ safe_divide('sum(t.monetary_12m)', 'max(tot.total_revenue)') }} as revenue_share,
    avg(t.monetary_12m)                                          as avg_revenue_12m,
    avg(t.frequency_12m)                                         as avg_orders_12m,
    avg(t.gross_margin_pct)                                      as avg_gross_margin_pct,
    avg(t.revenue_growth)                                        as avg_revenue_growth,
    avg(t.late_payment_rate)                                     as avg_late_payment_rate,
    avg(t.recency_days)                                          as avg_recency_days,
    sum(case when t.tier_movement = 'Upgraded' then 1 else 0 end)   as upgraded,
    sum(case when t.tier_movement = 'Downgraded' then 1 else 0 end) as downgraded
from {{ ref('mart_customer_tiers') }} t
cross join totals tot
group by t.tier, t.tier_rank
