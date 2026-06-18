"""Close-to-close strategy simulator for comparing SOLVENT risk profiles."""

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import httpx

from ..kernel.allowlist import SLEEVE_SYMBOLS, is_executable
from ..kernel.allocator import IntentKind, decide, qualification_intent
from ..kernel.rules import RISK_PROFILE_NAMES, RiskConfig, risk_config_for_profile
from ..kernel.state import MarketSignals, PortfolioState, SleevePosition
from ..signals.sources import BINANCE_API, momentum_score

DEFAULT_START_USD = 300.0
DEFAULT_FEE_PCT = 0.0025
BINANCE_RESEARCH_APIS = (BINANCE_API, "https://data-api.binance.vision/api/v3")


@dataclass(frozen=True)
class Candle:
    ts: datetime
    symbol: str
    close: float


@dataclass(frozen=True)
class BacktestResult:
    profile: str
    start_equity_usd: float
    end_equity_usd: float
    total_return_pct: float
    max_drawdown_pct: float
    dq_breached: bool
    trades: int
    entries: int
    exits: int
    deleverages: int
    qualifications: int
    final_position: str | None


def profile_configs() -> dict[str, RiskConfig]:
    return {name: risk_config_for_profile(name) for name in RISK_PROFILE_NAMES}


def simulate(
    candles_by_symbol: dict[str, list[Candle]],
    *,
    cfg: RiskConfig,
    profile: str = "custom",
    start_usd: float = DEFAULT_START_USD,
    fee_pct: float = DEFAULT_FEE_PCT,
    per_trade_cost_usd: float = 0.0,
    per_cycle_cost_usd: float = 0.0,
    interval_hours: float = 1.0,
) -> BacktestResult:
    series = {
        sym: sorted(candles, key=lambda c: c.ts)
        for sym, candles in candles_by_symbol.items()
        if candles and is_executable(sym)
    }
    if not series:
        raise ValueError("no executable candle series")

    close_by_time = {
        sym: {c.ts: c.close for c in candles} for sym, candles in series.items()
    }
    index_by_time = {
        sym: {c.ts: i for i, c in enumerate(candles)} for sym, candles in series.items()
    }
    times = sorted({c.ts for candles in series.values() for c in candles})
    lookback_24 = max(1, round(24 / interval_hours))
    lookback_7d = max(1, round(168 / interval_hours))
    active_start = times[min(lookback_7d, len(times) - 1)]

    holdings: dict[str, float] = {cfg.floor_symbols[0]: start_usd}
    position: SleevePosition | None = None
    daily_trades: dict[str, int] = {}
    last_prices: dict[str, float] = {}

    start_equity = start_usd
    peak_equity = start_usd
    max_drawdown = 0.0
    trades = entries = exits = deleverages = qualifications = 0

    for now in times:
        for sym, lookup in close_by_time.items():
            close = lookup.get(now)
            if close is not None:
                last_prices[sym] = close
        if not last_prices:
            continue
        if now < active_start:
            continue

        if per_cycle_cost_usd > 0:
            _charge_floor_cost(holdings, cfg.floor_symbols, per_cycle_cost_usd)
        momentum = _momentum_at(
            now, series, index_by_time, lookback_24=lookback_24, lookback_7d=lookback_7d
        )
        signals = MarketSignals(
            fear_greed=60,
            btc_funding_rate=None,
            momentum=momentum,
            prices=dict(last_prices),
            degraded=False,
        )
        equity, floor = _equity(holdings, signals, cfg.floor_symbols)
        peak_equity = max(peak_equity, equity)
        position = _marked_position(position, holdings, signals)
        day = now.strftime("%Y-%m-%d")
        state = PortfolioState(
            equity_usd=equity,
            start_equity_usd=start_equity,
            peak_equity_usd=peak_equity,
            floor_usd=floor,
            position=position,
            trades_today=daily_trades.get(day, 0),
            qualified_today=daily_trades.get(day, 0) > 0,
            now=now,
        )

        intents = decide(state, signals, cfg)
        qual = qualification_intent(state, cfg)
        if qual is not None:
            intents.append(qual)

        for intent in intents:
            filled = _apply_intent(
                holdings, intent, signals, cfg.floor_symbols, fee_pct
            )
            if filled <= 0:
                continue
            if per_trade_cost_usd > 0:
                _charge_floor_cost(holdings, cfg.floor_symbols, per_trade_cost_usd)
            trades += 1
            daily_trades[day] = daily_trades.get(day, 0) + 1
            if intent.kind is IntentKind.ENTER:
                entries += 1
                position = SleevePosition(
                    symbol=intent.to_symbol,
                    entry_price_usd=signals.prices[intent.to_symbol],
                    entry_momo_score=signals.momentum.get(intent.to_symbol, 0.0),
                    notional_usd=filled * (1 - fee_pct),
                    opened_at=now,
                )
            elif intent.kind is IntentKind.EXIT:
                exits += 1
                position = None
            elif intent.kind is IntentKind.TAKE_PROFIT:
                deleverages += 1
                if position is not None:
                    position = SleevePosition(
                        symbol=position.symbol,
                        entry_price_usd=position.entry_price_usd,
                        entry_momo_score=position.entry_momo_score,
                        notional_usd=max(0.0, position.notional_usd - filled),
                        opened_at=position.opened_at,
                        high_price_usd=position.high_price_usd,
                        profit_taken=True,
                    )
                position = _marked_position(position, holdings, signals)
            elif intent.kind is IntentKind.DELEVERAGE:
                deleverages += 1
                position = _marked_position(position, holdings, signals)
            elif intent.kind is IntentKind.QUALIFY:
                qualifications += 1

        equity, _floor = _equity(holdings, signals, cfg.floor_symbols)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown = max(max_drawdown, 1.0 - equity / peak_equity)

    final_signals = MarketSignals(
        fear_greed=60,
        btc_funding_rate=None,
        momentum={},
        prices=dict(last_prices),
        degraded=False,
    )
    end_equity, _floor = _equity(holdings, final_signals, cfg.floor_symbols)
    return BacktestResult(
        profile=profile,
        start_equity_usd=round(start_equity, 4),
        end_equity_usd=round(end_equity, 4),
        total_return_pct=round(end_equity / start_equity - 1.0, 6),
        max_drawdown_pct=round(max_drawdown, 6),
        dq_breached=max_drawdown >= cfg.dq_drawdown_pct,
        trades=trades,
        entries=entries,
        exits=exits,
        deleverages=deleverages,
        qualifications=qualifications,
        final_position=position.symbol if position else None,
    )


