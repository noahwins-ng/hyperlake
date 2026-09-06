{{ config(**materialization_for_target(kind='merge', unique_key='tid')) }}

{% if target.type == 'athena' %}
{{ config(
    partitioned_by=['coin', "day(time)"],
    incremental_predicates=[
        "target.time >= cast(date_add('day', -" ~ var('silver_lookback_days') ~ ", current_date) as timestamp)"
    ],
    merge_exclude_columns=['first_seen_source'],
    update_condition='src.source_rank >= target.source_rank',
) }}
{% endif %}

-- Source-side pre-dedup runs on both targets: an Athena MERGE errors if `src` matches a
-- target row more than once, and the incoming batch re-reads the whole lookback window on
-- every run, so it commonly holds more than one row per `tid` (a prior ws row plus a later
-- backfill row for the same trade). ADR-005: backfill outranks ws (source_rank); ties break
-- on the most recently ingested row.
with bronze as (

    select *
    from {{ source('bronze', 'trades_raw') }}
    {% if target.type == 'athena' %}
    where dt >= date_add('day', -{{ var('silver_lookback_days') }}, current_date)
    {% endif %}

),

ranked as (

    select
        tid,
        coin,
        side,
        px,
        sz,
        time,
        hash,
        crossed,
        liquidation,
        fee,
        source,
        case source
            when 'backfill' then 2
            when 'ws' then 1
        end as source_rank,
        source as first_seen_source,
        row_number() over (
            partition by tid
            order by
                case source when 'backfill' then 2 when 'ws' then 1 end desc,
                ingested_at desc
        ) as rn
    from bronze

)

select
    tid,
    coin,
    side,
    px,
    sz,
    time,
    hash,
    crossed,
    liquidation,
    fee,
    source,
    source_rank,
    first_seen_source
from ranked
where rn = 1
