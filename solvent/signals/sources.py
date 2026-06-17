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
    "AAVE": 7278,
    "ETC": 1321,
    "FIL": 2280,
    "ATOM": 3794,
}
CMC_QUOTE_IDS = ",".join(str(CMC_IDS[s]) for s in SLEEVE_SYMBOLS if s in CMC_IDS)


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
            fg = (
                gm.get("fear_and_greed")
                or gm.get("fearAndGreed")
                or gm.get("fear_greed")
            )
            if isinstance(fg, dict):
                fg = fg.get("value")
            if isinstance(fg, (int, float)):
                fear_greed = int(fg)
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
        for entry in _iter_quotes(quotes):
            sym = entry.get("symbol")
            if sym not in SLEEVE_SYMBOLS:
                continue
            price, pc24, pc7d = _extract_quote_fields(entry)
            if price is not None:
                prices[sym] = price
            if pc24 is not None and pc7d is not None:
                momentum[sym] = momentum_score(pc24, pc7d)
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
    quote = entry.get("quote", {})
    usd = quote.get("USD", quote) if isinstance(quote, dict) else {}
    if not isinstance(usd, dict):
        usd = {}
    price = usd.get("price", entry.get("price"))
    pc24 = usd.get("percent_change_24h", entry.get("percent_change_24h"))
    pc7d = usd.get("percent_change_7d", entry.get("percent_change_7d"))

    def to_f(v):
        return float(v) if isinstance(v, (int, float)) else None

    return to_f(price), to_f(pc24), to_f(pc7d)


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
