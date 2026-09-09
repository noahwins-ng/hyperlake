-- ADR-003 / G3: an archive-only trade is legitimate only if it falls inside a WS
-- gap interval recorded in the session manifest (`session_gaps` seed); anything
-- outside a recorded gap is a real miss on the live side.
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
            and r.time < g.gap_end
    )
