"""Partition-key helpers shared by the ingester and the backfill Lambda.

The one place that normalises a HIP-3 market name's colon separator to its partition
value's underscore, and derives `dt` from exchange event time — never
arrival/`ingested_at` (CLAUDE.md, PRD NFR-5).
"""

from collections.abc import Iterable
from datetime import UTC, datetime


def coin_partition_value(coin: str) -> str:
    """`coin=` partition value for a market name (HIP-3 markets use ':' as a separator)."""
    return coin.replace(":", "_")


def coin_from_partition_value(value: str, watchlist: Iterable[str]) -> str:
    """Inverse of `coin_partition_value`, resolved against the known watchlist markets."""
    mapping: dict[str, str] = {}
    for coin in watchlist:
        partition_value = coin_partition_value(coin)
        if partition_value in mapping and mapping[partition_value] != coin:
            raise ValueError(
                f"partition value {partition_value!r} is ambiguous between "
                f"{mapping[partition_value]!r} and {coin!r}"
            )
        mapping[partition_value] = coin
    if value not in mapping:
        raise ValueError(f"{value!r} does not match any watchlist market's partition value")
    return mapping[value]


def dt_from_event_time_ms(time_ms: int) -> str:
    """UTC calendar date (`YYYY-MM-DD`) of an exchange event time, in epoch milliseconds."""
    return datetime.fromtimestamp(time_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def hour_from_event_time_ms(time_ms: int) -> str:
    """UTC hour-of-day of an exchange event time, in epoch milliseconds -- matches the
    official archive's hour-file basename convention (no leading zero, e.g. "3" not
    "03"). Used by readers that must derive an object key's `hour=` segment from the
    event itself rather than from an invocation argument (Reservoir fallback, QNT-465)."""
    return str(datetime.fromtimestamp(time_ms / 1000, tz=UTC).hour)
