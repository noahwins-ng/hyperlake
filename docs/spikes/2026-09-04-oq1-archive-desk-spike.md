# Spike: OQ-1 archive sizing, grain, freshness, and `tid` parity (desk run)

- **Date:** 2026-09-04
- **Method:** read-only S3 listings on both archive buckets (requester-pays), one official
  hour file (`20260903/12.lz4`), two Reservoir liquidation files, column-pruned reads of
  both Reservoir 2026-09-03 `all` files (`coin, trade_id, side, address`), and a 30 s live
  WebSocket capture of the five watchlist markets. Total spend: cents.
- **Outcome:** region decided, HIP-3 coverage confirmed, grain confirmed fill-level on
  both archives, volume measured, decimal bounds confirmed. **One gate step remains:**
  match a live-captured `tid` against the official hour file that lands ~2 h later.

## Sources measured

| | Official `hl-mainnet-node-data` | Reservoir `hydromancer-reservoir` |
|---|---|---|
| Region | **ap-northeast-1** | **ap-northeast-1** |
| Access | requester-pays | requester-pays, "free forever" |
| Path | `node_fills_by_block/hourly/YYYYMMDD/H.lz4` | `by_dex/{hyperliquid,xyz,…}/fills/perp/{all,builder_fills,liquidations,twap_fills}/date=YYYY-MM-DD/fills.parquet` |
| Format | LZ4 frame → JSON lines, one line per block: `{local_time, block_time, block_number, events: [[user, fill], …]}` | Parquet, 28 columns, 4–8 row groups/day |
| Size / day | ~1.1 GB LZ4 (24 × ~46 MB); ~26 GB decompressed | hyperliquid `all` ~410–500 MB; xyz `all` ~240–275 MB |
| Scope | every market (439 seen), every user fill | per dex; hyperliquid 177 coins, xyz 103 coins |
| Freshness | hour `H` lands ~2 min after hour `H+1` closes → **~1 h lag** | daily file lands ~10:07 UTC next day → **10–34 h lag** |
| History | 2025-07-27 → today (`node_trades` prefix dead since 2025-06-21) | hyperliquid 2025-07-28 →; xyz 2025-10-13 → |
| Fill fields | `coin, px, sz, side, time, tid, hash` (WS-identical) + `oid, crossed, fee, feeToken, dir, closedPnl, startPosition, cloid, twapId` | renamed/typed: `trade_id UBIGINT, price/size DECIMAL(20,10), timestamp TIMESTAMPTZ, tx_hash, address, order_id, is_liquidation, builder, twap_id, dex, base_symbol, …` |
| Grain | fill-level: 2 rows per `tid` (270,270 of 275,930 tids in hour 12); **~2 % single-fill tids**, mostly `crossed=true` | fill-level: exactly 2 rows per `trade_id` in `all` (3,860,666 pairs, zero singles) |
| Drift signals | first-party | top-level `_pre_hip4_unification_backup/` prefix → layout was reorganised recently |

### Hour-file boundary (found while testing `scripts/spike/check_tid_parity.py`)

Official hour files are cut by **block `local_time` (arrival)**, not by trade `time`: the
first line of `20260903/12.lz4` has `local_time 12:00:00.05` but `block_time 11:59:59.86`,
and its fills carry `time = 1788436799860` (11:59:59.860 UTC). Any reader mapping trades
to hours must fetch hour `H` **and `H+1`**; the backfill's `dt` partition must come from
event `time`, not from the file name.

## Live feed (WS `trades` channel, 30 s capture)

- One message per trade: `{coin, side, px, sz, time, hash, tid, users: [buyer, seller]}`.
- **No liquidation / crossed / fee fields** on the feed, those exist only in the archives.
- HIP-3 markets subscribe and emit as `xyz:SP500`, `xyz:XYZ100`, identical to archive naming.
- `tid` is an integer of the same magnitude as the archive (`4.3e14`–`1.1e15`); `hash`
  present on both sides.
