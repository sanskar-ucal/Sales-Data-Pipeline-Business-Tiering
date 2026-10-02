with latest as (
    {{ latest_record(source('raw', 'customers'), 'customer_id') }}
)

select
    customer_id,
    trim(business_name)                                  as business_name,
    industry,
    region,
    employee_count,
    case
        when employee_count is null then 'Unknown'
        when employee_count < 50 then 'Small (<50)'
        when employee_count < 250 then 'Mid-market (50-249)'
        else 'Enterprise (250+)'
    end                                                  as company_size_band,
    signup_date,
    account_manager,
    updated_at,
    _loaded_at
from latest
