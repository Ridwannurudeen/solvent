import pytest

from solvent.kernel.rules import RiskConfig, risk_config_for_profile


def test_safety_profile_is_default_config():
    assert risk_config_for_profile("safety") == RiskConfig()


def test_conviction_profile_re_risks_but_raises_entry_bar():
    cfg = risk_config_for_profile("conviction_50")

    assert cfg.floor_frac_min == pytest.approx(0.50)
    assert cfg.sleeve_frac_target == pytest.approx(0.48)
    assert cfg.max_trade_frac == pytest.approx(0.50)
    assert cfg.stop_pct == pytest.approx(0.08)
    assert cfg.min_entry_momo == pytest.approx(10.0)
    assert cfg.min_hold_hours == pytest.approx(48.0)
    assert cfg.kill_switch_drawdown_pct <= cfg.dq_drawdown_pct - 0.05
    # Requires >=3 trades/day, with the deadline pulled earlier so three
    # one-per-cycle qualifiers fit before the UTC day rolls over.
    assert cfg.min_trades_per_day == 3
    assert cfg.qual_deadline_hour_utc == 18
    assert 24 - cfg.qual_deadline_hour_utc >= cfg.min_trades_per_day


def test_base_config_requires_one_trade_per_day():
    assert RiskConfig().min_trades_per_day == 1
    assert RiskConfig().forced_scalp is False


def test_scalp_profile_is_tight_drawdown_first():
    cfg = risk_config_for_profile("scalp_eth")
    assert cfg.forced_scalp is True
    assert cfg.stop_pct == pytest.approx(0.03)
    assert cfg.take_profit_pct == pytest.approx(0.03)
    assert cfg.take_profit_fraction == pytest.approx(1.0)
    assert cfg.sleeve_frac_target == pytest.approx(0.25)
    assert cfg.max_trade_frac == pytest.approx(0.25)
    assert cfg.floor_frac_min == pytest.approx(0.75)
    assert cfg.min_hold_hours == pytest.approx(0.0)
    assert cfg.min_trades_per_day == 3
    # Drawdown-first: the kill switch stays well below the 30% DQ line.
    assert cfg.kill_switch_drawdown_pct <= cfg.dq_drawdown_pct - 0.05


def test_unknown_profile_fails_closed():
    with pytest.raises(ValueError, match="unknown risk profile"):
        risk_config_for_profile("moon")
