-- Sample Athena queries over bronze.trades_raw (FR-5). Run in the `hyperlake` workgroup.

-- Row count for one market/day/source partition (partition projection: no MSCK REPAIR needed).
select count(*)
from bronze.trades_raw
where coin = 'BTC' and dt = date '2026-01-01' and source = 'ws';

-- Duplicate tids the live path wrote for a session hour (expected: at-least-once into
-- bronze; silver resolves these, bronze never does).
select tid, count(*) as n
from bronze.trades_raw
where coin = 'BTC' and dt = date '2026-01-01' and source = 'ws'
group by tid
having count(*) > 1;
