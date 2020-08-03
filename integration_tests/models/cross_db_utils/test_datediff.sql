
with data as (

    select * from {{ ref('data_datediff') }}

)

select
        case
            when datepart = 'hour' then {{ dbt_utils.datediff('first_date', 'second_date', 'hour') }}
            when datepart = 'day' then {{ dbt_utils.datediff('first_date', 'second_date', 'day') }}
            when datepart = 'month' then {{ dbt_utils.datediff('first_date', 'second_date', 'month') }}
            when datepart = 'year' then {{ dbt_utils.datediff('first_date', 'second_date', 'year') }}
            else null
        end as actual,
        result as expected

from data
