-- HIP-3 markets keep their exact exchange name (`xyz:SP500`) in silver and gold `coin`;
-- only bronze's partition key is normalised (`xyz_SP500`, hyperlake.partitions). On Athena
-- bronze exposes `coin` only as that partition key, so a model reading it unmapped leaks the
-- partition value downstream (found 2026-09-29: silver and gold held `xyz_SP500`).
select 'silver.trades' as model, coin
from {{ ref('trades') }}
where strpos(coin, '_') > 0 and strpos(coin, ':') = 0

union all

select 'gold.volume_daily' as model, coin
from {{ ref('volume_daily') }}
where strpos(coin, '_') > 0 and strpos(coin, ':') = 0
