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

from solvent.engine import StateStore, compute_equity, run_cycle
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


class _AlreadyConfirmedExecutor:
    """Executor reporting an old confirmed intent without a new fill."""

    def execute(self, intent, cycle_id):
        return ExecutionResult(
            intent_key(intent, cycle_id),
            True,
            "0x" + "ab" * 32,
            "already confirmed; skipped",
            "already_confirmed",
        )


def _run(tmp_path, signals, holdings, store, now, executor=None, cfg=None):
    journal = Journal(tmp_path / "journal.jsonl")
    summary = run_cycle(
        source=_Source(signals),
        executor=executor or PaperExecutor(journal),
        journal=journal,
        receipts=ReceiptChain(tmp_path / "receipts.jsonl"),
        store=store,
        holdings=holdings,
        cfg=cfg or RiskConfig(),
        advisor=None,
        now=now,
    )
    receipt = json.loads((tmp_path / "receipts.jsonl").read_text().splitlines()[-1])[
        "receipt"
    ]
    return summary, receipt


def _run_with_publisher(tmp_path, signals, holdings, store, now, publisher):
    journal = Journal(tmp_path / "journal.jsonl")
    return run_cycle(
        source=_Source(signals),
        executor=PaperExecutor(journal),
        journal=journal,
        receipts=ReceiptChain(tmp_path / "receipts.jsonl"),
        store=store,
        holdings=holdings,
        cfg=RiskConfig(),
        advisor=None,
        pretrade_publisher=publisher,
        now=now,
    )


def _position(symbol="CAKE", entry=2.5, notional=60.0, **over):
    position = {
        "symbol": symbol,
        "entry_price_usd": entry,
        "entry_momo_score": 2.0,
        "notional_usd": notional,
        "opened_at": "2026-06-24T10:00:00+00:00",
    }
    position.update(over)
    return position


def test_compute_equity_marks_stables_conservatively():
    signals = MarketSignals(
        fear_greed=60,
        btc_funding_rate=0.0001,
        prices={"USDT": 0.98, "USDC": 1.02, "CAKE": 2.5},
    )

    equity, floor = compute_equity(
        {"USDT": 10.0, "USDC": 10.0, "CAKE": 2.0},
        signals,
        ("USDT", "USDC"),
        stable_haircut_pct=0.01,
    )

    assert floor == pytest.approx(10.0 * 0.98 * 0.99 + 10.0 * 1.0 * 0.99)
    assert equity == pytest.approx(floor + 5.0)


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


def test_degraded_data_unwinds_open_position(tmp_path):
    signals = MarketSignals(
        fear_greed=None,
        btc_funding_rate=None,
        momentum={},
        prices={},
        degraded=True,
    )
    store = StateStore(path=tmp_path / "state.json", position=_position())
    summary, receipt = _run(
        tmp_path, signals, {"USDT": 240.0, "CAKE": 24.0}, store, NOON
    )
    assert summary["intents"] == 1
    assert receipt["intents"][0]["kind"] == "exit"
    assert "DEGRADED DATA UNWIND" in receipt["thesis"]
    assert store.position is None


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
    assert "persistent halt" in receipt["thesis"]
    assert store.position is None  # liquidated + persisted
    assert store.runtime_status == "HALTED"
    assert store.halt_reason and "trailing drawdown" in store.halt_reason


def test_persistent_halt_blocks_later_entries(tmp_path):
    signals = MarketSignals(
        fear_greed=80,
        btc_funding_rate=0.0001,
        momentum={"CAKE": 5.0},
        prices={"CAKE": 2.5},
        degraded=False,
    )
    store = StateStore(
        path=tmp_path / "state.json",
        start_equity_usd=300.0,
        peak_equity_usd=400.0,
        runtime_status="HALTED",
        halt_reason="operator review required",
    )

    summary, receipt = _run(tmp_path, signals, {"USDT": 300.0}, store, NOON)

    assert summary["intents"] == 0
    assert receipt["signals"]["runtime_status"] == "HALTED"
    assert "operator review required" in receipt["thesis"]


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


def test_exit_uses_current_position_value_not_entry_notional(tmp_path):
    signals = MarketSignals(
        fear_greed=60,
        btc_funding_rate=0.0001,
        momentum={"CAKE": 0.8},
        prices={"CAKE": 5.0},
        degraded=False,
    )
    store = StateStore(
        path=tmp_path / "state.json",
        position=_position(notional=60.0, profit_taken=True),
    )
    summary, receipt = _run(
        tmp_path, signals, {"USDT": 240.0, "CAKE": 24.0}, store, NOON
    )
    assert summary["intents"] == 1
    assert receipt["intents"][0]["kind"] == "exit"
    assert receipt["intents"][0]["notional_usd"] == pytest.approx(120.0)
    assert store.position is None


