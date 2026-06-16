from datetime import datetime, timezone

import pytest

from solvent.kernel import allowlist
from solvent.kernel.allocator import (
    IntentKind,
    Regime,
    decide,
    more_conservative,
)
from solvent.kernel.rules import RiskConfig
from solvent.kernel.state import MarketSignals, PortfolioState

CFG = RiskConfig()
NOON = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def pin_addresses(monkeypatch):
    monkeypatch.setattr(
        allowlist, "ADDRESSES", {s: "0x" + "1" * 40 for s in allowlist.SLEEVE_SYMBOLS}
    )


def _flat_state() -> PortfolioState:
    return PortfolioState(
        equity_usd=300.0,
        start_equity_usd=300.0,
        peak_equity_usd=300.0,
        floor_usd=300.0,
        position=None,
        trades_today=0,
        qualified_today=False,
        now=NOON,
    )


def _risk_on_signals() -> MarketSignals:
    return MarketSignals(
        fear_greed=60,
        btc_funding_rate=0.0001,
        momentum={"CAKE": 2.0, "ETH": 1.5},
        prices={"CAKE": 2.5, "ETH": 2500.0},
        degraded=False,
    )


def test_more_conservative_ordering():
    assert more_conservative(Regime.RISK_ON, Regime.RISK_OFF) is Regime.RISK_OFF
    assert more_conservative(Regime.RISK_ON, Regime.NEUTRAL) is Regime.NEUTRAL
    assert more_conservative(Regime.NEUTRAL, Regime.RISK_OFF) is Regime.RISK_OFF
    assert more_conservative(Regime.RISK_ON, Regime.RISK_ON) is Regime.RISK_ON


def test_override_suppresses_entry():
    # Signals are risk-on (an entry would fire), but the override de-risks.
    intents = decide(
        _flat_state(), _risk_on_signals(), CFG, regime_override=Regime.RISK_OFF
    )
    assert intents == []


def test_no_override_still_enters():
    intents = decide(_flat_state(), _risk_on_signals(), CFG)
    assert any(i.kind is IntentKind.ENTER for i in intents)


def test_override_risk_on_does_not_force_entry_when_signals_off():
    # An aggressive override cannot create an entry the signals don't support.
    off = MarketSignals(
        fear_greed=10,  # extreme fear
        btc_funding_rate=None,
        momentum={},  # no candidate above the bar
        prices={},
        degraded=False,
    )
    intents = decide(_flat_state(), off, CFG, regime_override=Regime.RISK_ON)
    assert all(i.kind is not IntentKind.ENTER for i in intents)
