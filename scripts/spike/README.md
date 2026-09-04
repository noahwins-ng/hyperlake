# OQ-1 `tid`-parity gate

The one open question the PRD cannot settle by design work: does the official archive
carry the same `tid` the WebSocket feed emits for the same trade? Everything else in
OQ-1 was measured on 2026-09-04 — see `docs/spikes/2026-09-04-oq1-archive-desk-spike.md`.

## Run it (one evening)

```bash
python -m venv .venv && .venv/bin/pip install -r scripts/spike/requirements.txt

# 1. Capture ~90 s of live trades for the watchlist (any hour H).
.venv/bin/python scripts/spike/capture_ws_trades.py --seconds 90 --out capture.jsonl

# 2. Wait until ~H+2:05 UTC (hour file lands ~1 h after the hour closes), then:
.venv/bin/python scripts/spike/check_tid_parity.py --capture capture.jsonl --cache .spike-cache
```

Needs AWS credentials with `s3:GetObject` on `hl-mainnet-node-data` (requester-pays;
one ~46 MB file per captured hour, cents).

## Outcome → PRD

- **PASS** → OQ-1 closes with an ADR (official bucket primary, region ap-northeast-1,
  dedup key `tid`); the data model freezes.
- **FAIL** → try plan B from PRD §11 (`hash` + `coin, side, px, sz`) by editing
  `COMPARE_KEYS`/the index key in `check_tid_parity.py`; if that also fails, G3 is
  re-scoped before the bronze schema freezes.

`capture.jsonl` and `.spike-cache/` are throwaway; do not commit them.
