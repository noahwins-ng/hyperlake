"""AC6 (dev execution, core logic unit-tested here): session-up must refuse to start --
before any terraform apply -- over an unfinished prior session or an ephemeral Kinesis
stream still present in Terraform state, naming the blocker in each case.
"""

from unittest.mock import MagicMock

from scripts.session_up import latest_image_tag, preflight_blocker, stream_exists_in_state


def test_no_prior_session_and_clean_state_does_not_block():
    assert preflight_blocker(None, "") is None


def test_unfinished_prior_session_blocks_and_names_it():
    manifest = {"session_id": "dev-20260908000000", "end": None, "reaped": False}
    blocker = preflight_blocker(manifest, "")
    assert blocker is not None
    assert "dev-20260908000000" in blocker


def test_finished_prior_session_does_not_block():
    manifest = {"session_id": "dev-20260908000000", "end": "2026-09-08T04:00:00Z", "reaped": False}
    assert preflight_blocker(manifest, "") is None


def test_existing_kinesis_stream_in_state_blocks():
    state_list = "aws_ecs_cluster.hyperlake\naws_kinesis_stream.trades\n"
    blocker = preflight_blocker(None, state_list)
    assert blocker is not None
    assert "Kinesis" in blocker


def test_stream_exists_in_state_matches_exact_resource_address():
    assert stream_exists_in_state("aws_kinesis_stream.trades") is True
    assert stream_exists_in_state("aws_kinesis_firehose_delivery_stream.trades") is False
    assert stream_exists_in_state("") is False


def test_latest_image_tag_picks_most_recently_pushed():
    ecr = MagicMock()
    ecr.describe_images.return_value = {
        "imageDetails": [
            {"imagePushedAt": "2026-09-07T00:00:00Z", "imageTags": ["older-sha"]},
            {"imagePushedAt": "2026-09-08T00:00:00Z"},  # untagged manifest layer, skipped
            {"imagePushedAt": "2026-09-08T12:00:00Z", "imageTags": ["newest-sha"]},
        ]
    }

    assert latest_image_tag(ecr) == "newest-sha"
