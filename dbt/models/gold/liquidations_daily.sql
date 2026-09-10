{{ config(**materialization_for_target(kind='table')) }}

-- Backfill-only by construction (FR-4): the live feed carries no liquidation flag, so
-- `liquidation` is only ever true on a row the backfill archive has upserted -- this mart
-- is complete only for coin/day combinations covered by a healed backfill window (see
-- schema.yml).
select
    coin,
    date_trunc('day', time) as day,
    sum(sz) as liquidation_volume,
    count(*) as liquidation_count
from {{ ref('trades') }}
where liquidation
group by coin, date_trunc('day', time)
