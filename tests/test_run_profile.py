from datetime import datetime, timezone

from solvent.kernel.state import MarketSignals, PortfolioState
from solvent.run import _adaptive_profile_selector


def _state(**overrides):
    return PortfolioState(
        equity_usd=300.0,
        start_equity_usd=300.0,
        peak_equity_usd=330.0,
        floor_usd=150.0,
        position=None,
        trades_today=0,
        qualified_today=True,
        now=datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc),
        **overrides,
    )


def _signals(fear, *, momentum=0.0, degraded=False, funding=0.0001):
    return MarketSignals(
        fear_greed=fear,
        btc_funding_rate=funding,
        momentum={"CAKE": momentum} if momentum else {},
        prices={"CAKE": 1.0},
        percent_change_1h={},
        volume_change_24h={},
        volume_24h_usd={},
        market_cap_usd={},
        degraded=degraded,
    )


def test_adaptive_profile_stays_safe_when_feed_degraded():
    selector = _adaptive_profile_selector("tournament_60")
    cfg = selector(None, _state(), _signals(80, degraded=True))
    assert cfg.floor_frac_min == 0.75


def test_adaptive_profile_clamps_to_base_profile():
    selector = _adaptive_profile_selector("conviction_50")
    cfg = selector(
        None,
        _state(),
        _signals(75, momentum=12.0, funding=0.0001),
    )
    assert cfg.floor_frac_min == 0.50
    assert cfg.sleeve_frac_target == 0.48


def test_adaptive_profile_runs_conviction_in_neutral_market():
    selector = _adaptive_profile_selector("tournament_60")
    cfg = selector(
        None,
        _state(),
        _signals(55, momentum=4.5, funding=0.0001),
    )
    assert cfg.floor_frac_min == 0.50
    assert cfg.sleeve_frac_target == 0.48


def test_adaptive_profile_runs_aggressive_when_strong_regime():
    selector = _adaptive_profile_selector("tournament_60")
    cfg = selector(
        None,
        _state(),
        _signals(70, momentum=9.5, funding=0.0),
    )
    assert cfg.floor_frac_min == 0.40
    assert cfg.sleeve_frac_target == 0.58
