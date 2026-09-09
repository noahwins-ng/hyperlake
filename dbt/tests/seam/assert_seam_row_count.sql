-- ADR-002 seam test (QNT-462): round 2 merges into an existing 3-row target (tids
-- 1001/1002/1003) without inserting or dropping rows -- a merge bug that duplicated
-- the late feed row (case a) or dropped a row would slip past the other singular tests,
-- which only check per-tid column values. Singular dbt test convention: a result set
-- means failure, so this selects a literal row when the count is wrong.
select 'seam_trades row count != 3, got ' || cast(count(*) as varchar) as failure
from {{ ref('seam_trades') }}
having count(*) != 3
