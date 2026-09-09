#!/usr/bin/env python3
"""dbt-run.yml pre-step (QNT-460, ADR-003): regenerate dbt/seeds/session_gaps.csv from
the real session manifest named by the dispatch's `recon_session_id` var, so the
remote dbt build sees that session's actual gap intervals rather than whatever seed
content happened to be committed.

This runs on the GH Actions runner's own checkout, not the machine that dispatched the
workflow -- a local `dbt/seeds/session_gaps.csv` write on the dispatching machine would
never reach this runner. `sessions/<id>.json` is already committed and pushed by the
time `make recon` can run at all (its manifest must have an `end`, which session-down
sets and commits), so reading it here needs no extra sync step.

A no-op (leaves the committed seed content as-is) when `DBT_VARS` carries no
`recon_session_id` or that session has no manifest -- covers every non-recon
dbt-run.yml dispatch and the AC1 fixture scenarios (`recon-fixture-*`, which have no
`sessions/<id>.json` and rely on the committed fixture rows instead).
"""

import json
import os
from pathlib import Path

from hyperlake.recon import write_session_gaps_seed
from hyperlake.session import load_manifest

SEED_PATH = Path("dbt/seeds/session_gaps.csv")
SESSIONS_DIR = Path("sessions")


def regenerate_seed(dbt_vars_raw: str, sessions_dir: Path, seed_path: Path) -> str | None:
    """Regenerates `seed_path` from `sessions_dir/<recon_session_id>.json`'s gaps, named
    by the `recon_session_id` key in `dbt_vars_raw` (a JSON object string, as dispatched
    via dbt-run.yml's `vars` input). Returns the session_id written, or None on a no-op
    (no vars, no recon_session_id, or no matching manifest -- leaves `seed_path` as-is)."""
    if not dbt_vars_raw:
        return None
    session_id = json.loads(dbt_vars_raw).get("recon_session_id")
    if not session_id:
        return None
    manifest_path = sessions_dir / f"{session_id}.json"
    if not manifest_path.exists():
        return None
    manifest = load_manifest(manifest_path)
    write_session_gaps_seed(manifest.get("gaps", []), session_id, seed_path)
    return session_id


def main() -> None:
    session_id = regenerate_seed(os.environ.get("DBT_VARS", ""), SESSIONS_DIR, SEED_PATH)
    if session_id:
        print(f"regen_recon_seed: wrote {SEED_PATH} for {session_id}")


if __name__ == "__main__":
    main()
