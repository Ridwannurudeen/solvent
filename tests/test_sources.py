"""The live signal path — CMCSource and its parsing helpers.

CMCSource turns paid CMC payloads into kernel MarketSignals. Two things must
hold: (1) momentum/quotes/funding are extracted correctly across CMC's payload
shapes, and (2) any missing or failed data marks the snapshot `degraded` so the
kernel fails frozen rather than trading on bad data. A fake MCP client exercises
all of this offline, and also asserts per-call cost metering lands in the
receipt's data_purchases.
"""

from solvent.receipts.chain import DataPurchase
from solvent.signals.sources import (
    CMC_QUOTE_IDS,
    CMCSource,
    _extract_fear_greed,
    _extract_quote_fields,
    _iter_quotes,
    momentum_score,
)

# ── pure helpers ──────────────────────────────────────────────────────


def test_momentum_score_requires_both_legs_positive():
    assert momentum_score(3.0, 5.0) == 0.6 * 3.0 + 0.4 * 5.0  # 3.8
    assert momentum_score(3.0, -1.0) == 0.0  # 7d down -> no confirmation
    assert momentum_score(-1.0, 5.0) == 0.0  # 24h down -> no confirmation
    assert momentum_score(0.0, 5.0) == 0.0  # flat counts as not-up


def test_iter_quotes_handles_list_and_keyed_shapes():
    entry = {"symbol": "CAKE", "price": 2.5}
    assert _iter_quotes([entry]) == [entry]
    assert _iter_quotes({"data": [entry]}) == [entry]
    assert _iter_quotes({"data": {"CAKE": entry}}) == [entry]
    assert _iter_quotes({"data": {"CAKE": [entry]}}) == [entry]
    assert _iter_quotes(
        {"headers": ["symbol", "price"], "rows": [["ASTER", 0.66]]}
    ) == [{"symbol": "ASTER", "price": 0.66}]
    assert _iter_quotes(None) == []


def test_extract_fear_greed_handles_cmc_sentiment_shape():
    assert (
        _extract_fear_greed(
            {"sentiment": {"fear_greed": {"current": {"value": "Fear", "index": 25}}}}
        )
        == 25
    )
    assert _extract_fear_greed({"fear_and_greed": {"value": 40}}) == 40


def test_extract_quote_fields_nested_and_flat():
    nested = {
        "quote": {
            "USD": {"price": 2.5, "percent_change_24h": 3.0, "percent_change_7d": 5.0}
        }
    }
    assert _extract_quote_fields(nested) == (2.5, 3.0, 5.0)
    flat = {"price": 1.0, "percent_change_24h": 2.0, "percent_change_7d": 4.0}
    assert _extract_quote_fields(flat) == (1.0, 2.0, 4.0)
    assert _extract_quote_fields({}) == (None, None, None)


# ── CMCSource.fetch with a fake paying client ─────────────────────────


class FakeClient:
    """Stand-in for X402MCPClient: canned structuredContent per tool, with a
    set of tools forced to 'fail' (return None + ok=False purchase)."""

    def __init__(self, responses, fail=()):
        self.responses = responses
        self.fail = set(fail)
        self.calls = []

    def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments or {}))
        if name in self.fail:
            return None, DataPurchase(tool=name, cost_usdc=0.0, ok=False)
        return {"structuredContent": self.responses.get(name)}, DataPurchase(
            tool=name, cost_usdc=0.01, ok=True
        )


def _quote(symbol, p, pc24, pc7d):
    return {
        "symbol": symbol,
        "quote": {
            "USD": {"price": p, "percent_change_24h": pc24, "percent_change_7d": pc7d}
        },
    }


def _responses(fng=60, deriv=0.0002):
    return {
        "get_global_metrics_latest": {"fear_and_greed": {"value": fng}},
        "get_crypto_quotes_latest": {"data": {"CAKE": _quote("CAKE", 2.5, 3.0, 5.0)}},
        "get_global_crypto_derivatives_metrics": {"funding_rate": deriv},
    }


def test_fetch_happy_path_parses_signals_and_meters_cost():
    client = FakeClient(_responses(fng=60))
    signals, purchases = CMCSource(client).fetch()
    assert signals.degraded is False
    assert signals.fear_greed == 60
    assert signals.prices["CAKE"] == 2.5
    assert signals.momentum["CAKE"] == momentum_score(3.0, 5.0)
    # fear_greed 60 >= 45 -> anomaly tier (derivatives) fetched.
    assert ("get_global_crypto_derivatives_metrics", {}) in client.calls
    assert ("get_crypto_quotes_latest", {"id": CMC_QUOTE_IDS}) in client.calls
    assert signals.btc_funding_rate == 0.0002
    # every paid call is metered into the receipt.
    assert [p.cost_usdc for p in purchases] == [0.01, 0.01, 0.01]
    assert "36341" in CMC_QUOTE_IDS


def test_fetch_degrades_when_global_metrics_fail():
    client = FakeClient(_responses(), fail={"get_global_metrics_latest"})
    signals, _ = CMCSource(client).fetch()
    assert signals.degraded is True
    assert signals.fear_greed is None


def test_fetch_degrades_when_no_quotes():
    resp = _responses()
    resp["get_crypto_quotes_latest"] = {"data": {}}
    signals, _ = CMCSource(FakeClient(resp)).fetch()
    assert signals.degraded is True
    assert signals.prices == {}


def test_neutral_fear_greed_skips_anomaly_tier():
    # 40 is neither >=45 nor <=25 -> no derivatives call, funding stays None.
    client = FakeClient(_responses(fng=40))
    signals, purchases = CMCSource(client).fetch()
    assert ("get_global_crypto_derivatives_metrics", {}) not in client.calls
    assert signals.btc_funding_rate is None
    assert len(purchases) == 2  # base tier only
