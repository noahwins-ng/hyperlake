{{ config(**materialization_for_target(unique_key='tid')) }}

select
    tid,
    coin,
    side,
    px,
    sz,
    time
from {{ source('fixtures', 'trades_sample') }}
