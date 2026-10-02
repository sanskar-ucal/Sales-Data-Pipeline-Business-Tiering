-- Tier transitions between consecutive scoring runs (Sankey / migration matrix input).
with ordered as (
    select
        customer_id,
        as_of_date,
        tier,
        tier_rank,
        lag(tier) over (partition by customer_id order by as_of_date)       as previous_tier,
        lag(tier_rank) over (partition by customer_id order by as_of_date)  as previous_tier_rank,
        lag(as_of_date) over (partition by customer_id order by as_of_date) as previous_as_of_date
    from {{ source('tiering', 'customer_tier_history') }}
)

select
    as_of_date,
    previous_as_of_date,
    coalesce(previous_tier, 'New')      as from_tier,
    tier                                as to_tier,
    case
        when previous_tier is null then 'New'
        when tier_rank < previous_tier_rank then 'Upgraded'
        when tier_rank > previous_tier_rank then 'Downgraded'
        else 'Stable'
    end                                 as movement,
    count(*)                            as customers
from ordered
group by 1, 2, 3, 4, 5
