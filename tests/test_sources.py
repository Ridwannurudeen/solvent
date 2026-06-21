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
    CrossCheckedSource,
    _extract_fear_greed,
    _extract_quote_metrics,
    _extract_quote_fields,
    _iter_quotes,
    quote_ids_for,
    momentum_score,
)
from solvent.kernel.state import MarketSignals

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


def test_extract_quote_metrics_includes_liquidity_context():
    entry = {
        "symbol": "CAKE",
        "quote": {
            "USD": {
                "price": 2.5,
                "percent_change_1h": 1.0,
                "percent_change_24h": 3.0,
                "percent_change_7d": 5.0,
                "volume_change_24h": 12.0,
                "volume_24h": 10_000_000,
                "market_cap": 700_000_000,
            }
        },
    }
    metrics = _extract_quote_metrics(entry)
    assert metrics.percent_change_1h == 1.0
    assert metrics.volume_change_24h == 12.0
    assert metrics.volume_24h == 10_000_000
    assert metrics.market_cap == 700_000_000


def test_quote_ids_for_ignores_unknown_symbols():
    assert quote_ids_for(["CAKE", "UNKNOWN"], {"CAKE": 7186}) == "7186"


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
    assert signals.volume_24h_usd == {}
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


class StaticSource:
    def __init__(self, signals):
        self.signals = signals

    def fetch(self):
        return self.signals, [DataPurchase(tool="static", cost_usdc=0.0, ok=True)]


def test_cross_checked_source_keeps_clean_matching_prices():
    primary = StaticSource(
        MarketSignals(
            fear_greed=60,
            btc_funding_rate=0.0001,
            prices={"CAKE": 2.50},
            degraded=False,
        )
    )
    secondary = StaticSource(
        MarketSignals(
            fear_greed=None,
            btc_funding_rate=None,
            prices={"CAKE": 2.50},
            degraded=False,
        )
    )

    signals, purchases = CrossCheckedSource(primary, secondary).fetch()

    assert signals.degraded is False
    assert signals.source_deviation_pct["CAKE"] == 0.0
    assert len(purchases) == 2


def test_cross_checked_source_degrades_on_price_divergence():
    primary = StaticSource(
        MarketSignals(
            fear_greed=60,
            btc_funding_rate=0.0001,
            prices={"CAKE": 2.50},
            degraded=False,
        )
    )
    secondary = StaticSource(
        MarketSignals(
            fear_greed=None,
            btc_funding_rate=None,
            prices={"CAKE": 2.00},
            degraded=False,
        )
    )

    signals, _ = CrossCheckedSource(primary, secondary, max_deviation_pct=5.0).fetch()

    assert signals.degraded is True
    assert signals.source_deviation_pct["CAKE"] == 25.0


def test_cross_checked_source_clamps_momentum_to_confirmed_score():
    primary = StaticSource(
        MarketSignals(
            fear_greed=60,
            btc_funding_rate=0.0001,
            prices={"CAKE": 2.50},
            momentum={"CAKE": 8.0},
            degraded=False,
        )
    )
    secondary = StaticSource(
        MarketSignals(
            fear_greed=None,
            btc_funding_rate=None,
            prices={"CAKE": 2.50},
            momentum={"CAKE": 3.0},
            degraded=False,
        )
    )

    signals, _ = CrossCheckedSource(primary, secondary).fetch()

    assert signals.degraded is False
    assert signals.momentum["CAKE"] == 3.0


def test_cross_checked_source_degrades_on_unconfirmed_positive_momentum():
    primary = StaticSource(
        MarketSignals(
            fear_greed=60,
            btc_funding_rate=0.0001,
            prices={"CAKE": 2.50},
            momentum={"CAKE": 8.0},
            degraded=False,
        )
    )
    secondary = StaticSource(
        MarketSignals(
            fear_greed=None,
            btc_funding_rate=None,
            prices={"CAKE": 2.50},
            momentum={"CAKE": 0.0},
            degraded=False,
        )
    )

    signals, _ = CrossCheckedSource(primary, secondary).fetch()

    assert signals.degraded is True
    assert "CAKE" not in signals.momentum
