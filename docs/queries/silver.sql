-- Sample Athena queries over silver.trades (FR-5). Run in the `hyperlake` workgroup.
-- The `-2` below mirrors dbt/dbt_project.yml's `silver_lookback_days` default — update
-- both if that default changes.

-- Exactly-once check: row count equals distinct tid count (proves the merge, QNT-453 AC2).
select count(*) as row_count, count(distinct tid) as distinct_tid
from silver.trades
where coin = 'BTC';

-- Lineage: trades first seen via backfill vs. ws, over the default lookback window.
select first_seen_source, count(*)
from silver.trades
where time >= date_add('day', -2, current_date)
group by first_seen_source;

-- Bytes-scanned check for the target-side partition prune (QNT-453 AC7): run this against
-- `SHOW CREATE TABLE silver.trades` / query execution stats, not the row output.
select coin, day(time) as trade_day, count(*)
from silver.trades
where time >= date_add('day', -2, current_date)
group by coin, day(time);