- Rate over 30 s: BTC 162, HYPE 143, XYZ100 63, ETH 57, SP500 55 → **~17 trades/s**.

## Watchlist volume (Reservoir `all`, full day 2026-09-03)

| Market | Trades/day | Fill rows/day |
|---|---|---|
| BTC | 504,527 | 1,009,054 |
| HYPE | 328,003 | 656,006 |
| ETH | 195,708 | 391,416 |
| xyz:SP500 | 47,657 | 95,314 |
| xyz:XYZ100 | 34,651 | 69,302 |
| **Watchlist** | **~1.11 M** | **~2.22 M** |
| Network-wide (official hour 12 × 24) | ~6.6 M | ~13 M |

Cross-check: official hour 12 UTC had BTC 25.6 k / HYPE 12.2 k / ETH 9.7 k / SP500 2.8 k /
XYZ100 1.8 k trades → ~52 k/h, consistent with the daily figure.

## Decimal bounds (max digits observed, official hour 12)

| | px int | px frac | sz int | sz frac |
|---|---|---|---|---|
| BTC | 5 | 1 | 2 | 5 |
| ETH | 4 | 1 | 3 | 4 |
| HYPE | 2 | 3 | 4 | 2 |
| xyz:SP500 | 4 | 1 | 2 | 3 |
| xyz:XYZ100 | 5 | 1 | 1 | 4 |
| widest across all xyz | 5 | 4 | 5 | 5 |

`decimal(18,8)` for `px` and `decimal(18,6)` for `sz` hold with margin.

## Backfill cost (30 days, deployed in ap-northeast-1 → S3→Lambda transfer is free)

| Source | Bytes read | Lambda estimate | Notes |
|---|---|---|---|
| Official | ~33 GB LZ4 (720 hour files) | 720 × ~40 s × 2 GB ≈ 58 k GB-s ≈ **$1** | must stream-decode LZ4; naive read needs 1–2 GB RAM |
| Reservoir | ~22 GB Parquet (60 daily files) | 60 × ~60 s × 2 GB ≈ 7 k GB-s ≈ **$0.15** | column-pruned reads still scan whole file (not coin-sorted) |

Requester-pays GET/LIST requests are negligible at these object counts.

## Consequences for the PRD

1. Region = ap-northeast-1 for either source.
2. Official bucket is the better **primary**: ~1 h lag lets the G3 replay run inside one
   demo session; fields are WS-identical so the envelope is pass-through. Reservoir is the
   **fallback** (Parquet, curated flags) behind a small column-mapping layer.
3. Backfill fan-out is **per hour file**, not per coin × day; filter the watchlist inside
   the Lambda and write per-coin partitions.
4. Bronze grain collapse must accept **1 or 2** fills per `tid`.
5. Liquidation marts are **backfill-only**: the feed carries no flag.
6. Watchlist estimate becomes ~1.1 M trades/day; idle storage estimate drops to < 5 GB.

## Gate result (2026-09-05): PASS

`scripts/spike/capture_ws_trades.py --seconds 90` at 15:05 UTC 2026-09-04 → 1,986 trades
(BTC 680, ETH 797, HYPE 323, xyz:SP500 114, xyz:XYZ100 72).
`scripts/spike/check_tid_parity.py` against `hourly/20260904/15.lz4` (55 MB):

| matched | mismatched | missing |
|---|---|---|
| **1,986** | 0 | 0 |

Two facts learned on the way:

- The first run reported 548 `side` mismatches, all because the indexer kept the
  **maker** fill. The pair shares `px, sz, time` but has opposite `side`; the WS feed
  reports the **taker's** side (`crossed = true`). Preferring the crossed fill gives a
  100 % match. This confirms the PRD's collapse rule exactly.
- The WS `trades` subscription replays ~6 s of recent trades on connect (first trade
  received predated the connect timestamp).

OQ-1 closed by [ADR-005](../decisions/ADR-005-backfill-source-and-trade-identity.md).
