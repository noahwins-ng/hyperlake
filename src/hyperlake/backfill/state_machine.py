"""Renders the backfill Step Functions state machine's ASL definition.

The definition lives in `infra/main/ephemeral/state_machine/backfill.asl.json` as the
single source of truth, templated with a plain `${lambda_arn}` placeholder -- read
identically by Terraform's `templatefile()` (backfill_state_machine.tf) and by this
module (for pytest, AC3). A plain string replace, not `string.Template`: ASL's own
`$.foo` / `$$.foo` JSONPath syntax collides with `string.Template`'s `$` sigil.
"""

import json
from pathlib import Path

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[3] / "infra/main/ephemeral/state_machine/backfill.asl.json"
)


def render_definition(lambda_arn: str) -> dict:
    text = TEMPLATE_PATH.read_text()
    return json.loads(text.replace("${lambda_arn}", lambda_arn))
