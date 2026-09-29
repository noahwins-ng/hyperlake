"""`gap_recorded` log events -> manifest `gaps[]`: the per-coin `coin` field survives, and
a pre-QNT-480 event without one still collects (as a coin-less gap covering every coin)."""

import json
from datetime import UTC, datetime
from unittest import mock

from scripts import session_down


def test_collects_coin_from_gap_recorded_events():
    events = [
        {
            "message": json.dumps(
                {
                    "event": "gap_recorded",
                    "coin": "ETH",
                    "gap_start": 0,
                    "gap_end": 534,
                    "healed": False,
                }
            )
        },
        {
            "message": json.dumps(
                {"event": "gap_recorded", "gap_start": 0, "gap_end": 1000, "healed": False}
            )
        },
    ]
    logs = mock.Mock()
    logs.filter_log_events.return_value = {"events": events}

    with mock.patch.object(session_down.boto3, "client", return_value=logs):
        gaps = session_down._real_collect_gaps(
            "s", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
        )

    assert gaps == [
        {
            "coin": "ETH",
            "start": "1970-01-01T00:00:00Z",
            "end": "1970-01-01T00:00:00.534000Z",
            "healed": False,
        },
        {
            "coin": None,
            "start": "1970-01-01T00:00:00Z",
            "end": "1970-01-01T00:00:01Z",
            "healed": False,
        },
    ]
