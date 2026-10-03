{{ config(**materialization_for_target(kind='merge', unique_key=['dt', 'coin'])) }}

select
    cast(null as date) as dt,
    cast(null as varchar) as coin,
    cast(null as bigint) as ws_rows,
    cast(null as bigint) as backfill_rows,
    cast(null as double) as duplicate_rate,
    cast(null as double) as gap_minutes,
    cast(null as double) as median_archive_lag_seconds
where false
