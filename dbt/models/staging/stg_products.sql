with latest as (
    {{ latest_record(source('raw', 'products'), 'product_id') }}
)

select
    product_id,
    product_name,
    category,
    cast(unit_cost as {{ dbt.type_numeric() }})          as unit_cost,
    cast(list_price as {{ dbt.type_numeric() }})         as list_price,
    {{ safe_divide('list_price - unit_cost', 'list_price') }} as list_margin_pct,
    updated_at
from latest
