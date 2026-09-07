# Spike: Kinesis + Firehose Parquet landing (QNT-455)

- **Date:** 2026-09-07
- **Method:** applied `infra/main/ephemeral`'s new Kinesis stream + Firehose delivery
  stream for real in ap-northeast-1, pushed 500 fixture envelope records
  (`scripts/spike/put_records.py`, 100 per watchlist market, one per coin backdated 2
  days) over `PutRecords`, verified landing in S3 + Athena, then destroyed the stack.
- **Outcome:** **PASS — keep Kinesis + Firehose (ADR-004 confirmed).** Wiring cost was
  one Terraform argument-name fix, not a redesign; the Parquet-conversion timestamp
  format was the only real gotcha.

## Wiring time

- Terraform (`kinesis_firehose.tf`: stream, delivery stream, IAM, log group, alarm) +
  `put_records.py`: **~35 min**, mostly reading the existing persistent-layer / envelope
  patterns to reuse (`data.terraform_remote_state.persistent`, `glue_columns()`).
  Excludes the review/plan-approval round trip, which is process time, not wiring time.
- One `terraform validate` failure: `aws_kinesis_firehose_delivery_stream`'s
  `hive_json_ser_de` block argument is `timestamp_formats`, not
  `timestamp_format_strings` (the AWS API's own parameter name, which the Terraform
  provider does *not* mirror) — a 2-minute fix once caught by validate, never reached AWS.
- `terraform apply`: 17 resources (includes QNT-451/452/454's backfill Lambda/state
  machine/schedule, unchanged, re-created because the ephemeral root is one state and had
  been destroyed since the last session), ~80 s wall clock.

## Gotchas

1. **Epoch-millisecond timestamps need an explicit format string.** Firehose's Parquet
   conversion (`data_format_conversion_configuration`) deserializes JSON via Hive's JSON
   SerDe, whose default timestamp parser expects `yyyy-MM-dd HH:mm:ss[.fffffffff]`, not a
   bare epoch. The envelope's `time`/`ingested_at` are epoch ms (matches the WS feed and
   the archive natively — PRD Data model), so
   `input_format_configuration.deserializer.hive_json_ser_de.timestamp_formats = ["millis"]`
   is required or Parquet conversion fails record-by-record into `errors/`.
2. **JQ dynamic partitioning runs on the raw JSON, before Parquet conversion drops the
   partition columns.** `coin`/`source` are declared as Glue *partition keys* (not table
   columns — `persistent/glue.tf`), so they never enter the Parquet body; the
   `MetadataExtraction` JQ query (`{coin: (.coin | gsub(":"; "_")), dt: (.time / 1000 |
   floor | gmtime | strftime("%Y-%m-%d"))}`) is the only place the colon normalisation and
   event-time-not-arrival-time derivation happen for the streaming path.
3. **Athena's `coin` column returns the normalised partition value, not the exact HIP-3
   name.** Because `coin` is partition-key-only, `SELECT coin FROM bronze.trades_raw`
   returns `xyz_SP500`, never `xyz:SP500` — the exact exchange name only survives in
   `raw_payload`. This is pre-existing behaviour from QNT-450 (Hive/Glue partition
   columns are always sourced from the object key), not something this ticket changed,
   and AC3 as written ("`xyz:SP500` records land under `coin=xyz_SP500`") is about the S3
   path, which holds. Flagging in case a downstream dbt model ever needs the untruncated
   name — it must read `raw_payload`, not the partition column.
4. **Measured emission → bronze latency: ~2m47s–3m00s** (Kinesis `PutRecords` at
   15:57:02 UTC; last S3 object `LastModified` 16:00:02 UTC; CloudWatch
   `DeliveryToS3.Success` confirms the same window). This is above ADR-004's "~60–120s"
   estimate and right at AC1's 3-minute bound — small-batch delivery (500 records, well
   under the 64 MB size trigger) waits out the full 60 s buffer interval *plus* JQ +
   Parquet conversion overhead, not just the buffer. G2's "emission → visible in bronze <
   3 min" goal has little margin at this payload size; worth re-measuring at watchlist-scale
   live volume (~17 trades/s per OQ-1) once the Fargate ingester exists (QNT-457), since a
   busier stream hits the size trigger sooner than the time trigger.

5. **Fixture `tid`s must sit clearly outside the real archive's range.** The first draft of
   `put_records.py` used a base (`9e14`) that actually falls *inside* OQ-1's observed
   real-tid magnitude (`4.3e14`-`1.1e15`) — a fixture re-run could collide with a real
   trade's `tid` in the silver merge. Caught in review, moved to `9e15` (an order of
   magnitude clear of the observed range).

## Verification (dev execution)

- **AC1 — landed:** 10 Parquet objects across all 5 markets × 2 `dt` partitions under
  `bronze/coin=<c>/dt=<d>/source=ws/`. `SELECT count(*) FROM bronze.trades_raw WHERE
  session_id = 'qnt455-spike'` → **500** (query `4024dcb8-2e98-46e1-bce9-c5bf7a746a37`,
  SUCCEEDED), matching the 500 records sent (0 failed on `PutRecords`).
- **AC2 — event-time partition:** the one backdated fixture per coin (`time` = put-time −
  2 days) landed under `dt=2026-09-05`; the other 99 under `dt=2026-09-07` (today).
  `GROUP BY coin, dt` breakdown: 1 + 99 per coin, all 5 markets.
- **AC3 — colon-normalised:** `xyz:SP500` and `xyz:XYZ100` fixture records landed under
  `coin=xyz_SP500/` and `coin=xyz_XYZ100/` respectively (object listing above).
- **AC4 — destroyed:** `terraform destroy` of the ephemeral stack completed (17 destroyed,
  0 errors); `aws kinesis list-streams` and `aws firehose list-delivery-streams` both
  returned empty afterward.

## Cleanup

The 10 fixture Parquet objects (`session_id = 'qnt455-spike'`) were deleted from the
persistent `bronze/` prefix after verification — bronze survives ephemeral `destroy`, and
fixture rows would otherwise sit alongside the real 27M-row backfill ahead of Phase 2's
real streaming data.

## Consequence for ADR-004

No revisit needed — the spike's one gotcha (timestamp format) was a config detail with a
documented fix, not a structural cost against the weekend-pace budget. Kinesis + Firehose
stand for the Phase 2 ingester (QNT-456/457).
