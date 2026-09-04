# ADR-005: Official node-data archive as backfill source; `tid` as trade identity

- **Status:** accepted
- **Date:** 2026-09-05 (closes PRD OQ-1)
- **Ticket:** —

## Context

The silver exactly-once contract and the G3 replay reconciliation both dedup on a trade
identity that must be shared by the live WebSocket feed and the historical archive. The
PRD held OQ-1 open on that single hard gate — plus source, region, backfill window,
HIP-3 coverage, and grain — until measured. The desk spike of 2026-09-04
(`docs/spikes/2026-09-04-oq1-archive-desk-spike.md`) settled everything except identity;
the live gate ran on 2026-09-05.

## Decision

- **Backfill source:** `s3://hl-mainnet-node-data/node_fills_by_block/hourly/YYYYMMDD/H.lz4`
  (official, requester-pays) is **primary**. `s3://hydromancer-reservoir` is the
  **fallback**, behind a column-mapping reader.
- **Region:** **ap-northeast-1** — both buckets live there; S3 → Lambda transfer is free.
- **Trade identity / dedup key:** **`tid`**. The gate passed: a 90 s live capture of the
  five watchlist markets (1,986 trades, 15:05 UTC 2026-09-04) was matched **1,986 / 1,986**
  in `20260904/15.lz4` on `tid, coin, px, sz, side, time`; 0 missing, 0 mismatched.
- **Grain rule (confirmed):** the archive holds a maker and a taker fill per `tid` with
  **opposite `side`**; the feed reports the **taker's** side. The backfill reader keeps
  the `crossed = true` fill as the canonical row, or the sole fill when only one exists.
- **Backfill window:** 30 days default (≈ $1 Lambda, ~33 GB read); full history from
  2025-07-27 is a config change at ≈ $1 per extra month.
- **Hour-file semantics:** files are cut by block arrival time, not trade `time`; readers
  fetch hour `H` and `H+1` for boundary trades, and `dt` is always derived from event
  `time`.

## Alternatives considered

- **Reservoir as primary** — cleaner format (typed Parquet, curated flags), but 10–34 h
  lag pushes the G3 replay demo to the next day, its schema is renamed (mapping layer
  on the primary path), and its bucket layout has already been reorganised once
  (`_pre_hip4_unification_backup/`).
- **`hash`-based identity (plan B)** — present on both sides and viable, but not needed;
  `tid` matched 100 %. Kept documented in the PRD as the fallback if `tid` semantics ever
  change upstream.
- **Composite key `coin,time,px,sz,side`** — collides on simultaneous identical trades;
  rejected in the PRD before the gate ran.

## Consequences

- The bronze envelope, silver merge, and `recon_trades` all freeze on `tid`. PRD v1.0.
- The backfill Lambda must stream-decode LZ4 JSON (~235 MB/hour decompressed) and apply
  the taker-fill collapse; both are isolated in one reader module.
- The Reservoir fallback reader is a Phase 1 stretch, not a blocker: it needs a schema
  assertion and path pin so drift fails loudly.
- If `hl-mainnet-node-data` ever changes format (it has once before: `node_trades` →
  `node_fills` → `node_fills_by_block`), the reader's schema assertion catches it and
  Reservoir carries the gap.
