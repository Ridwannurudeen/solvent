"""The allocator — every dollar that moves is decided here, deterministically.

Pure functions only: (state, signals, config) -> intents. No I/O, no
randomness, no LLM. The engine may feed LLM-suggested candidate rankings
into MarketSignals.momentum, but sizing, gating, stops, the ratchet and
the kill switch are decided by this module alone.
"""

from dataclasses import dataclass
from enum import Enum

from .allowlist import SLEEVE_SYMBOLS, is_executable
from .rules import RiskConfig
from .state import MarketSignals, PortfolioState


class Regime(Enum):
    RISK_ON = "risk-on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk-off"


class IntentKind(Enum):
    ENTER = "enter"  # floor stable -> sleeve token
    EXIT = "exit"  # sleeve token -> floor stable
    TAKE_PROFIT = "take_profit"  # partial sleeve -> floor after a gain
    DELEVERAGE = "deleverage"  # partial sleeve -> floor (ratchet/kill)
    QUALIFY = "qualify"  # micro stable<->stable qualification trade


@dataclass(frozen=True)
class TradeIntent:
    kind: IntentKind
    from_symbol: str
    to_symbol: str
    notional_usd: float
    reason: str
    # Expected execution price of `to_symbol` at decision time, in USD. Set on
    # ENTER so the settlement verifier can enforce a min-units floor on a buy;
    # None for stable<->stable trades the verifier checks in USD directly.
    expected_price_usd: float | None = None


# Momentum score below which we never enter, regardless of regime.
MIN_ENTRY_MOMO = 1.0

# Risk ordering, most-conservative first. The LLM advisor can only move the
# regime down this scale, never up — it can de-risk, never force a trade.
_REGIME_RISK = {Regime.RISK_OFF: 0, Regime.NEUTRAL: 1, Regime.RISK_ON: 2}


def more_conservative(a: Regime, b: Regime) -> Regime:
    """The lower-risk of two regimes."""
    return a if _REGIME_RISK[a] <= _REGIME_RISK[b] else b


def classify_regime(signals: MarketSignals) -> Regime:
    """Coarse regime from Fear & Greed + aggregate funding.

    Conservative by construction: missing data never upgrades the
    regime, only downgrades it.
    """
    if signals.degraded or signals.fear_greed is None:
        return Regime.RISK_OFF
    fng = signals.fear_greed
    funding = signals.btc_funding_rate
    if fng < 30:
        return Regime.RISK_OFF
    # Extremely positive funding = crowded longs; treat euphoria as risk.
    if funding is not None and funding > 0.0015:
        return Regime.NEUTRAL
    if fng >= 45:
        return Regime.RISK_ON
    return Regime.NEUTRAL


def best_candidate(
    signals: MarketSignals, min_entry_momo: float = MIN_ENTRY_MOMO
) -> tuple[str, float] | None:
    """Highest-momentum executable sleeve symbol above the entry bar."""
    ranked = sorted(
        (
            (sym, score)
            for sym, score in signals.momentum.items()
            if sym in SLEEVE_SYMBOLS and is_executable(sym)
        ),
        key=lambda kv: kv[1],
        reverse=True,
    )
    if ranked and ranked[0][1] >= min_entry_momo:
        return ranked[0]
    return None


