{{ config(**materialization_for_target(kind='table')) }}

-- ADR-003: G3 is proven over bronze (append-only), not silver (whose tid merge
-- destroys the "seen by both paths" evidence). One row per distinct `tid` in the
-- reconcilable window (`var('recon_window_start'/'recon_window_end')`), deduped per
-- source via `group by tid` so live-path bronze duplicates don't inflate `both`
-- (AC4) -- `min()` picks a stable representative row since coin/time are identical
-- across duplicate rows of the same trade.

with bronze as (

    select *
    from {{ source('bronze', 'trades_raw') }}
    where
        time >= timestamp '{{ var("recon_window_start") }}'
        and time < timestamp '{{ var("recon_window_end") }}'

),

ws_tids as (

    select tid, min(coin) as coin, min(time) as time
    from bronze
    where source = 'ws'
    group by tid

),

backfill_tids as (

    select tid, min(coin) as coin, min(time) as time
    from bronze
    where source = 'backfill'
    group by tid

),

unioned as (

    select
        coalesce(w.tid, b.tid) as tid,
        coalesce(w.coin, b.coin) as coin,
        coalesce(w.time, b.time) as time,
        w.tid is not null as in_ws,
        b.tid is not null as in_backfill
    from ws_tids w
    full outer join backfill_tids b on w.tid = b.tid

)

select
    '{{ var("recon_session_id") }}' as session_id,
    tid,
    coin,
    time,
    case
        when in_ws and in_backfill then 'both'
        when in_ws then 'ws_only'
        else 'backfill_only'
    end as membership
from unioned
