-- ADR-003 / G3: a live-only trade with no backfill counterpart in the reconcilable
-- window is a real miss on the archive side -- this must never happen. Singular dbt
-- test convention: rows returned here are failures.
select *
from {{ ref('recon_trades') }}
where membership = 'ws_only'
