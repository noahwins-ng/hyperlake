from pathlib import Path

from scripts.recon_chart import OUTPUT, render_svg


def test_committed_svg_matches_script_output() -> None:
    assert OUTPUT.read_text() == render_svg(), "run `uv run python scripts/recon_chart.py`"


def test_svg_shows_every_bucket_and_the_gap() -> None:
    svg = render_svg()
    for label in ("ws_only", "both", "backfill_only", "132,823", "13,576", "recorded gap"):
        assert label in svg


def test_readme_embeds_the_chart() -> None:
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    assert "docs/img/recon-2026-09-11.svg" in readme
