"""Paper fills charge fee + slippage + gas, so they don't overstate live (#4)."""

import pytest

from solvent.kernel.allocator import IntentKind, TradeIntent
from solvent.kernel.state import MarketSignals
from solvent.run import PAPER_FEE, PAPER_GAS_USD, PAPER_SLIPPAGE, PaperBook


def test_paper_fill_charges_fee_slippage_and_gas(tmp_path):
    book = PaperBook(tmp_path / "paper.json")  # seeds USDT 300
    signals = MarketSignals(
        fear_greed=50, btc_funding_rate=0.0, momentum={}, prices={"CAKE": 2.5}
    )
    intent = TradeIntent(IntentKind.ENTER, "USDT", "CAKE", 60.0, "enter")

    book.apply(intent, signals)

    expected_units = (60.0 * (1 - PAPER_FEE - PAPER_SLIPPAGE) - PAPER_GAS_USD) / 2.5
    assert book.holdings["USDT"] == pytest.approx(240.0)
    assert book.holdings["CAKE"] == pytest.approx(expected_units)
    # Strictly worse than the old fee-only fill: gas + slippage are real drag.
    assert book.holdings["CAKE"] < (60.0 * (1 - PAPER_FEE)) / 2.5
