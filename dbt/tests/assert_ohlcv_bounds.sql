-- Gold invariant (PRD G5): every candle's open/close must fall within its own
-- [low, high] range. True by construction when open/high/low/close all derive from the
-- same bucket's trade prices (dbt/macros/ohlcv_candles.sql) -- a row here means the
-- derivation drifted. Singular dbt test convention: rows returned here are failures.
with candles as (

    select coin, bucket, open, high, low, close from {{ ref('ohlcv_1m') }}
    union all
    select coin, bucket, open, high, low, close from {{ ref('ohlcv_1h') }}
    union all
    select coin, bucket, open, high, low, close from {{ ref('ohlcv_1d') }}

)

select *
from candles
where not (low <= open and open <= high and low <= close and close <= high)
