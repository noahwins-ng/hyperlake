import json
from pathlib import Path

from hyperlake.session import latest_manifest, session_is_open


def _write(tmp_path: Path, name: str, start: str) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({"session_id": name, "start": start}))
    return p


def test_no_manifests_yields_none(tmp_path: Path) -> None:
    assert latest_manifest(tmp_path) is None


def test_latest_manifest_by_start_time_same_label(tmp_path: Path) -> None:
    _write(tmp_path, "dev-20260901000000.json", "2026-09-01T00:00:00Z")
    latest = _write(tmp_path, "dev-20260907000000.json", "2026-09-07T00:00:00Z")
    _write(tmp_path, "dev-20260903000000.json", "2026-09-03T00:00:00Z")

    assert latest_manifest(tmp_path) == latest


def test_latest_manifest_ignores_label_when_labels_differ(tmp_path: Path) -> None:
    # "aaa-..." sorts before "zzz-..." lexically even though it's chronologically later --
    # latest_manifest must go by the manifest's own `start` field, not filename order.
    _write(tmp_path, "zzz-20260101000000.json", "2026-01-01T00:00:00Z")
    latest = _write(tmp_path, "aaa-20260901000000.json", "2026-09-01T00:00:00Z")

    assert latest_manifest(tmp_path) == latest


def test_open_session_with_no_end_and_not_reaped_blocks() -> None:
    assert session_is_open({"session_id": "x", "end": None, "reaped": False}) is True


def test_finished_session_with_end_set_does_not_block() -> None:
    manifest = {"session_id": "x", "end": "2026-09-08T00:00:00Z", "reaped": False}
    assert session_is_open(manifest) is False


def test_reaped_session_with_no_end_does_not_block() -> None:
    assert session_is_open({"session_id": "x", "end": None, "reaped": True}) is False


def test_no_prior_manifest_does_not_block() -> None:
    assert session_is_open(None) is False
