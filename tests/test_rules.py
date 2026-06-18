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


def test_unknown_profile_fails_closed():
    with pytest.raises(ValueError, match="unknown risk profile"):
        risk_config_for_profile("moon")
