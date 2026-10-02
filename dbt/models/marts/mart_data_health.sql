-- Latest result per check plus 30-day pass rate, for the Data Health dashboard.
with results as (
    select
        checked_at,
        cast(checked_at as date) as check_date,
        check_type,
        check_name,
        target,
        severity,
        message,
        observed_value,
        threshold,
        row_number() over (partition by check_type, check_name, target order by checked_at desc) as recency_rank
    from {{ source('monitoring', 'check_results') }}
),

history as (
    select
        check_type,
        check_name,
        target,
        count(*)                                                as runs_30d,
        sum(case when severity = 'pass' then 1 else 0 end)      as passes_30d,
        sum(case when severity = 'warn' then 1 else 0 end)      as warnings_30d,
        sum(case when severity = 'error' then 1 else 0 end)     as errors_30d
    from results
    where checked_at >= {{ dbt.dateadd('day', -30, dbt.current_timestamp()) }}
    group by 1, 2, 3
)

select
    r.check_type,
    r.check_name,
    r.target,
    r.checked_at                                                as last_checked_at,
    r.severity                                                  as last_severity,
    r.message                                                   as last_message,
    r.observed_value,
    r.threshold,
    h.runs_30d,
    h.passes_30d,
    h.warnings_30d,
    h.errors_30d,
    {{ safe_divide('h.passes_30d', 'h.runs_30d') }}             as pass_rate_30d
from results r
left join history h
    on h.check_type = r.check_type and h.check_name = r.check_name and h.target = r.target
where r.recency_rank = 1
