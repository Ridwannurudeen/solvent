"""Review-only universe scanner for eligible, not-yet-pinned tokens."""

from dataclasses import dataclass, field

from ..kernel.allowlist import (
    ADDRESSES,
    ALLOWED_SYMBOLS,
    DISCOVERY_SYMBOLS,
)
from ..receipts.chain import DataPurchase
from .sources import (
    DISCOVERY_CMC_IDS,
    _extract_quote_metrics,
    _iter_quotes,
    momentum_score,
    quote_ids_for,
)


@dataclass(frozen=True)
class UniverseCandidate:
    symbol: str
    allowed: bool
    currently_executable: bool
    cmc_id: int | None
    has_binance_pair: bool
    price_usd: float | None = None
    volume_24h_usd: float | None = None
    market_cap_usd: float | None = None
    momentum_score: float = 0.0
    manual_address_pin_required: bool = False
    market_gate_passed: bool = False
    rejection_reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CandidateScanResult:
    candidates: list[UniverseCandidate]
    purchases: list[DataPurchase] = field(default_factory=list)


class CandidateScanner:
    """Ranks review candidates without changing the executable universe."""

    def __init__(
        self,
        *,
        cmc_ids: dict[str, int] | None = None,
        addresses: dict[str, str] | None = None,
        binance_pairs: set[str] | None = None,
    ) -> None:
        self.cmc_ids = cmc_ids or DISCOVERY_CMC_IDS
        self.addresses = addresses or ADDRESSES
        self.binance_pairs = binance_pairs or set()

    def scan_quotes(
        self,
        quotes,
        *,
        symbols: tuple[str, ...] = DISCOVERY_SYMBOLS,
        min_volume_usd: float = 5_000_000.0,
        min_market_cap_usd: float = 25_000_000.0,
        min_momentum: float = 1.0,
    ) -> CandidateScanResult:
        by_symbol = {
            entry.get("symbol"): entry
            for entry in _iter_quotes(quotes)
            if isinstance(entry.get("symbol"), str)
        }
        candidates = [
            self._candidate(
                symbol,
                by_symbol.get(symbol),
                min_volume_usd=min_volume_usd,
                min_market_cap_usd=min_market_cap_usd,
                min_momentum=min_momentum,
            )
            for symbol in symbols
        ]
        candidates.sort(
            key=lambda c: (
                c.market_gate_passed,
                c.momentum_score,
                c.volume_24h_usd or 0.0,
            ),
            reverse=True,
        )
        return CandidateScanResult(candidates=candidates)

    def fetch_cmc_candidates(
        self,
        client,
        *,
        symbols: tuple[str, ...] = DISCOVERY_SYMBOLS,
        min_volume_usd: float = 5_000_000.0,
        min_market_cap_usd: float = 25_000_000.0,
        min_momentum: float = 1.0,
    ) -> CandidateScanResult:
        ids = quote_ids_for(symbols, self.cmc_ids)
        if not ids:
            return self.scan_quotes(
                {},
                symbols=symbols,
                min_volume_usd=min_volume_usd,
                min_market_cap_usd=min_market_cap_usd,
                min_momentum=min_momentum,
            )
        raw, purchase = client.call_tool("get_crypto_quotes_latest", {"id": ids})
        payload = raw.get("structuredContent") if raw and not raw.get("isError") else {}
        result = self.scan_quotes(
            payload,
            symbols=symbols,
            min_volume_usd=min_volume_usd,
            min_market_cap_usd=min_market_cap_usd,
            min_momentum=min_momentum,
        )
        return CandidateScanResult(candidates=result.candidates, purchases=[purchase])

    def _candidate(
        self,
        symbol: str,
        quote: dict | None,
        *,
        min_volume_usd: float,
        min_market_cap_usd: float,
        min_momentum: float,
    ) -> UniverseCandidate:
        allowed = symbol in ALLOWED_SYMBOLS
        executable = allowed and bool(self.addresses.get(symbol))
        cmc_id = self.cmc_ids.get(symbol)
        has_binance_pair = f"{symbol}USDT" in self.binance_pairs
        manual_pin = allowed and not executable
        reasons: list[str] = []
        if not allowed:
            reasons.append("not_in_competition_allowlist")
        if cmc_id is None:
            reasons.append("missing_cmc_id")
        if not has_binance_pair:
            reasons.append("missing_binance_pair")
        metrics = _extract_quote_metrics(quote or {})
        score = 0.0
        if quote is None:
            reasons.append("missing_cmc_quote")
        elif (
            metrics.percent_change_24h is not None
            and metrics.percent_change_7d is not None
        ):
            score = momentum_score(
                metrics.percent_change_24h, metrics.percent_change_7d
            )
        if metrics.volume_24h is None or metrics.volume_24h < min_volume_usd:
            reasons.append("low_volume")
        if metrics.market_cap is None or metrics.market_cap < min_market_cap_usd:
            reasons.append("low_market_cap")
        if score < min_momentum:
            reasons.append("low_momentum")
        market_blockers = {
            "not_in_competition_allowlist",
            "missing_cmc_id",
            "missing_binance_pair",
            "missing_cmc_quote",
            "low_volume",
            "low_market_cap",
            "low_momentum",
        }
        market_gate_passed = not any(reason in market_blockers for reason in reasons)
        if manual_pin:
            reasons.append("manual_address_pin_required")
        return UniverseCandidate(
            symbol=symbol,
            allowed=allowed,
            currently_executable=executable,
            cmc_id=cmc_id,
            has_binance_pair=has_binance_pair,
            price_usd=metrics.price,
            volume_24h_usd=metrics.volume_24h,
            market_cap_usd=metrics.market_cap,
            momentum_score=score,
            manual_address_pin_required=manual_pin,
            market_gate_passed=market_gate_passed,
            rejection_reasons=tuple(reasons),
        )
