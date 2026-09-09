"""AC2 (code): the reconcilable window (ADR-003) excludes the session's leading AND
trailing partial hours and is further bounded by which archive hour files have landed
(~1h lag, PRD FR-8) -- proven directly against `reconcilable_window`, independent of
any live session or dbt run. The leading-hour case mirrors a real bug found running
this against real Athena data (2026-09-09, QNT-460 AC3 verification): a session that
starts mid-hour has no WS coverage for that hour's leading sliver, so archive trades
in it are wrongly flagged `backfill_only` outside any recorded gap unless excluded.
"""

from datetime import UTC, datetime

from hyperlake.recon import reconcilable_window


def test_excludes_trailing_partial_hour_when_archive_is_caught_up():
    # Session ran 09:00-11:47; the trailing partial hour (11:00-11:47) is excluded
    # even though its archive file would already have landed by `now`.
    start = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)
    end = datetime(2026, 9, 8, 11, 47, tzinfo=UTC)
    now = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)  # well past archive lag

    window = reconcilable_window(start, end, now)

    assert window == (
        datetime(2026, 9, 8, 9, 0, tzinfo=UTC),
        datetime(2026, 9, 8, 11, 0, tzinfo=UTC),
    )


def test_bounded_by_landed_archive_hours_even_within_a_completed_session():
    # Session ended hours ago (12:00), but `now` is only 30 min past the end of hour
    # 10 -- hour 10's archive file (lands at 11:00 + 1h lag = 12:00) has not landed
    # yet, so the window must stop at hour 10, not the session's own end.
    start = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)
    end = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    now = datetime(2026, 9, 8, 11, 30, tzinfo=UTC)

    window = reconcilable_window(start, end, now)

    assert window == (
        datetime(2026, 9, 8, 9, 0, tzinfo=UTC),
        datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
    )


def test_none_when_no_hour_is_both_complete_and_landed():
    # A short session with the archive still lagging -- no hour qualifies yet.
    start = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)
    end = datetime(2026, 9, 8, 9, 40, tzinfo=UTC)
    now = datetime(2026, 9, 8, 9, 45, tzinfo=UTC)

    assert reconcilable_window(start, end, now) is None


def test_excludes_leading_partial_hour_when_session_starts_mid_hour():
    # Session started 13:52 (mid-hour) and ran to 14:19; the 13:00-13:52 sliver had no
    # WS coverage at all, and the 14:00-14:19 sliver is the trailing partial hour --
    # neither is a fully-covered hour, so there is no reconcilable hour yet.
    start = datetime(2026, 9, 8, 13, 52, 29, tzinfo=UTC)
    end = datetime(2026, 9, 8, 14, 19, 37, tzinfo=UTC)
    now = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)

    assert reconcilable_window(start, end, now) is None


def test_leading_partial_hour_excluded_when_a_later_full_hour_exists():
    start = datetime(2026, 9, 8, 13, 52, 29, tzinfo=UTC)
    end = datetime(2026, 9, 8, 16, 5, 0, tzinfo=UTC)
    now = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)

    window = reconcilable_window(start, end, now)

    # 13:00-14:00 (leading, partial) and 16:00-16:05 (trailing, partial) both excluded;
    # only 14:00-16:00 is fully covered.
    assert window == (
        datetime(2026, 9, 8, 14, 0, tzinfo=UTC),
        datetime(2026, 9, 8, 16, 0, tzinfo=UTC),
    )


def test_custom_archive_lag_hours():
    start = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)
    end = datetime(2026, 9, 8, 13, 0, tzinfo=UTC)
    now = datetime(2026, 9, 8, 13, 30, tzinfo=UTC)

    window = reconcilable_window(start, end, now, archive_lag_hours=2)

    # landed_end = floor(13:30 - 2h) = 11:00; trailing-hour-excluded end = floor(13:00) = 13:00
    assert window == (
        datetime(2026, 9, 8, 9, 0, tzinfo=UTC),
        datetime(2026, 9, 8, 11, 0, tzinfo=UTC),
    )
