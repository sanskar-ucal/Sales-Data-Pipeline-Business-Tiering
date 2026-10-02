-- Published tier dataset: model output joined to customer attributes and prior tier.
with history as (
    select
        customer_id,
        as_of_date,
        tier,
        lag(tier) over (partition by customer_id order by as_of_date)       as previous_tier,
        lag(tier_rank) over (partition by customer_id order by as_of_date)  as previous_tier_rank
    from {{ source('tiering', 'customer_tier_history') }}
)

select
    t.customer_id,
    c.business_name,
    c.industry,
    c.region,
    c.company_size_band,
    c.account_manager,
    t.as_of_date,
    t.composite_score,
    t.score_percentile,
    t.tier,
    t.tier_rank,
    h.previous_tier,
    case
        when h.previous_tier is null then 'New'
        when t.tier_rank < h.previous_tier_rank then 'Upgraded'
        when t.tier_rank > h.previous_tier_rank then 'Downgraded'
        else 'Stable'
    end                                     as tier_movement,
    t.segment,
    t.monetary_12m,
    t.frequency_12m,
    t.avg_order_value,
    t.recency_days,
    t.gross_margin_pct,
    t.revenue_growth,
    t.category_breadth,
    t.active_month_ratio,
    t.late_payment_rate,
    t.return_rate,
    t.tenure_days,
    c.lifetime_revenue,
    c.is_active,
    t.run_id,
    t.scored_at
from {{ source('tiering', 'customer_tiers') }} t
left join {{ ref('dim_customers') }} c on c.customer_id = t.customer_id
left join history h on h.customer_id = t.customer_id and h.as_of_date = t.as_of_date
