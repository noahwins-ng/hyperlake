# ADR-004: Land live trades via Kinesis + Firehose, not a direct Fargate → S3 write

- **Status:** accepted
- **Date:** 2026-09-04 (raised in PRD v0.4 review; lands in Phase 2)
- **Ticket:** —

## Context

The Fargate ingester already holds the WebSocket and has the trade in memory. Writing
Parquet straight to S3 from that task would remove two managed services from a project
whose owner is AWS-beginner and whose PRD biases toward fewer moving parts. The question
was whether Kinesis + Firehose earn their place or exist for service-count breadth.

## Decision

Keep **Kinesis Data Streams (on-demand) → Firehose → S3**. The pair supplies, as managed
behaviour, exactly what the ingestion contract already requires:

- **Kinesis** decouples the socket-holding task from the S3 writer. A crash mid-batch
  does not lose in-flight trades; they stay in the stream for replay. This is the
  at-least-once half of the contract.
- **Firehose** owns crash-safe buffering, file rotation, native Parquet conversion against
  a Glue schema, and event-time dynamic partitioning — all of which the ingester would
  otherwise hand-roll and unit-test.

Firehose is configured at **60 s / 64 MB** (the 64 MB floor is imposed by Parquet
conversion), which sets the G2 bronze-visibility bound at ~1–2 minutes.

Revisit only if the Phase 2 spike shows the Fargate → Kinesis → Firehose → Glue-schema
wiring consuming disproportionate time against the weekend-pace budget.

## Alternatives considered

- **Fargate writes Parquet to S3 directly** — fewest services, but the ingester then owns
  buffering, rotation, partial-file recovery on crash, and Parquet writing. Every one of
  those is a place to lose trades silently, and there is no replay buffer.
- **Fargate → Firehose directly (no Kinesis)** — removes the replay buffer; a Firehose
  `PutRecordBatch` failure during a task crash is lost data. Saves ~$0.04/hr.
- **MSK Serverless** — Kafka is the stronger resume signal, but the idle floor is
  ~$0.75/hr, which alone violates G4.

## Consequences

- Cost difference against direct write is pennies per session; the real trade is
  learning-curve and debugging surface, which the Phase 2 spike is budgeted to absorb.
- G2 cannot promise sub-minute bronze visibility. The goal is measured in two parts
  (to Kinesis, to bronze) to keep the claim honest.
- Kinesis on-demand at ~$29/month if left running is the single largest cost risk; the
  scripted `session-down` and post-destroy checklist exist chiefly for this.
- The Glue schema for Firehose conversion is owned by the ingester envelope (OQ-7), so
  upstream drift never fails conversion.
