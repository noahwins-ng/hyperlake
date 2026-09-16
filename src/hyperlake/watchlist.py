"""Config-driven watchlist loader, the only place a market list may live (CLAUDE.md)."""

from pathlib import Path

import yaml

# Resolved against the repo's src-layout checkout, valid for local dev, CI, and the dbt
# GitHub Actions run. A packaged deployment (e.g. the backfill Lambda) must pass `path=`
# explicitly rather than rely on this default.
DEFAULT_WATCHLIST_PATH = Path(__file__).resolve().parents[2] / "config" / "watchlist.yaml"


def load_watchlist(path: Path | str | None = None) -> list[str]:
    """Return the configured market symbols, in file order."""
    config_path = Path(path) if path is not None else DEFAULT_WATCHLIST_PATH
    with config_path.open() as f:
        config = yaml.safe_load(f)
    return list(config["markets"])
