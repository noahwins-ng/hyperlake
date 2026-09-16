-- ADR-003 / G3: an archive-only trade is legitimate only if it falls inside a WS
-- gap interval recorded in the session manifest (`session_gaps` seed); anything
-- outside a recorded gap is a real miss on the live side.
--
-- 2026-09-11 live session: `gap_end` is the event `time` of the trade that
-- *closed* the gap (hyperlake.ingester.GapTracker) -- the reconnect resubscribe isn't
-- instantaneous, so other real trades sharing that exact millisecond can still be
-- backfill-only. Inclusive upper bound so the closing millisecond counts as covered,
-- not a real miss (confirmed live: 8 such rows, all at exactly `gap_end`).
select r.*
from {{ ref('recon_trades') }} as r
where
    r.membership = 'backfill_only'
    and not exists (
        select 1
        from {{ ref('session_gaps') }} as g
        where
            g.session_id = r.session_id
            and r.time >= g.gap_start
            and r.time <= g.gap_end
    )