def decide(
    state: PortfolioState,
    signals: MarketSignals,
    cfg: RiskConfig,
    regime_override: Regime | None = None,
) -> list[TradeIntent]:
    """One decision cycle. Returns ordered intents (often empty).

    `regime_override` lets the engine substitute an LLM-reconciled regime
    for the entry gate; it is always the more-conservative of the
    deterministic read and the advisor's, so it can only suppress entries.
    """
    intents: list[TradeIntent] = []

    # ── 0. Kill switch: liquidate sleeve, freeze ─────────────────────
    if state.trailing_drawdown_pct >= cfg.kill_switch_drawdown_pct:
        if state.position is not None:
            intents.append(
                TradeIntent(
                    kind=IntentKind.EXIT,
                    from_symbol=state.position.symbol,
                    to_symbol=cfg.floor_symbols[0],
                    notional_usd=state.position.notional_usd,
                    reason=(
                        f"KILL SWITCH: trailing drawdown "
                        f"{state.trailing_drawdown_pct:.1%} >= "
                        f"{cfg.kill_switch_drawdown_pct:.0%} guard"
                    ),
                )
            )
        return intents  # frozen: nothing else this cycle

    # ── 1. Open-position management (stop / decay / ratchet) ────────
    pos = state.position
    if pos is not None:
        if signals.degraded:
            intents.append(
                TradeIntent(
                    kind=IntentKind.EXIT,
                    from_symbol=pos.symbol,
                    to_symbol=cfg.floor_symbols[0],
                    notional_usd=pos.notional_usd,
                    reason=(
                        f"DEGRADED DATA UNWIND: exit {pos.symbol}; "
                        "market-data verification failed"
                    ),
                )
            )
            return intents
        price = signals.prices.get(pos.symbol)
        if price is not None and pos.entry_price_usd > 0:
            pnl_pct = price / pos.entry_price_usd - 1.0
            high_price = max(pos.high_price_usd, pos.entry_price_usd, price)
            stop_price = pos.entry_price_usd * (1.0 - cfg.stop_pct)
            high_pnl = high_price / pos.entry_price_usd - 1.0
            if high_pnl >= cfg.breakeven_activation_pct:
                stop_price = max(stop_price, pos.entry_price_usd)
            if high_pnl >= cfg.trailing_activation_pct:
                stop_price = max(stop_price, high_price * (1.0 - cfg.trailing_stop_pct))
            if price <= stop_price:
                intents.append(
                    TradeIntent(
                        kind=IntentKind.EXIT,
                        from_symbol=pos.symbol,
                        to_symbol=cfg.floor_symbols[0],
                        notional_usd=pos.notional_usd,
                        reason=(
                            f"PROTECTIVE STOP: {pos.symbol} price ${price:.4f} <= "
                            f"${stop_price:.4f} ({pnl_pct:.1%} from entry)"
                        ),
                    )
                )
                return intents
            if (
                not pos.profit_taken
                and pnl_pct >= cfg.take_profit_pct
                and pos.notional_usd * cfg.take_profit_fraction > cfg.qual_trade_usd
            ):
                intents.append(
                    TradeIntent(
                        kind=IntentKind.TAKE_PROFIT,
                        from_symbol=pos.symbol,
                        to_symbol=cfg.floor_symbols[0],
                        notional_usd=pos.notional_usd * cfg.take_profit_fraction,
                        reason=(
                            f"TAKE PROFIT: {pos.symbol} {pnl_pct:.1%} >= "
                            f"{cfg.take_profit_pct:.0%}; sell "
                            f"{cfg.take_profit_fraction:.0%} of sleeve"
                        ),
                    )
                )
                return intents
        momo_now = signals.momentum.get(pos.symbol)
        age_hours = (state.now - pos.opened_at).total_seconds() / 3600
        if (
            age_hours >= cfg.min_hold_hours
            and not signals.degraded
            and momo_now is not None
            and pos.entry_momo_score > 0
            and momo_now < cfg.momo_decay_exit * pos.entry_momo_score
        ):
            intents.append(
                TradeIntent(
                    kind=IntentKind.EXIT,
                    from_symbol=pos.symbol,
                    to_symbol=cfg.floor_symbols[0],
                    notional_usd=pos.notional_usd,
                    reason=(
                        f"DECAY EXIT: {pos.symbol} momentum {momo_now:.2f} < "
                        f"{cfg.momo_decay_exit:.0%} of entry {pos.entry_momo_score:.2f}"
                    ),
                )
            )
            return intents

        # Ratchet: shrink an oversized position after banked gains.
        cap_frac = cfg.sleeve_cap_for_gain(state.banked_gain_pct)
        cap_usd = cap_frac * state.equity_usd
        if pos.notional_usd > cap_usd * 1.10:  # 10% tolerance band
            intents.append(
                TradeIntent(
                    kind=IntentKind.DELEVERAGE,
                    from_symbol=pos.symbol,
                    to_symbol=cfg.floor_symbols[0],
                    notional_usd=pos.notional_usd - cap_usd,
                    reason=(
                        f"RATCHET: banked gain {state.banked_gain_pct:.1%} caps "
                        f"sleeve at {cap_frac:.0%} (${cap_usd:.2f})"
                    ),
                )
            )
            return intents
        return intents  # holding; entries only when flat

    # ── 2. Entry (flat, risk-on, confirmed candidate) ────────────────
    effective_regime = (
        regime_override if regime_override is not None else classify_regime(signals)
    )
    if (
        not signals.degraded
        and state.trades_today < cfg.max_trades_per_day
        and state.equity_usd >= cfg.min_portfolio_usd
        and effective_regime is Regime.RISK_ON
    ):
        cand = best_candidate(signals, cfg.min_entry_momo)
        if cand is not None:
            symbol, score = cand
            cap_frac = min(
                cfg.sleeve_cap_for_gain(state.banked_gain_pct),
                cfg.max_trade_frac,
                1.0 - cfg.floor_frac_min,
            )
            notional = cap_frac * state.equity_usd
            # Entry is funded from the floor; never breach the floor minimum.
            max_from_floor = state.floor_usd - cfg.floor_frac_min * state.equity_usd
            notional = min(notional, max(0.0, max_from_floor))
            if notional > cfg.qual_trade_usd:  # not worth dust entries
                intents.append(
                    TradeIntent(
                        kind=IntentKind.ENTER,
                        from_symbol=cfg.floor_symbols[0],
                        to_symbol=symbol,
                        notional_usd=notional,
                        reason=(
                            f"ENTER: regime risk-on, {symbol} top momentum "
                            f"{score:.2f}, sleeve {cap_frac:.0%} of equity"
                        ),
                        expected_price_usd=signals.prices.get(symbol),
                    )
                )
    return intents


def qualification_intent(state: PortfolioState, cfg: RiskConfig) -> TradeIntent | None:
    """Deadman fallback: a micro stable->stable swap to satisfy the
    1-trade/day rule. Independent of signals by design — it must work
    when every data feed is down."""
    if state.qualified_today:
        return None
    if state.now.hour < cfg.qual_deadline_hour_utc:
        return None
    return TradeIntent(
        kind=IntentKind.QUALIFY,
        from_symbol=cfg.floor_symbols[0],
        to_symbol=cfg.floor_symbols[1],
        notional_usd=cfg.qual_trade_usd,
        reason=(
            f"QUALIFY: no trade by {cfg.qual_deadline_hour_utc:02d}:00 UTC — "
            f"deadman micro-rotation to satisfy 1-trade/day rule"
        ),
    )
