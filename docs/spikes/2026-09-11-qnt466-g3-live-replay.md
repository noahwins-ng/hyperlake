# Spike: G3 replay on a live session (QNT-466)

- **Date:** 2026-09-11
- **Method:** ran the full real sequence on session `qnt-466-20260911124320` --
  `session-up` → stream 12:43–14:06 UTC (83 min, one induced disconnect) →
  `session-down` → wait for the archive → `heal` → `recon` -- against real AWS
  infra in ap-northeast-1, no fixtures.
- **Outcome:** **PASS, with one documented residual.** `assert_ws_only_zero` is
  clean. `assert_backfill_only_within_gaps` has exactly one row out of ~146K
  reconciled trades that isn't a real miss (see "The one residual" below). Four
  real bugs found and fixed live (all previously invisible because no session had
  ever run long enough to observe a full clock hour -- QNT-460's open AC1 note).

## Timeline

| UTC | Event |
|---|---|
| 12:43:20 | `session-up` (label `qnt-466`) |
| 12:48–12:55 | Two failed disconnect-induction attempts (see below) |
| 13:21:10–13:30:35 | NACL egress block (~9.4 min) -- the induced disconnect that stuck |
| 13:21:11.624–13:30:46.517 | Gap the ingester actually recorded (`gap_recorded`) |
| 14:06:00 | `session-down` triggered (session spans the full 13:00–14:00 clock hour) |
| 15:05 / 16:02 | Archive hour files 13 and 14 land |
| 16:10–17:05 | Six `make heal` attempts -- backfill crash, freshness-test bug, ref-pin
  bug, boundary semantics, in that order (see below) |
| 17:05 | Final recon: `ws_only=0`, `backfill_only=13577` (one residual) |

## Inducing a real disconnect -- three attempts, two dead ends

The ticket's suggested mechanism, `stop the task once`, **does not produce a
recorded gap**: `hyperlake.ingester.GapTracker` is in-process state. ECS SIGTERMs
the task, `asyncio`'s receive-timeout loop notices `_stop` within 5 s and exits
*cleanly* (no `websockets` exception, so `on_disconnect()` never fires), and the
replacement task starts a brand-new process with a fresh tracker. Confirmed live:
stopping the task produced a clean `self_exit` and a `subscribed` with a new
`session_id` -- no `disconnected`/`reconnected`/`gap_recorded` anywhere in between.

A security-group egress revoke doesn't work either: security groups are
*stateful*, so revoking the rule doesn't affect an already-established connection
-- `put_records` kept flowing the whole ~90 s window, uninterrupted.

What actually works: a **subnet NACL deny-all-egress rule** (stateless, evaluated
per-packet regardless of connection state). A short (~90 s) block only delayed
one `put_records` call (`latency_ms: 130769`) without tripping `websockets`'
ping/pong keepalive timeout -- the OS TCP stack just queued and retried. A
longer (~9.4 min) block did: `disconnected` (`timed out during opening
handshake`) four times at 30 s backoff, then `reconnected` + `gap_recorded` on
the same process (`session_id` unchanged throughout all three attempts).
**Neither of the two AWS network levers here is quick** -- planning a future
induced-disconnect session should budget for the long block, not the short one.

## Bugs found and fixed live

Every fix below was a one-off scoped exception to this ticket's normal
no-code-change rule, approved live given the ticket's alternative (a
same-domain follow-up ticket) would have just re-discovered the same four
things on a second live session.

1. **Backfill Lambda crash on liquidation-shaped fills**
   (`src/hyperlake/backfill/official.py`). This session's real archive data
   contained a liquidation-driven trade whose `liquidation` field is a nested
   `{liquidatedUser, markPx, method}` object, not the bare bool the schema
   declares (`pa.bool_()`) and the 2026-09-04 spike's sample assumed. Crashed
   the whole hour's Arrow/Parquet write, not just that row. Fixed by coercing
   to presence/absence; `raw_payload` keeps the full detail.
2. **`assert_silver_freshness` always fails on a real session**
   (`scripts/session_down.py`, `scripts/heal.py`). Neither script passed the
   session's real `freshness_window_start/end` to its `dbt-run`, so the test
   checked against `dbt_project.yml`'s placeholder demo-fixture date
   (`2026-02-01`) every time -- guaranteed failure regardless of actual
   freshness. Fixed by passing the real window from the manifest.
3. **`scripts/gh_run.sh` doesn't pin `--ref`**. `gh workflow run` without
   `--ref` dispatches against the repo's default branch, not the caller's.
   `scripts/regen_recon_seed.py` (the CI pre-step that rebuilds
   `dbt/seeds/session_gaps.csv` from `sessions/<id>.json`) silently no-ops when
   that file doesn't exist on the checked-out ref -- true for any session's
   manifest until its branch merges. Every recon `dbt-run` from this feature
   branch was silently checking `main`, which had never seen this session's gap,
   so `assert_backfill_only_within_gaps` flagged 13,577 rows as unexplained
   (every `backfill_only` row in the window -- there was no gap seed at all).
   Fixed by pinning `--ref "$(git rev-parse --abbrev-ref HEAD)"`; needed the
   branch actually pushed to resolve (it wasn't yet -- pushed as part of this
   fix).
4. **G3's gap upper bound was exclusive**
   (`dbt/tests/assert_backfill_only_within_gaps.sql`). Once (3) was fixed, the
   13,577 dropped to 8 -- all seven BTC + one at the *exact* millisecond of
   `gap_end` (the event time of the trade that closed the gap). The resubscribe
   isn't instantaneous, so other real trades sharing that millisecond can still
   be archive-only. Made the upper bound inclusive (`<=`); dropped the 8 to 1.

## The one residual

One ETH trade (`tid 1050395612332311`) at `13:30:47.051` -- **534 ms after**
`gap_end` (13:30:46.517, a BTC trade). `GapTracker` keeps one shared gap
timeline across all five watchlist coins on the single WS connection; it closes
on whichever coin's trade is first observed after reconnect. Here that was BTC,
but ETH's own first post-reconnect trade landed slightly later, in the same
catch-up window. Not fixed live -- widening the boundary further means picking
an arbitrary grace period, which trades real detection power for a cleaner
demo run on this one session. Left for a follow-up ticket: either a small
explicit grace window (bounded, so it can't mask a real multi-second miss) or
per-coin gap tracking (more precise, more state).

## Final numbers (session `qnt-466-20260911124320`, window 13:00–14:00 UTC)

- `ws_only = 0` (clean pass)
- `backfill_only = 13577` (13,576 inside the recorded gap; the one residual above)
- `both = 132823`
- Silver exactly-once over the full session window (12:43:20–14:08:29):
  `count(*) = count(distinct tid) = 225,290`
- Cost estimate: $0.21 (single session, four `heal` ephemeral re-applies included)

## Consequences

- QNT-460 AC3 is now genuinely observed (not just unit-tested): `make recon`
  writes real `ws_only`/`backfill_only`/`both` into a real manifest.
- The induced-disconnect mechanism in any future runbook/demo script should be
  the NACL approach with a several-minute block, not `stop the task` -- update
  `docs/guides/ops-runbook.md` if a repeatable induction procedure is wanted.
- Three of the four fixes (freshness vars, `--ref` pinning, gap boundary) are
  now load-bearing for *every* future real session run from a feature branch,
  not just this one.
