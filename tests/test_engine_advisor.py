from datetime import datetime, timezone

import pytest

from solvent.brain.advisor import RegimeAdvice
from solvent.engine import StateStore, run_cycle
from solvent.exec.executor import Journal, PaperExecutor
from solvent.kernel import allowlist
from solvent.kernel.allocator import Regime
from solvent.kernel.rules import RiskConfig
from solvent.kernel.state import MarketSignals
from solvent.receipts.chain import DataPurchase, ReceiptChain

NOON = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def pin_addresses(monkeypatch):
    monkeypatch.setattr(
        allowlist, "ADDRESSES", {s: "0x" + "1" * 40 for s in allowlist.SLEEVE_SYMBOLS}
    )


class _Source:
    def fetch(self):
        signals = MarketSignals(
            fear_greed=60,
            btc_funding_rate=0.0001,
            momentum={"CAKE": 2.0},
            prices={"CAKE": 2.5},
            degraded=False,
        )
        return signals, [DataPurchase(tool="test", cost_usdc=0.0, ok=True)]


def _cycle(tmp_path, advisor):
    journal = Journal(tmp_path / "journal.jsonl")
    return run_cycle(
        source=_Source(),
        executor=PaperExecutor(journal),
        journal=journal,
        receipts=ReceiptChain(tmp_path / "receipts.jsonl"),
        store=StateStore(path=tmp_path / "state.json"),
        holdings={"USDT": 300.0},
        cfg=RiskConfig(),
        advisor=advisor,
        now=NOON,
    )


def test_entry_fires_without_advisor(tmp_path):
    summary = _cycle(tmp_path, advisor=None)
    assert summary["intents"] == 1
    assert summary["regime"] == "risk-on"


def test_advisor_downgrade_suppresses_entry(tmp_path):
    def advisor(signals, regime, position):
        return RegimeAdvice(Regime.RISK_OFF, 0.9, "stand down", [])

    summary = _cycle(tmp_path, advisor=advisor)
    assert summary["intents"] == 0
    assert summary["regime"] == "risk-off"


def test_advisor_thesis_lands_in_receipt(tmp_path):
    def advisor(signals, regime, position):
        return RegimeAdvice(Regime.NEUTRAL, 0.5, "funding crowded", ["CAKE"])

    _cycle(tmp_path, advisor=advisor)
    import json

    rec = json.loads((tmp_path / "receipts.jsonl").read_text().splitlines()[-1])[
        "receipt"
    ]
    assert rec["thesis"] == "funding crowded"
    assert rec["signals"]["regime_deterministic"] == "risk-on"
    assert rec["signals"]["advisor"]["regime"] == "neutral"
    assert rec["signals"]["advisor"]["confidence"] == 0.5


def test_advisor_failure_falls_back_to_deterministic(tmp_path):
    summary = _cycle(tmp_path, advisor=lambda s, r, p: None)
    assert summary["intents"] == 1  # advisor returned None -> deterministic risk-on
    assert summary["regime"] == "risk-on"
