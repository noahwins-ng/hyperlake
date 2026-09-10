{{ config(**materialization_for_target(kind='table')) }}

{{ ohlcv_candles('minute') }}
