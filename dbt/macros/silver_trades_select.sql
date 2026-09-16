{#
  The silver merge's select body (source-side pre-dedup, source_rank precedence,
  first_seen_source lineage), factored out of models/silver/trades.sql so the Athena seam
  test (models/seam/seam_trades.sql) runs the exact same logic silver runs, over a
  different source -- a hand-copied twin could drift from silver's real behavior and the
  seam test would stop proving anything.
#}
{% macro silver_trades_select(source_relation) %}

with bronze as (

    select *
    from {{ source_relation }}

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

{% endmacro %}
