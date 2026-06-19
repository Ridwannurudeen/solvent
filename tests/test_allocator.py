from datetime import datetime, timezone

import pytest

from solvent.kernel import allowlist
from solvent.kernel.allocator import (
    MIN_ENTRY_MOMO,
    IntentKind,
    Regime,
    classify_regime,
    decide,
    qualification_intent,
)
from solvent.kernel.rules import RiskConfig
from solvent.kernel.state import MarketSignals, PortfolioState, SleevePosition

CFG = RiskConfig()
PROFIT_CFG = RiskConfig(
    breakeven_activation_pct=0.04,
    trailing_activation_pct=0.06,
    take_profit_pct=0.10,
    take_profit_fraction=0.35,
)
NOON = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
EVENING = datetime(2026, 6, 24, 21, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def pin_addresses(monkeypatch):
    """Make sleeve symbols executable for tests."""
    monkeypatch.setattr(
        allowlist, "ADDRESSES", {s: "0x" + "1" * 40 for s in allowlist.SLEEVE_SYMBOLS}
    )


def flat_state(**over) -> PortfolioState:
    defaults = dict(
        equity_usd=300.0,
        start_equity_usd=300.0,
        peak_equity_usd=300.0,
        floor_usd=300.0,
        position=None,
        trades_today=0,
        qualified_today=False,
        now=NOON,
    )
    defaults.update(over)
    return PortfolioState(**defaults)


def risk_on_signals(**over) -> MarketSignals:
    defaults = dict(
        fear_greed=60,
        btc_funding_rate=0.0001,
        momentum={"CAKE": 2.0, "ETH": 1.5},
        prices={"CAKE": 2.5, "ETH": 2500.0},
        degraded=False,
    )
    defaults.update(over)
    return MarketSignals(**defaults)


# ── Regime ────────────────────────────────────────────────────────────


def test_regime_risk_on():
    assert classify_regime(risk_on_signals()) is Regime.RISK_ON


def test_regime_fear_is_risk_off():
    assert classify_regime(risk_on_signals(fear_greed=20)) is Regime.RISK_OFF


def test_regime_degraded_is_risk_off():
    assert classify_regime(risk_on_signals(degraded=True)) is Regime.RISK_OFF


def test_regime_missing_fng_is_risk_off():
    assert classify_regime(risk_on_signals(fear_greed=None)) is Regime.RISK_OFF


def test_regime_euphoric_funding_is_neutral():
    assert classify_regime(risk_on_signals(btc_funding_rate=0.005)) is Regime.NEUTRAL


# ── Entries ───────────────────────────────────────────────────────────


def test_enters_top_momentum_in_risk_on():
    intents = decide(flat_state(), risk_on_signals(), CFG)
    assert len(intents) == 1
    it = intents[0]
    assert it.kind is IntentKind.ENTER
    assert it.to_symbol == "CAKE"
    # Sleeve target 22%, capped by floor: 300 - 0.75*300 = 75 max
    assert it.notional_usd == pytest.approx(0.22 * 300.0)


def test_no_entry_when_risk_off():
    assert decide(flat_state(), risk_on_signals(fear_greed=10), CFG) == []


def test_no_entry_when_degraded():
    assert decide(flat_state(), risk_on_signals(degraded=True), CFG) == []


def test_no_entry_below_momentum_bar():
    sig = risk_on_signals(momentum={"CAKE": MIN_ENTRY_MOMO - 0.01})
    assert decide(flat_state(), sig, CFG) == []


def test_configurable_entry_bar():
    cfg = RiskConfig(min_entry_momo=3.0)
    sig = risk_on_signals(momentum={"CAKE": 2.9})
    assert decide(flat_state(), sig, cfg) == []


def test_no_entry_for_unpinned_address(monkeypatch):
    monkeypatch.setattr(allowlist, "ADDRESSES", {})  # nothing executable
    assert decide(flat_state(), risk_on_signals(), CFG) == []


def test_no_entry_for_non_sleeve_symbol():
    sig = risk_on_signals(momentum={"SMILEK": 9.9})  # allowed but not sleeve
    assert decide(flat_state(), sig, CFG) == []


def test_no_entry_when_daily_trade_cap_hit():
    st = flat_state(trades_today=CFG.max_trades_per_day)
    assert decide(st, risk_on_signals(), CFG) == []


def test_no_entry_when_portfolio_dust():
    st = flat_state(equity_usd=10.0, floor_usd=10.0)
    assert decide(st, risk_on_signals(), CFG) == []


def test_entry_never_breaches_floor_minimum():
    # Floor already partially deployed elsewhere: only 5 USD above minimum.
    st = flat_state(equity_usd=300.0, floor_usd=0.75 * 300.0 + 5.0)
    intents = decide(st, risk_on_signals(), CFG)
    assert len(intents) == 1
    assert intents[0].notional_usd <= 5.0 + 1e-9


def test_ratchet_caps_entry_size_after_gains():
    st = flat_state(
        equity_usd=390.0, peak_equity_usd=390.0, floor_usd=390.0
    )  # +30% banked
    intents = decide(st, risk_on_signals(), CFG)
    assert len(intents) == 1
    # +30% gain → tier 0.20 applies → cap 10%
    assert intents[0].notional_usd == pytest.approx(0.10 * 390.0)


# ── Position management ───────────────────────────────────────────────


def pos_state(**over) -> PortfolioState:
    pos = SleevePosition(
        symbol="CAKE",
        entry_price_usd=2.5,
        entry_momo_score=2.0,
        notional_usd=66.0,
        opened_at=NOON,
        high_price_usd=2.5,
    )
    return flat_state(position=pos, floor_usd=234.0, **over)


def test_stop_loss_exits():
    sig = risk_on_signals(prices={"CAKE": 2.5 * (1 - CFG.stop_pct - 0.01)})
    intents = decide(pos_state(), sig, CFG)
    assert [i.kind for i in intents] == [IntentKind.EXIT]
    assert "PROTECTIVE STOP" in intents[0].reason


def test_trailing_stop_exits_after_profit():
    pos = SleevePosition(
        symbol="CAKE",
        entry_price_usd=2.5,
        entry_momo_score=2.0,
        notional_usd=66.0,
        opened_at=NOON,
        high_price_usd=3.0,
    )
    st = flat_state(position=pos, floor_usd=234.0)
    sig = risk_on_signals(prices={"CAKE": 2.84})
    intents = decide(st, sig, PROFIT_CFG)
    assert [i.kind for i in intents] == [IntentKind.EXIT]
    assert "PROTECTIVE STOP" in intents[0].reason


def test_take_profit_deleverages_once():
    sig = risk_on_signals(
        prices={"CAKE": 2.5 * (1 + PROFIT_CFG.take_profit_pct + 0.01)}
    )
    intents = decide(pos_state(), sig, PROFIT_CFG)
    assert [i.kind for i in intents] == [IntentKind.TAKE_PROFIT]
    assert intents[0].notional_usd == pytest.approx(
        66.0 * PROFIT_CFG.take_profit_fraction
    )


def test_take_profit_does_not_repeat():
    pos = SleevePosition(
        symbol="CAKE",
        entry_price_usd=2.5,
        entry_momo_score=2.0,
        notional_usd=66.0,
        opened_at=NOON,
        high_price_usd=2.9,
        profit_taken=True,
    )
    st = flat_state(position=pos, floor_usd=234.0)
    sig = risk_on_signals(
        prices={"CAKE": 2.5 * (1 + PROFIT_CFG.take_profit_pct + 0.01)}
    )
    assert decide(st, sig, PROFIT_CFG) == []


def test_momentum_decay_exits():
    sig = risk_on_signals(momentum={"CAKE": 0.9})  # < 50% of entry 2.0
    intents = decide(pos_state(), sig, CFG)
    assert [i.kind for i in intents] == [IntentKind.EXIT]
    assert "DECAY" in intents[0].reason


def test_min_hold_hours_suppresses_momentum_decay():
    cfg = RiskConfig(min_hold_hours=4.0)
    sig = risk_on_signals(momentum={"CAKE": 0.9})
    assert decide(pos_state(), sig, cfg) == []


def test_degraded_data_unwinds_open_position():
    sig = risk_on_signals(momentum={"CAKE": 0.0}, degraded=True, prices={})
    intents = decide(pos_state(), sig, CFG)
    assert [i.kind for i in intents] == [IntentKind.EXIT]
    assert "DEGRADED DATA UNWIND" in intents[0].reason


def test_holding_blocks_new_entries():
    sig = risk_on_signals(momentum={"ETH": 5.0, "CAKE": 2.0})
    intents = decide(pos_state(), sig, CFG)
    assert all(i.kind is not IntentKind.ENTER for i in intents)


def test_ratchet_deleverages_oversized_position():
    # +35% banked gain → cap 5%; position 66 USD on 405 equity ≈ 16%.
    st = pos_state(equity_usd=405.0, peak_equity_usd=405.0)
    intents = decide(st, risk_on_signals(), CFG)
    assert [i.kind for i in intents] == [IntentKind.DELEVERAGE]
    assert intents[0].notional_usd == pytest.approx(66.0 - 0.05 * 405.0)


# ── Kill switch ───────────────────────────────────────────────────────


def test_kill_switch_liquidates_position():
    st = pos_state(equity_usd=300.0, peak_equity_usd=400.0)  # 25% trailing DD
    intents = decide(st, risk_on_signals(), CFG)
    assert [i.kind for i in intents] == [IntentKind.EXIT]
    assert "KILL SWITCH" in intents[0].reason


def test_kill_switch_blocks_entries_when_flat():
    st = flat_state(equity_usd=300.0, peak_equity_usd=400.0)
    assert decide(st, risk_on_signals(), CFG) == []


# ── Qualification deadman ─────────────────────────────────────────────


def test_qualifies_after_deadline_when_unqualified():
    it = qualification_intent(flat_state(now=EVENING), CFG)
    assert it is not None and it.kind is IntentKind.QUALIFY
    assert it.from_symbol != it.to_symbol
    assert it.notional_usd == CFG.qual_trade_usd


def test_no_qualify_before_deadline():
    assert qualification_intent(flat_state(now=NOON), CFG) is None


def test_no_qualify_when_already_qualified():
    st = flat_state(now=EVENING, qualified_today=True)
    assert qualification_intent(st, CFG) is None


# ── Config invariants ─────────────────────────────────────────────────


def test_floor_symbols_are_allowlisted():
    for s in CFG.floor_symbols:
        assert allowlist.is_allowed(s)


def test_sleeve_symbols_are_allowlisted():
    for s in allowlist.SLEEVE_SYMBOLS:
        assert allowlist.is_allowed(s)


def test_ratchet_monotonic():
    tiers = sorted(CFG.ratchet, key=lambda t: t.gain_pct)
    caps = [t.sleeve_cap for t in tiers]
    assert caps == sorted(caps, reverse=True)


def test_kill_switch_well_inside_dq():
    assert CFG.kill_switch_drawdown_pct <= CFG.dq_drawdown_pct - 0.05
