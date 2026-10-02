{% snapshot customers_snapshot %}
{{
    config(
        unique_key='customer_id',
        strategy='timestamp',
        updated_at='updated_at',
    )
}}
-- SCD2 history of customer attributes (industry, region, account manager, size).
select customer_id, business_name, industry, region, employee_count, company_size_band,
       account_manager, signup_date, updated_at
from {{ ref('stg_customers') }}
{% endsnapshot %}
