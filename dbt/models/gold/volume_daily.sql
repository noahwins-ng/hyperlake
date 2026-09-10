{{ config(**materialization_for_target(kind='table')) }}

select
    coin,
    date_trunc('day', time) as day,
    sum(sz) as volume
from {{ ref('trades') }}
group by coin, date_trunc('day', time)