def fetch_binance_candles(
    symbols: list[str], *, days: int, interval: str, timeout: float = 20.0
) -> dict[str, list[Candle]]:
    hours = _interval_hours(interval)
    limit = min(1000, math.ceil(days * 24 / hours) + math.ceil(168 / hours) + 2)
    out: dict[str, list[Candle]] = {}
    with httpx.Client(timeout=timeout) as client:
        for sym in symbols:
            pair = f"{sym}USDT"
            rows = _fetch_klines(client, pair=pair, interval=interval, limit=limit)
            if rows is None:
                continue
            candles = []
            for row in rows:
                candles.append(
                    Candle(
                        ts=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                        symbol=sym,
                        close=float(row[4]),
                    )
                )
            if candles:
                out[sym] = candles
    return out


def _fetch_klines(
    client: httpx.Client, *, pair: str, interval: str, limit: int
) -> list | None:
    last_error: httpx.HTTPError | None = None
    missing_pair = False
    for base_url in BINANCE_RESEARCH_APIS:
        try:
            resp = client.get(
                f"{base_url}/klines",
                params={"symbol": pair, "interval": interval, "limit": limit},
            )
            if resp.status_code == 400:
                missing_pair = True
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            last_error = exc
    if last_error is not None and not missing_pair:
        raise last_error
    return None


