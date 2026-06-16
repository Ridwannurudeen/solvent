"""Chaos / fail-frozen behavior, exercised through the full decision cycle.

The kernel's pure rules are unit-tested in test_allocator.py; here we verify
they survive the round trip through run_cycle -> executor -> receipts -> state:
degraded data freezes trading, the kill switch and stop actually liquidate and
clear the position, the deadman still qualifies the day, and a failed execution
is recorded without corrupting position state.
"""

import json
from datetime import datetime, timezone

import pytest

from solvent.engine import StateStore, run_cycle
from solvent.exec.executor import ExecutionResult, Journal, PaperExecutor, intent_key
from solvent.kernel import allowlist
from solvent.kernel.rules import RiskConfig
from solvent.kernel.state import MarketSignals
from solvent.receipts.chain import DataPurchase, ReceiptChain

NOON = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
EVENING = datetime(2026, 6, 24, 21, 0, tzinfo=timezone.utc)  # past the 20:00 deadline


@pytest.fixture(autouse=True)
def pin_addresses(monkeypatch):
    monkeypatch.setattr(
        allowlist, "ADDRESSES", {s: "0x" + "1" * 40 for s in allowlist.SLEEVE_SYMBOLS}
    )


class _Source:
    def __init__(self, signals):
        self._signals = signals

    def fetch(self):
        return self._signals, [DataPurchase(tool="test", cost_usdc=0.0, ok=True)]


class _FailingExecutor:
    """Executor whose every send fails (e.g. RPC/TWAK error)."""

    def execute(self, intent, cycle_id):
        return ExecutionResult(intent_key(intent, cycle_id), False, None, "boom")


def _run(tmp_path, signals, holdings, store, now, executor=None):
    journal = Journal(tmp_path / "journal.jsonl")
    summary = run_cycle(
        source=_Source(signals),
        executor=executor or PaperExecutor(journal),
        journal=journal,
        receipts=ReceiptChain(tmp_path / "receipts.jsonl"),
        store=store,
        holdings=holdings,
        cfg=RiskConfig(),
        advisor=None,
        now=now,
    )
    receipt = json.loads((tmp_path / "receipts.jsonl").read_text().splitlines()[-1])[
        "receipt"
    ]
    return summary, receipt


def _position(symbol="CAKE", entry=2.5, notional=60.0):
    return {
        "symbol": symbol,
        "entry_price_usd": entry,
        "entry_momo_score": 2.0,
        "notional_usd": notional,
        "opened_at": "2026-06-24T10:00:00+00:00",
    }


def test_degraded_data_freezes_trading(tmp_path):
    signals = MarketSignals(
        fear_greed=None, btc_funding_rate=None, momentum={}, prices={}, degraded=True
    )
    store = StateStore(path=tmp_path / "state.json")
    summary, receipt = _run(tmp_path, signals, {"USDT": 300.0}, store, NOON)
    assert summary["intents"] == 0
    assert summary["degraded"] is True
    assert receipt["regime"] == "risk-off"
    assert receipt["signals"]["degraded"] is True


def test_kill_switch_liquidates_and_clears_position(tmp_path):
    # equity 300 vs peak 400 -> 25% trailing drawdown >= 22% kill line.
    signals = MarketSignals(
        fear_greed=60,
        btc_funding_rate=0.0001,
        momentum={},
        prices={"CAKE": 2.5},
        degraded=False,
    )
    store = StateStore(
        path=tmp_path / "state.json",
        start_equity_usd=300.0,
        peak_equity_usd=400.0,
        position=_position(),
    )
    summary, receipt = _run(
        tmp_path, signals, {"USDT": 240.0, "CAKE": 24.0}, store, NOON
    )
    assert summary["intents"] == 1
    assert summary["executed_ok"] == 1
    assert receipt["intents"][0]["kind"] == "exit"
    assert "KILL SWITCH" in receipt["thesis"]
    assert store.position is None  # liquidated + persisted


def test_stop_loss_exits_losing_position(tmp_path):
    # No drawdown vs peak (fresh), but price 2.1 < entry 2.5 = -16% <= -12% stop.
    signals = MarketSignals(
        fear_greed=60,
        btc_funding_rate=0.0001,
        momentum={"CAKE": 2.0},
        prices={"CAKE": 2.1},
        degraded=False,
    )
    store = StateStore(path=tmp_path / "state.json", position=_position())
    summary, receipt = _run(
        tmp_path, signals, {"USDT": 240.0, "CAKE": 24.0}, store, NOON
    )
    assert summary["intents"] == 1
    assert receipt["intents"][0]["kind"] == "exit"
    assert "STOP" in receipt["thesis"]
    assert store.position is None


def test_deadman_qualifies_after_deadline(tmp_path):
    # Risk-off (no signal trade) + past 20:00 + unqualified -> forced micro-rotation.
    signals = MarketSignals(
        fear_greed=18, btc_funding_rate=None, momentum={}, prices={}, degraded=False
    )
    store = StateStore(path=tmp_path / "state.json")
    summary, receipt = _run(tmp_path, signals, {"USDT": 300.0}, store, EVENING)
    assert summary["intents"] == 1
    intent = receipt["intents"][0]
    assert intent["kind"] == "qualify"
    assert intent["from"] == "USDT" and intent["to"] == "USDC"


def test_failed_execution_recorded_without_corrupting_position(tmp_path):
    signals = MarketSignals(
        fear_greed=18, btc_funding_rate=None, momentum={}, prices={}, degraded=False
    )
    store = StateStore(path=tmp_path / "state.json")
    summary, receipt = _run(
        tmp_path, signals, {"USDT": 300.0}, store, EVENING, executor=_FailingExecutor()
    )
    assert summary["intents"] == 1  # the qualify intent was produced
    assert summary["executed_ok"] == 0  # but it failed
    assert receipt["executions"][0]["ok"] is False
    assert store.position is None  # a failed qualify never opened a position