def test_take_profit_reduces_position_and_marks_it(tmp_path):
    signals = MarketSignals(
        fear_greed=60,
        btc_funding_rate=0.0001,
        momentum={"CAKE": 2.0},
        prices={"CAKE": 2.8},
        degraded=False,
    )
    store = StateStore(path=tmp_path / "state.json", position=_position(notional=60.0))
    summary, receipt = _run(
        tmp_path,
        signals,
        {"USDT": 240.0, "CAKE": 24.0},
        store,
        NOON,
        cfg=RiskConfig(take_profit_pct=0.10),
    )
    assert summary["intents"] == 1
    assert receipt["intents"][0]["kind"] == "take_profit"
    assert store.position is not None
    assert store.position["profit_taken"] is True
    assert store.position["notional_usd"] < 24.0 * 2.8


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


def test_already_confirmed_execution_does_not_mutate_position_state(tmp_path):
    signals = MarketSignals(
        fear_greed=60,
        btc_funding_rate=0.0001,
        momentum={"CAKE": 2.0},
        prices={"CAKE": 2.5},
        degraded=False,
    )
    store = StateStore(path=tmp_path / "state.json")

    summary, receipt = _run(
        tmp_path,
        signals,
        {"USDT": 300.0},
        store,
        NOON,
        executor=_AlreadyConfirmedExecutor(),
    )

    assert summary["intents"] == 1
    assert summary["executed_ok"] == 0
    assert receipt["executions"][0]["ok"] is True
    assert receipt["executions"][0]["outcome"] == "already_confirmed"
    assert store.position is None


def test_pre_trade_commit_and_execution_seal_wrap_intent(tmp_path):
    signals = MarketSignals(
        fear_greed=60,
        btc_funding_rate=0.0001,
        momentum={"CAKE": 2.0},
        prices={"CAKE": 2.5},
        degraded=False,
    )
    store = StateStore(path=tmp_path / "state.json")
    _run(tmp_path, signals, {"USDT": 300.0}, store, NOON)

    entries = [
        json.loads(line)["receipt"]
        for line in (tmp_path / "receipts.jsonl").read_text().splitlines()
    ]

    assert [entry["phase"] for entry in entries] == [
        "pre_trade_commit",
        "execution_seal",
        "cycle_summary",
    ]
    assert entries[0]["intent_key"] == entries[1]["intent_key"]
    assert entries[1]["pre_trade_hash"]
    assert entries[1]["execution_seal"]["ok"] is True


def test_failed_execution_still_gets_pre_trade_seal(tmp_path):
    signals = MarketSignals(
        fear_greed=18, btc_funding_rate=None, momentum={}, prices={}, degraded=False
    )
    store = StateStore(path=tmp_path / "state.json")
    _run(
        tmp_path,
        signals,
        {"USDT": 300.0},
        store,
        EVENING,
        executor=_FailingExecutor(),
    )

    entries = [
        json.loads(line)["receipt"]
        for line in (tmp_path / "receipts.jsonl").read_text().splitlines()
    ]

    assert entries[0]["phase"] == "pre_trade_commit"
    assert entries[1]["phase"] == "execution_seal"
    assert entries[1]["execution_seal"]["ok"] is False


def test_pretrade_publisher_runs_before_execution(tmp_path):
    class Publisher:
        def __init__(self):
            self.calls = []

        def publish(self, *, cycle_id, intent_key, commit_hash):
            self.calls.append((cycle_id, intent_key, commit_hash))
            return "0x" + "44" * 32

    signals = MarketSignals(
        fear_greed=60,
        btc_funding_rate=0.0001,
        momentum={"CAKE": 2.0},
        prices={"CAKE": 2.5},
        degraded=False,
    )
    publisher = Publisher()
    _run_with_publisher(
        tmp_path,
        signals,
        {"USDT": 300.0},
        StateStore(tmp_path / "state.json"),
        NOON,
        publisher,
    )
    entries = [
        json.loads(line)["receipt"]
        for line in (tmp_path / "receipts.jsonl").read_text().splitlines()
    ]

    assert len(publisher.calls) == 1
    assert (
        publisher.calls[0][2]
        == json.loads((tmp_path / "receipts.jsonl").read_text().splitlines()[0])["hash"]
    )
    assert entries[1]["execution_seal"]["pre_trade_anchor_tx_hash"] == "0x" + "44" * 32
