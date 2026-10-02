with latest as (
    {{ latest_record(source('raw', 'order_items'), 'order_item_id') }}
)

select
    order_item_id,
    order_id,
    product_id,
    quantity,
    cast(unit_price as {{ dbt.type_numeric() }})               as unit_price,
    quantity * cast(unit_price as {{ dbt.type_numeric() }})    as line_amount,
    updated_at
from latest
