-- G5 (PRD): freshness = max event `time` in silver is within the session window
-- (var('freshness_window_start')..var('freshness_window_end')), allowing up to
-- `freshness_max_lag_minutes` of catch-up lag before the window's end. A session var
-- moved past what silver has actually ingested must fail loud, not silently report
-- stale data as fresh. Singular test convention: rows returned here are failures.
select max(time) as latest_time
from {{ ref('trades') }}
where
    time >= timestamp '{{ var("freshness_window_start") }}'
    and time < timestamp '{{ var("freshness_window_end") }}'
having
    max(time) is null
    or max(time) < timestamp '{{ var("freshness_window_end") }}'
        - interval '{{ var("freshness_max_lag_minutes") }}' minute
