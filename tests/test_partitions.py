import pytest

from hyperlake.partitions import (
    coin_from_partition_value,
    coin_partition_value,
    dt_from_event_time_ms,
)
from hyperlake.watchlist import load_watchlist


def test_coin_partition_value_normalises_colon():
    assert coin_partition_value("xyz:SP500") == "xyz_SP500"


def test_coin_partition_value_leaves_plain_markets_untouched():
    assert coin_partition_value("BTC") == "BTC"


@pytest.mark.parametrize("coin", load_watchlist())
def test_coin_partition_value_round_trips_for_every_watchlist_market(coin):
    watchlist = load_watchlist()
    value = coin_partition_value(coin)
    assert coin_from_partition_value(value, watchlist) == coin


def test_coin_from_partition_value_rejects_unknown_value():
    with pytest.raises(ValueError):
        coin_from_partition_value("not_a_market", load_watchlist())


def test_coin_from_partition_value_rejects_ambiguous_collision():
    with pytest.raises(ValueError):
        coin_from_partition_value("xyz_SP500", ["xyz:SP500", "xyz_SP500"])


def test_dt_from_event_time_ms_uses_utc_date():
    # 2026-09-04T12:00:00Z
    assert dt_from_event_time_ms(1788523200000) == "2026-09-04"


def test_dt_from_event_time_ms_boundary_lands_in_prior_day():
    # 2026-09-04T23:59:59.900Z must land on 2026-09-04, not the 5th.
    ms = 1788566399900
    assert dt_from_event_time_ms(ms) == "2026-09-04"


def test_dt_from_event_time_ms_just_after_midnight_lands_in_next_day():
    # 2026-09-05T00:00:00.100Z
    ms = 1788566400100
    assert dt_from_event_time_ms(ms) == "2026-09-05"
