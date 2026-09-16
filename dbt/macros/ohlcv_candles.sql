{#
  Shared candle-aggregation body for ohlcv_1m/1h/1d -- one macro so the three grains
  stay identical except the bucket unit (same reasoning as silver_trades_select: a hand-copied
  triplet could drift between grains). Bucketed on `time` only (NFR-5), never `ingested_at`.
  open/close are picked via row_number ties on time (px as a deterministic tiebreak) rather
  than an engine-specific arg_min/arg_max (duckdb) vs. min_by/max_by (Presto/Athena) function,
  so the same SQL compiles identically on both targets.
#}
{% macro ohlcv_candles(grain) %}

with trades as (

    select
        coin,
        date_trunc('{{ grain }}', time) as bucket,
        px,
        sz,
        time
    from {{ ref('trades') }}

),

ranked as (

    select
        *,
        row_number() over (partition by coin, bucket order by time asc, px asc) as rn_open,
        row_number() over (partition by coin, bucket order by time desc, px desc) as rn_close
    from trades

)

select
    coin,
    bucket,
    max(case when rn_open = 1 then px end) as open,
    max(px) as high,
    min(px) as low,
    max(case when rn_close = 1 then px end) as close,
    sum(sz) as volume
from ranked
group by coin, bucket

{% endmacro %}
