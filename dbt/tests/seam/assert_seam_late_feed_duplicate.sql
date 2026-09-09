-- ADR-002 seam test (QNT-462), case (a): tid 1001's round-2 row is a late `ws`
-- re-delivery of the same trade (identical values, later ingested_at) -- it must not
-- change any value on the merged row. Singular dbt test convention: rows returned are
-- failures.
select *
from {{ ref('seam_trades') }}
where
    tid = 1001
    and (source != 'ws' or crossed != false or liquidation != false or first_seen_source != 'ws')
