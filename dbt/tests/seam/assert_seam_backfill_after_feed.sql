-- ADR-002 seam test, case (b): tid 1002's round-1 `ws` row is followed in
-- round 2 by a `backfill` row for the same trade -- backfill outranks ws (ADR-005), so
-- the merged row must carry backfill's flags. Singular dbt test convention: rows
-- returned are failures.
select *
from {{ ref('seam_trades') }}
where
    tid = 1002
    and (source != 'backfill' or crossed != true or liquidation != true)
