-- Gold invariant (PRD G5): a candle's `volume` must equal the sum of silver `sz` over the
-- same coin/bucket window, for every grain. Singular dbt test convention: rows returned
-- here are failures.
with expected_1m as (

    select coin, date_trunc('minute', time) as bucket, sum(sz) as expected_volume
    from {{ ref('trades') }}
    group by coin, date_trunc('minute', time)

),

expected_1h as (

    select coin, date_trunc('hour', time) as bucket, sum(sz) as expected_volume
    from {{ ref('trades') }}
    group by coin, date_trunc('hour', time)

),

expected_1d as (

    select coin, date_trunc('day', time) as bucket, sum(sz) as expected_volume
    from {{ ref('trades') }}
    group by coin, date_trunc('day', time)

),

checked as (

    select e.coin, e.bucket, e.expected_volume, a.volume
    from expected_1m as e
    left join {{ ref('ohlcv_1m') }} as a on a.coin = e.coin and a.bucket = e.bucket

    union all

    select e.coin, e.bucket, e.expected_volume, a.volume
    from expected_1h as e
    left join {{ ref('ohlcv_1h') }} as a on a.coin = e.coin and a.bucket = e.bucket

    union all

    select e.coin, e.bucket, e.expected_volume, a.volume
    from expected_1d as e
    left join {{ ref('ohlcv_1d') }} as a on a.coin = e.coin and a.bucket = e.bucket

)

select *
from checked
where volume is null or expected_volume is distinct from volume
