# ADR-003: Prove batch/stream convergence at bronze, not silver

- **Status:** accepted
- **Date:** 2026-09-04
- **Ticket:**: (PRD v0.5 review; lands in Phase 3)

## Context

G3 is the project's central claim: stream an hour live, backfill the same hour from the
archive, and prove 0 missing / 0 duplicate trades. Earlier PRD drafts placed that proof at
silver. But silver is built by a `MERGE` on `tid`: when the backfilled row arrives for a
`tid` the stream already wrote, the merge upserts and one `source` value wins. After the
merge, silver cannot distinguish "seen by both paths" from "seen by one path only", the
evidence G3 needs is destroyed by the mechanism that produces exactly-once.

## Decision

Reconciliation is asserted over **bronze**, which is append-only and keeps one row per
`tid` per `source`.

- A dbt model **`recon_trades`** groups the target window by **distinct** `tid` per
  source (the live path can write duplicates into bronze) and pivots on `source`,
  yielding `ws_only`, `backfill_only`, and `both` counts.
- The window is the **reconcilable window**: session hours whose archive hour file has
  landed. The trailing partial hour is excluded, otherwise `ws_only` is nonzero by
  construction.
- dbt tests assert **`ws_only = 0`** and **every `backfill_only` trade's `time` lies
  inside a gap interval recorded in the session manifest**. A WS disconnect legitimately
  yields archive-only trades; a `backfill_only` trade outside any recorded gap is a real
  miss. That pair of assertions **is** G3. *(Amended 2026-09-04: the original
  `backfill_only = 0` failed on every legitimate gap.)*
- Backfill writes bronze at deterministic object keys so re-runs overwrite rather than
  append; the live path cannot, hence the distinct-on-`tid` above.
- Bronze is **never pruned** for windows under reconciliation.
- Silver gains **`first_seen_source`**, set from `source` on insert and never updated by
  later merges. It exists for lineage and queries, not as the proof.
- `recon_trades` output is written into the session manifest (FR-8).

## Alternatives considered

- **Keep proving at silver with a `sources` array column**: the merge must then append
  to an array on update, which is awkward in Athena `MERGE` and means the "exactly-once"
  table carries multi-valued lineage. Mixes two concerns in one table.
- **Prove at silver by counting rows before and after the backfill merge**: depends on
  run ordering, and cannot tell a duplicate from a late trade absorbed by the same merge.
- **A separate silver table per source, unioned at gold**: doubles the Iceberg surface
  and moves the dedup problem to gold, which is the opposite of the medallion intent.

## Consequences

- G3 is a pure set comparison on immutable data: cheap, deterministic, and easy to show
  in a demo query. It also works on a historical window if the archive lags (OQ-1
  freshness criterion).
- Bronze retention becomes a correctness property, not just a storage choice. Any future
  bronze lifecycle rule must exempt reconciled windows.
- `first_seen_source` requires the merge to use an insert-only column, which dbt-athena
  supports via `merge_update_columns`; this must be set explicitly or the merge will
  overwrite it.
- If the OQ-1 spike finds a fill-level archive, the bronze grain collapse rule must run
  in the backfill reader so that `recon_trades` compares like with like.
