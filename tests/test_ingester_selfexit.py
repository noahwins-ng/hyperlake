"""Pins the ingester's self-exit path (QNT-456 AC5, code-level regression): expiring
`--max-session-hours` while idle inside the receive loop must still reach the final
drain + `self_exit` log line, not just the outer reconnect loop's own expiry check.

Caught live (AC5 dev-execution run): the inner loop used `return` on session-expiry,
which unwound `run()` before its trailing `self._flush()` / `log_event("self_exit", ...)`
ever ran -- a live `--max-session-hours` session exited 0, but silently, with no drained-
batch receipt. Fixed to `break`, which falls through to the outer loop's own expiry check.
"""

import asyncio
import json
from unittest import mock

from hyperlake import ingester as ing


class _IdleConn:
    """Every `recv()` blocks past the (short, test-only) recv timeout -- like a live
    connection with no trades ready to deliver."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def send(self, msg: str) -> None:
        pass

    async def recv(self) -> str:
        await asyncio.Event().wait()
        raise AssertionError("unreachable -- the event is never set")


class _FakeKinesis:
    def put_records(self, *, StreamName, Records):
        return {"FailedRecordCount": 0, "Records": [{} for _ in Records]}


def test_session_expiry_while_idle_still_logs_self_exit(capsys):
    ingester = ing.Ingester(
        coins=["BTC"],
        kinesis_client=_FakeKinesis(),
        stream_name="hyperlake-trades",
        recv_timeout_s=0.02,
        max_session_hours=0.02 / 3600,  # expires almost immediately, no request_stop needed
    )

    with mock.patch.object(ing.websockets, "connect", return_value=_IdleConn()):
        asyncio.run(asyncio.wait_for(ingester.run(), timeout=2))

    events = [json.loads(line)["event"] for line in capsys.readouterr().out.splitlines()]
    assert "self_exit" in events
