{{ config(tags=['post_tiering']) }}
-- A better tier must never contain a lower composite score than the next tier's maximum.
with bounds as (
    select tier_rank, min(composite_score) as min_score, max(composite_score) as max_score
    from {{ ref('mart_customer_tiers') }}
    group by tier_rank
)
select better.tier_rank, better.min_score, worse.max_score
from bounds better
join bounds worse on worse.tier_rank = better.tier_rank + 1
where better.min_score < worse.max_score
