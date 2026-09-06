from hyperlake.watchlist import load_watchlist


def test_load_watchlist_returns_expected_markets():
    markets = load_watchlist()
    assert markets == ["BTC", "ETH", "HYPE", "xyz:SP500", "xyz:XYZ100"]


def test_load_watchlist_from_explicit_path(tmp_path):
    custom = tmp_path / "watchlist.yaml"
    custom.write_text("markets:\n  - FOO\n  - BAR\n")
    assert load_watchlist(custom) == ["FOO", "BAR"]
