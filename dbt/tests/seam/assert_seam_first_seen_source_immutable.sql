-- ADR-002 / ADR-003 seam test: `first_seen_source` is insert-only lineage --
-- it must equal the source of each tid's round-1 row regardless of which source won the
-- round-2 merge. Singular dbt test convention: rows returned are failures.
select *
from {{ ref('seam_trades') }}
where
    (tid = 1001 and first_seen_source != 'ws')
    or (tid = 1002 and first_seen_source != 'ws')
    or (tid = 1003 and first_seen_source != 'backfill')
