# Hyperlake — Plan

The narrative tracker. One checkbox item per ticket, grouped by phase (= Linear milestone).
`ship` ticks an item when its ticket merges; `change-scope` adds/removes items when scope shifts;
`sync-plan` reconciles this against the tracker. Every shipped ticket MUST appear here — the plan
is what a reader who opens the repo cold uses to understand what was built and why.

> Item format: `- [ ] <ISSUE-ID>: <short title>` with optional sub-bullets for deliverables or a
> `**Triggered by:**` note explaining why the ticket exists.

## Phase 0 — <name>

- [ ] <ISSUE-ID>: <short title>
  - <deliverable or context sub-bullet>
- [ ] <ISSUE-ID>: <short title>

## Phase 1 — <name>

- [ ] <ISSUE-ID>: <short title>

## Ops & Reliability  <!-- perpetual milestone: hardening that cuts across phases -->

- [ ] <ISSUE-ID>: <short title>
