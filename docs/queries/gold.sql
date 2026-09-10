-- Sample Athena queries over the gold marts (FR-5). Run in the `hyperlake` workgroup.

-- Last 24 hourly candles for one coin (AC3 shape).
select *
from gold.ohlcv_1h
where coin = 'BTC'
order by bucket desc
limit 24;

-- Reconciliation: a day's total ohlcv_1d volume must equal silver's sum(sz) for that
-- coin/day (AC4 shape) -- the singular test assert_ohlcv_volume_reconciles proves this
-- for every grain on every build; this is the same check run by hand.
select
    o.day,
    o.volume as gold_volume,
    s.silver_volume
from gold.ohlcv_1d as o
inner join (
    select coin, date_trunc('day', time) as day, sum(sz) as silver_volume
    from silver.trades
    where coin = 'BTC'
    group by coin, date_trunc('day', time)
) as s on s.coin = o.coin and s.day = o.day
where o.coin = 'BTC'
order by o.day desc;

-- Daily volume per coin, most recent first.
select *
from gold.volume_daily
order by day desc, coin;

-- Liquidation coverage: backfill-only by construction (FR-4) -- a day with zero rows here
-- means either no liquidations occurred or that day isn't backfill-healed yet, not that
-- liquidations definitely didn't happen.
select *
from gold.liquidations_daily
order by day desc, coin;