def stress_suite() -> dict[str, dict[str, list[Candle]]]:
    return {
        "uptrend": {
            "CAKE": synthetic_candles(
                "CAKE", start=2.0, hourly_returns=[0.0] * 168 + [0.004] * 72
            )
        },
        "crash": {
            "CAKE": synthetic_candles(
                "CAKE",
                start=2.0,
                hourly_returns=[0.0] * 168 + [0.004] * 24 + [-0.12] * 4 + [0.0] * 24,
            )
        },
        "chop": {
            "CAKE": synthetic_candles(
                "CAKE",
                start=2.0,
                hourly_returns=[0.0] * 168 + [0.02, -0.018] * 48,
            )
        },
    }


def synthetic_candles(
    symbol: str,
    *,
    start: float,
    hourly_returns: list[float],
    start_ts: datetime | None = None,
) -> list[Candle]:
    now = start_ts or datetime(2026, 6, 1, tzinfo=timezone.utc)
    price = start
    candles = [Candle(ts=now, symbol=symbol, close=price)]
    for i, ret in enumerate(hourly_returns, start=1):
        price *= 1.0 + ret
        candles.append(Candle(ts=now + timedelta(hours=i), symbol=symbol, close=price))
    return candles


def _momentum_at(
    now: datetime,
    series: dict[str, list[Candle]],
    index_by_time: dict[str, dict[datetime, int]],
    *,
    lookback_24: int,
    lookback_7d: int,
) -> dict[str, float]:
    momentum = {}
    for sym, candles in series.items():
        idx = index_by_time[sym].get(now)
        if idx is None or idx < lookback_24 or idx < lookback_7d:
            continue
        current = candles[idx].close
        close_24 = candles[idx - lookback_24].close
        close_7d = candles[idx - lookback_7d].close
        if close_24 <= 0 or close_7d <= 0:
            continue
        pc24 = (current / close_24 - 1.0) * 100.0
        pc7d = (current / close_7d - 1.0) * 100.0
        momentum[sym] = momentum_score(pc24, pc7d)
    return momentum


def _marked_position(
    position: SleevePosition | None,
    holdings: dict[str, float],
    signals: MarketSignals,
) -> SleevePosition | None:
    if position is None:
        return None
    price = signals.prices.get(position.symbol)
    if price is None:
        return position
    return SleevePosition(
        symbol=position.symbol,
        entry_price_usd=position.entry_price_usd,
        entry_momo_score=position.entry_momo_score,
        notional_usd=max(0.0, holdings.get(position.symbol, 0.0) * price),
        opened_at=position.opened_at,
        high_price_usd=max(position.high_price_usd, position.entry_price_usd, price),
        profit_taken=position.profit_taken,
    )


def _equity(
    holdings: dict[str, float], signals: MarketSignals, stable_symbols: tuple[str, ...]
) -> tuple[float, float]:
    equity = 0.0
    floor = 0.0
    stable = set(stable_symbols)
    for sym, units in holdings.items():
        if sym in stable:
            equity += units
            floor += units
        else:
            price = signals.prices.get(sym)
            if price is not None:
                equity += units * price
    return equity, floor


def _apply_intent(
    holdings: dict[str, float],
    intent,
    signals: MarketSignals,
    stable_symbols: tuple[str, ...],
    fee_pct: float,
) -> float:
    p_from = _price(intent.from_symbol, signals, stable_symbols)
    p_to = _price(intent.to_symbol, signals, stable_symbols)
    if p_from is None or p_to is None or p_from <= 0 or p_to <= 0:
        return 0.0
    available = holdings.get(intent.from_symbol, 0.0) * p_from
    usd = min(intent.notional_usd, available)
    if usd <= 0:
        return 0.0
    holdings[intent.from_symbol] = holdings.get(intent.from_symbol, 0.0) - usd / p_from
    holdings[intent.to_symbol] = (
        holdings.get(intent.to_symbol, 0.0) + (usd * (1 - fee_pct)) / p_to
    )
    for sym in list(holdings):
        if holdings[sym] <= 1e-12:
            del holdings[sym]
    return usd


