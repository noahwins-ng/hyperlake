-- ADR-002 seam test (QNT-462), case (c): tid 1003's round-1 `backfill` row is followed
-- in round 2 by a late `ws` row for the same trade -- ws must not downgrade an
-- already-backfilled row (ADR-005: backfill outranks ws). Singular dbt test convention:
-- rows returned are failures.
select *
from {{ ref('seam_trades') }}
where
    tid = 1003
    and (source != 'backfill' or crossed != true or liquidation != true)
