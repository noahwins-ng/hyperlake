from hyperlake.session import estimate_cost_usd


def test_four_hour_session_lands_in_prd_headline_band() -> None:
    # PRD S8's own "Session total ~= $0.50-1" headline for a ~4h demo (docs/prd.md).
    assert 0.50 <= estimate_cost_usd(4.0) <= 1.00


def test_cost_scales_with_duration() -> None:
    assert estimate_cost_usd(1.0) < estimate_cost_usd(4.0) < estimate_cost_usd(6.0)


def test_six_hour_session_stays_under_budget_cap() -> None:
    # PRD G4: < $2 per demo session, even at the reaper's 6h bound.
    assert estimate_cost_usd(6.0) < 2.00
