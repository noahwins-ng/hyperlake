-- HIP-3 markets keep their exact exchange name (`xyz:SP500`) in silver and gold `coin`;
-- only bronze's partition key is normalised (`xyz_SP500`, hyperlake.partitions). On Athena
-- bronze exposes `coin` only as that partition key, so a model reading it unmapped leaks the
-- partition value downstream (found 2026-09-29: silver and gold held `xyz_SP500`).
{% set models = ['trades', 'ohlcv_1m', 'ohlcv_1h', 'ohlcv_1d', 'volume_daily', 'liquidations_daily'] %}
{% for m in models %}
select '{{ m }}' as model, coin
from {{ ref(m) }}
where strpos(coin, '_') > 0 and strpos(coin, ':') = 0
{% if not loop.last %}union all{% endif %}
{% endfor %}
