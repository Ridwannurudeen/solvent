"""Signal sources — turn raw market data into kernel MarketSignals.

Two sources:

- CMCSource: production. Pays per call via the x402 MCP client. Buys
  tiered data: a cheap base set every cycle (quotes for the sleeve
  universe + global metrics), and an anomaly set (derivatives metrics)
  only when the base set warrants it — the information-budget behavior,
  metered by X402Signer.
- BinanceSource: free public REST. Paper-mode driver, and a live-mode
  price cross-check (a wildly diverging paid quote marks the snapshot
  degraded rather than trusted).

Momentum score (frozen at Phase 3): 0.6 * pct_change_24h + 0.4 * pct_change_7d,
in percent units, entries gated on score >= MIN_ENTRY_MOMO and both legs
positive ("momentum on confirmation").
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace

import httpx

from ..kernel.allowlist import SLEEVE_SYMBOLS
from ..kernel.state import MarketSignals
from ..receipts.chain import DataPurchase
from .x402pay import X402MCPClient

logger = logging.getLogger(__name__)


def momentum_score(pc_24h: float, pc_7d: float) -> float:
    if pc_24h <= 0 or pc_7d <= 0:
        return 0.0
    return 0.6 * pc_24h + 0.4 * pc_7d


CMC_IDS = {
    "ETH": 1027,
    "XRP": 52,
    "DOGE": 74,
    "ADA": 2010,
    "LINK": 1975,
    "LTC": 2,
    "AVAX": 5805,
    "DOT": 6636,
    "UNI": 7083,
    "BCH": 1831,
    "CAKE": 7186,
    "TWT": 5964,
    "FLOKI": 10804,
    "SHIB": 5994,
    "FET": 3773,
    "INJ": 7226,
    "PENDLE": 9481,
    "ASTER": 36341,
    "AAVE": 7278,
    "ETC": 1321,
    "FIL": 2280,
    "ATOM": 3794,
}
DISCOVERY_CMC_IDS = {**CMC_IDS}


def quote_ids_for(symbols: Iterable[str], cmc_ids: dict[str, int] | None = None) -> str:
    ids = cmc_ids or CMC_IDS
    return ",".join(str(ids[s]) for s in symbols if s in ids)


CMC_QUOTE_IDS = quote_ids_for(SLEEVE_SYMBOLS)


@dataclass(frozen=True)
class QuoteMetrics:
    price: float | None = None
    percent_change_1h: float | None = None
    percent_change_24h: float | None = None
    percent_change_7d: float | None = None
    volume_change_24h: float | None = None
    volume_24h: float | None = None
    market_cap: float | None = None


def _mcp_json(result: dict | None) -> dict | list | None:
    """Unwrap an MCP tools/call result into its JSON payload."""
    if not result:
        return None
    if result.get("isError"):
        return None
    if isinstance(result.get("structuredContent"), (dict, list)):
        return result["structuredContent"]
    for item in result.get("content", []):
        if item.get("type") == "text":
            import json

            try:
                return json.loads(item["text"])
            except (ValueError, KeyError):
                return None
    return None


def _to_int(value) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _extract_fear_greed(metrics: dict) -> int | None:
    fg = (
        metrics.get("fear_and_greed")
        or metrics.get("fearAndGreed")
        or metrics.get("fear_greed")
    )
    sentiment = metrics.get("sentiment")
    if fg is None and isinstance(sentiment, dict):
        fg = sentiment.get("fear_greed") or sentiment.get("fearAndGreed")
    if not isinstance(fg, dict):
        return _to_int(fg)

    candidates = []
    current = fg.get("current")
    if isinstance(current, dict):
        candidates.extend([current.get("index"), current.get("value")])
    candidates.extend([fg.get("index"), fg.get("value")])
    for candidate in candidates:
        value = _to_int(candidate)
        if value is not None:
            return value
    return None


class CMCSource:
    """Paid production source via the x402 MCP server."""

    def __init__(self, client: X402MCPClient) -> None:
        self.client = client

    def fetch(self) -> tuple[MarketSignals, list[DataPurchase]]:
        purchases: list[DataPurchase] = []
        degraded = False

        # ── Base tier: global metrics (Fear & Greed) ────────────────
        gm_raw, p = self.client.call_tool("get_global_metrics_latest", {})
        purchases.append(p)
        gm = _mcp_json(gm_raw)
        fear_greed = None
        if isinstance(gm, dict):
            fear_greed = _extract_fear_greed(gm)
        if fear_greed is None:
            degraded = True

        # ── Base tier: sleeve-universe quotes ───────────────────────
        q_raw, p = self.client.call_tool(
            "get_crypto_quotes_latest", {"id": CMC_QUOTE_IDS}
        )
        purchases.append(p)
        quotes = _mcp_json(q_raw)
        momentum: dict[str, float] = {}
        prices: dict[str, float] = {}
        percent_change_1h: dict[str, float] = {}
        volume_change_24h: dict[str, float] = {}
        volume_24h_usd: dict[str, float] = {}
        market_cap_usd: dict[str, float] = {}
        for entry in _iter_quotes(quotes):
            sym = entry.get("symbol")
            if sym not in SLEEVE_SYMBOLS:
                continue
            metrics = _extract_quote_metrics(entry)
            if metrics.price is not None:
                prices[sym] = metrics.price
            if metrics.percent_change_1h is not None:
                percent_change_1h[sym] = metrics.percent_change_1h
            if metrics.volume_change_24h is not None:
                volume_change_24h[sym] = metrics.volume_change_24h
            if metrics.volume_24h is not None:
                volume_24h_usd[sym] = metrics.volume_24h
            if metrics.market_cap is not None:
                market_cap_usd[sym] = metrics.market_cap
            if (
                metrics.percent_change_24h is not None
                and metrics.percent_change_7d is not None
            ):
                momentum[sym] = momentum_score(
                    metrics.percent_change_24h, metrics.percent_change_7d
                )
        if not prices:
            degraded = True

        # ── Anomaly tier: derivatives, bought only on signal ────────
        btc_funding = None
        if fear_greed is not None and (fear_greed >= 45 or fear_greed <= 25):
            d_raw, p = self.client.call_tool(
                "get_global_crypto_derivatives_metrics", {}
            )
            purchases.append(p)
            deriv = _mcp_json(d_raw)
            if isinstance(deriv, dict):
                fr = deriv.get("funding_rate") or deriv.get("avg_funding_rate")
                if isinstance(fr, (int, float)):
                    btc_funding = float(fr)

        return (
            MarketSignals(
                fear_greed=fear_greed,
                btc_funding_rate=btc_funding,
                momentum=momentum,
                prices=prices,
                percent_change_1h=percent_change_1h,
                volume_change_24h=volume_change_24h,
                volume_24h_usd=volume_24h_usd,
                market_cap_usd=market_cap_usd,
                degraded=degraded,
            ),
            purchases,
        )


def _iter_quotes(quotes) -> list[dict]:
    """CMC quote payloads come keyed by symbol/id or as a list; flatten."""
    if isinstance(quotes, list):
        return [q for q in quotes if isinstance(q, dict)]
    if isinstance(quotes, dict):
        data = quotes.get("data", quotes)
        if isinstance(data, dict):
            headers = data.get("headers")
            rows = data.get("rows")
            if isinstance(headers, list) and isinstance(rows, list):
                return [
                    {str(k): v for k, v in zip(headers, row)}
                    for row in rows
                    if isinstance(row, list)
                ]
            out = []
            for v in data.values():
                if isinstance(v, list):
                    out.extend(x for x in v if isinstance(x, dict))
                elif isinstance(v, dict):
                    out.append(v)
            return out
        if isinstance(data, list):
            return [q for q in data if isinstance(q, dict)]
    return []


def _extract_quote_fields(entry: dict):
    """price, pct_change_24h, pct_change_7d from a CMC quote entry."""
    metrics = _extract_quote_metrics(entry)
    return metrics.price, metrics.percent_change_24h, metrics.percent_change_7d


def _extract_quote_metrics(entry: dict) -> QuoteMetrics:
    """Quote metrics from a CMC quote entry."""
    quote = entry.get("quote", {})
    usd = quote.get("USD", quote) if isinstance(quote, dict) else {}
    if not isinstance(usd, dict):
        usd = {}

    def to_f(v):
        return float(v) if isinstance(v, (int, float)) else None

    return QuoteMetrics(
        price=to_f(usd.get("price", entry.get("price"))),
        percent_change_1h=to_f(
            usd.get("percent_change_1h", entry.get("percent_change_1h"))
        ),
        percent_change_24h=to_f(
            usd.get("percent_change_24h", entry.get("percent_change_24h"))
        ),
        percent_change_7d=to_f(
            usd.get("percent_change_7d", entry.get("percent_change_7d"))
        ),
        volume_change_24h=to_f(
            usd.get("volume_change_24h", entry.get("volume_change_24h"))
        ),
        volume_24h=to_f(usd.get("volume_24h", entry.get("volume_24h"))),
        market_cap=to_f(usd.get("market_cap", entry.get("market_cap"))),
    )


# ── Free source (paper mode / cross-check) ───────────────────────────

BINANCE_API = "https://api.binance.com/api/v3"

# Sleeve symbols quoted as Binance USDT pairs. Symbols without a USDT
# listing are simply absent in paper mode.
_BINANCE_PAIR = {s: f"{s}USDT" for s in SLEEVE_SYMBOLS}


class BinanceSource:
    """Free signal source from Binance public REST (no key)."""

    def __init__(self, timeout: float = 15.0) -> None:
        self._http = httpx.Client(timeout=timeout)

    def fetch(self) -> tuple[MarketSignals, list[DataPurchase]]:
        purchases = [DataPurchase(tool="binance:ticker24h+7d", cost_usdc=0.0, ok=True)]
        momentum: dict[str, float] = {}
        prices: dict[str, float] = {}
        volume_24h_usd: dict[str, float] = {}
        degraded = False
        try:
            resp = self._http.get(f"{BINANCE_API}/ticker/24hr")
            resp.raise_for_status()
            by_pair = {t["symbol"]: t for t in resp.json()}
            for sym, pair in _BINANCE_PAIR.items():
                t = by_pair.get(pair)
                if not t:
                    continue
                prices[sym] = float(t["lastPrice"])
                volume_24h_usd[sym] = float(t.get("quoteVolume", 0.0))
                pc24 = float(t["priceChangePercent"])
                pc7d = self._pct_change_7d(pair)
                if pc7d is not None:
                    momentum[sym] = momentum_score(pc24, pc7d)
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.warning("binance source failed: %s", e)
            degraded = True
            purchases = [
                DataPurchase(tool="binance:ticker24h+7d", cost_usdc=0.0, ok=False)
            ]

        fear_greed = None
        try:
            # alternative.me free Fear & Greed (paper mode stand-in for CMC's).
            r = self._http.get("https://api.alternative.me/fng/?limit=1")
            r.raise_for_status()
            fear_greed = int(r.json()["data"][0]["value"])
        except (httpx.HTTPError, KeyError, ValueError, IndexError):
            degraded = True

        return (
            MarketSignals(
                fear_greed=fear_greed,
                btc_funding_rate=None,
                momentum=momentum,
                prices=prices,
                volume_24h_usd=volume_24h_usd,
                degraded=degraded,
            ),
            purchases,
        )

    def _pct_change_7d(self, pair: str) -> float | None:
        try:
            r = self._http.get(
                f"{BINANCE_API}/klines",
                params={"symbol": pair, "interval": "1d", "limit": 8},
            )
            r.raise_for_status()
            kl = r.json()
            if len(kl) < 2:
                return None
            old_close = float(kl[0][4])
            last_close = float(kl[-1][4])
            if old_close <= 0:
                return None
            return (last_close / old_close - 1.0) * 100.0
        except (httpx.HTTPError, ValueError, IndexError):
            return None


class CrossCheckedSource:
    """Marks primary signals degraded when an independent price source disagrees."""

    def __init__(
        self,
        primary,
        secondary,
        *,
        max_deviation_pct: float = 5.0,
        require_secondary: bool = True,
        require_momentum_confirmation: bool = True,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.max_deviation_pct = max_deviation_pct
        self.require_secondary = require_secondary
        self.require_momentum_confirmation = require_momentum_confirmation

    def fetch(self) -> tuple[MarketSignals, list[DataPurchase]]:
        primary_signals, purchases = self.primary.fetch()
        secondary_signals, secondary_purchases = self.secondary.fetch()
        purchases.extend(secondary_purchases)

        degraded = primary_signals.degraded
        deviations: dict[str, float] = {}
        if secondary_signals.degraded and self.require_secondary:
            degraded = True
        for symbol, primary_price in primary_signals.prices.items():
            secondary_price = secondary_signals.prices.get(symbol)
            if (
                primary_price is None
                or secondary_price is None
                or primary_price <= 0
                or secondary_price <= 0
            ):
                continue
            deviation = abs(primary_price / secondary_price - 1.0) * 100.0
            deviations[symbol] = deviation
            if deviation > self.max_deviation_pct:
                degraded = True

        momentum = dict(primary_signals.momentum)
        if self.require_momentum_confirmation:
            for symbol, primary_score in primary_signals.momentum.items():
                if primary_score <= 0:
                    continue
                secondary_score = secondary_signals.momentum.get(symbol)
                if secondary_score is None or secondary_score <= 0:
                    momentum.pop(symbol, None)
                    if self.require_secondary:
                        degraded = True
                    continue
                momentum[symbol] = min(primary_score, secondary_score)

        return (
            replace(
                primary_signals,
                momentum=momentum,
                degraded=degraded,
                source_deviation_pct=deviations,
            ),
            purchases,
        )
