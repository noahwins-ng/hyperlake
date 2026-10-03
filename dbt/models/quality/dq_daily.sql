{{ config(**materialization_for_target(kind='merge', unique_key=['dt', 'coin'])) }}
{% if target.type == 'athena' %}
-- a new metric column reaches the existing Iceberg table instead of being silently dropped
{{ config(on_schema_change='append_new_columns') }}
{% endif %}

-- Daily data-quality report per (event day, coin), read from bronze so both acquisition
-- paths are still visible. On athena the scan is bounded to whole `dt` partitions inside
-- `var('silver_lookback_days')` (an unbounded bronze scan LISTs every projected partition)
-- and merged on (dt, coin), so each run recomputes complete days and older days keep their
-- last value. `coin` is bronze's, the partition value on athena (`xyz_SP500`), matching
-- `session_gaps.coin`.
with bronze as (

    select
        cast(time as date) as dt,
        coin,
        source,
        tid,
        px,
        sz,
        side,
        date_diff('second', time, ingested_at) as lag_seconds
    from {{ source('bronze', 'trades_raw') }}
    {% if target.type == 'athena' %}
    where dt >= date_add('day', -{{ var('silver_lookback_days') }}, current_date)
    {% endif %}

),

per_day as (

    select
        dt,
        coin,
        count(case when source = 'ws' then 1 end) as ws_rows,
        count(case when source = 'backfill' then 1 end) as backfill_rows,
        count(*) as bronze_rows,
        -- within-source: a tid held once by each path is overlap (recon's concern), not a
        -- duplicate
        count(distinct source || ':' || cast(tid as varchar)) as distinct_source_tids,
        {{ median_of("case when source = 'backfill' then lag_seconds end") }}
            as median_archive_lag_seconds
    from bronze
    group by dt, coin

),

-- ADR-005 corrections: a trade held by both paths whose archive row disagrees with a feed
-- row on price, size or side, so silver's merge replaced the feed value. Keyed on the archive
-- row's day and coin, the row silver keeps.
corrections as (

    select
        b.dt,
        b.coin,
        count(distinct b.tid) as corrected_trades
    from bronze as b
    inner join bronze as w
        on
            b.tid = w.tid
            and w.source = 'ws'
            and (
                b.px is distinct from w.px
                or b.sz is distinct from w.sz
                or b.side is distinct from w.side
            )
    where b.source = 'backfill'
    group by b.dt, b.coin

),

-- A gap counts toward each day it overlaps, clipped to that day; a null `coin` covers
-- every coin (manifests from before per-coin gaps).
gaps as (

    select
        p.dt,
        p.coin,
        sum(
            date_diff(
                'millisecond',
                greatest(g.gap_start, cast(p.dt as timestamp)),
                least(g.gap_end, cast(p.dt as timestamp) + interval '1' day)
            )
        ) as gap_ms
    from per_day as p
    inner join {{ ref('session_gaps') }} as g
        on
            (g.coin is null or g.coin = p.coin)
            and g.gap_start < cast(p.dt as timestamp) + interval '1' day
            and g.gap_end > cast(p.dt as timestamp)
    group by p.dt, p.coin

)

select
    p.dt,
    p.coin,
    p.ws_rows,
    p.backfill_rows,
    cast(p.bronze_rows - p.distinct_source_tids as double) / p.bronze_rows as duplicate_rate,
    cast(coalesce(g.gap_ms, 0) as double) / 60000 as gap_minutes,
    p.median_archive_lag_seconds,
    coalesce(c.corrected_trades, 0) as corrected_trades
from per_day as p
left join gaps as g on p.dt = g.dt and p.coin = g.coin
left join corrections as c on p.dt = c.dt and p.coin = c.coin