def _charge_floor_cost(
    holdings: dict[str, float], stable_symbols: tuple[str, ...], cost_usd: float
) -> None:
    remaining = cost_usd
    for sym in stable_symbols:
        available = holdings.get(sym, 0.0)
        if available <= 0:
            continue
        debit = min(available, remaining)
        holdings[sym] = available - debit
        remaining -= debit
        if holdings[sym] <= 1e-12:
            del holdings[sym]
        if remaining <= 0:
            return


def _price(
    symbol: str, signals: MarketSignals, stable_symbols: tuple[str, ...]
) -> float | None:
    if symbol in stable_symbols:
        return 1.0
    return signals.prices.get(symbol)


def _interval_hours(interval: str) -> float:
    if interval.endswith("m"):
        return int(interval[:-1]) / 60
    if interval.endswith("h"):
        return int(interval[:-1])
    if interval.endswith("d"):
        return int(interval[:-1]) * 24
    raise ValueError(f"unsupported interval: {interval}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--start-usd", type=float, default=DEFAULT_START_USD)
    parser.add_argument("--fee-pct", type=float, default=DEFAULT_FEE_PCT)
    parser.add_argument("--per-trade-cost-usd", type=float, default=0.0)
    parser.add_argument("--per-cycle-cost-usd", type=float, default=0.0)
    parser.add_argument("--profiles", default=",".join(RISK_PROFILE_NAMES))
    parser.add_argument("--symbols", default=",".join(SLEEVE_SYMBOLS))
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    profiles = profile_configs()
    selected = [name.strip() for name in args.profiles.split(",") if name.strip()]
    unknown = [name for name in selected if name not in profiles]
    if unknown:
        raise SystemExit(f"unknown profiles: {', '.join(unknown)}")

    if args.stress:
        payload = {}
        for scenario, candles in stress_suite().items():
            payload[scenario] = [
                asdict(
                    simulate(
                        candles,
                        cfg=profiles[name],
                        profile=name,
                        start_usd=args.start_usd,
                        fee_pct=args.fee_pct,
                        per_trade_cost_usd=args.per_trade_cost_usd,
                        per_cycle_cost_usd=args.per_cycle_cost_usd,
                        interval_hours=1.0,
                    )
                )
                for name in selected
            ]
    else:
        symbols = [
            sym.strip()
            for sym in args.symbols.split(",")
            if sym.strip() in SLEEVE_SYMBOLS and is_executable(sym.strip())
        ]
        candles = fetch_binance_candles(symbols, days=args.days, interval=args.interval)
        interval_hours = _interval_hours(args.interval)
        payload = [
            asdict(
                simulate(
                    candles,
                    cfg=profiles[name],
                    profile=name,
                    start_usd=args.start_usd,
                    fee_pct=args.fee_pct,
                    per_trade_cost_usd=args.per_trade_cost_usd,
                    per_cycle_cost_usd=args.per_cycle_cost_usd,
                    interval_hours=interval_hours,
                )
            )
            for name in selected
        ]

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_table(payload)
    return 0


def _print_table(payload) -> None:
    rows = []
    if isinstance(payload, dict):
        for scenario, results in payload.items():
            for result in results:
                rows.append((scenario, result))
    else:
        rows = [("historical", result) for result in payload]
    print(
        "scenario profile end_usd return_pct max_dd_pct dq trades entries exits quals"
    )
    for scenario, result in rows:
        print(
            scenario,
            result["profile"],
            f"{result['end_equity_usd']:.2f}",
            f"{100 * result['total_return_pct']:.2f}",
            f"{100 * result['max_drawdown_pct']:.2f}",
            result["dq_breached"],
            result["trades"],
            result["entries"],
            result["exits"],
            result["qualifications"],
        )


if __name__ == "__main__":
    raise SystemExit(main())
