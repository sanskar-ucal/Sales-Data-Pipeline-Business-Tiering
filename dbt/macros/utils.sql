{% macro safe_divide(numerator, denominator) -%}
    case when ({{ denominator }}) = 0 or ({{ denominator }}) is null then null
         else cast({{ numerator }} as {{ dbt.type_float() }}) / ({{ denominator }}) end
{%- endmacro %}

{% macro month_start(column) -%}
    cast({{ dbt.date_trunc('month', column) }} as date)
{%- endmacro %}

{% macro latest_record(relation, key, order_by='updated_at desc, _loaded_at desc') -%}
    select * from (
        select *, row_number() over (partition by {{ key }} order by {{ order_by }}) as _row_num
        from {{ relation }}
    ) ranked
    where _row_num = 1
{%- endmacro %}

{% macro is_recognized(status_column) -%}
    {{ status_column }} in ({% for s in var('recognized_statuses') %}'{{ s }}'{% if not loop.last %}, {% endif %}{% endfor %})
{%- endmacro %}
