"""The decision cycle — wires signals -> kernel -> executor -> receipts.

One call to run_cycle() is one heartbeat of the agent. The systemd timer
fires it hourly; the deadman path inside it guarantees the daily
qualification trade even when every data feed is down.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .exec.executor import ExecutionResult, Journal, intent_key, intent_payload
from .kernel.allocator import (
    IntentKind,
    TradeIntent,
    classify_regime,
    decide,
    more_conservative,
    qualification_intent,
)
from .kernel.rules import RiskConfig, RISK_PROFILE_NAMES, risk_config_for_profile
from .kernel.state import MarketSignals, PortfolioState, SleevePosition
from .receipts.chain import ReceiptChain

logger = logging.getLogger(__name__)


def _risk_profile_name(cfg: RiskConfig) -> str:
    for name in RISK_PROFILE_NAMES:
        if cfg == risk_config_for_profile(name):
            return name
    return "custom"


@dataclass
class StateStore:
    """Durable agent state (JSON file): competition anchors + position."""

    path: Path
    start_equity_usd: float = 0.0
    peak_equity_usd: float = 0.0
    position: dict | None = None  # serialized SleevePosition

    @classmethod
    def load(cls, path: Path) -> "StateStore":
        if path.exists():
            data = json.loads(path.read_text())
            return cls(path=path, **{k: v for k, v in data.items() if k != "path"})
        return cls(path=path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "start_equity_usd": self.start_equity_usd,
                    "peak_equity_usd": self.peak_equity_usd,
                    "position": self.position,
                },
                indent=1,
            )
        )

    def position_obj(self) -> SleevePosition | None:
        if not self.position:
            return None
        p = dict(self.position)
        p["opened_at"] = datetime.fromisoformat(p["opened_at"])
        p.setdefault("high_price_usd", p.get("entry_price_usd", 0.0))
        p.setdefault("profit_taken", False)
        return SleevePosition(**p)


def compute_equity(
    holdings: dict[str, float], signals: MarketSignals, stable_symbols: tuple[str, ...]
) -> tuple[float, float]:
    """(equity_usd, floor_usd) from symbol->units holdings.

    Stables are valued at $1 (they are the unit of account here);
    everything else needs a price — a held token with no price marks the
    cycle degraded upstream, so this stays simple.
    """
    equity = 0.0
    floor = 0.0
    for sym, units in holdings.items():
        if sym in stable_symbols:
            equity += units
            floor += units
        else:
            price = signals.prices.get(sym)
            if price is not None:
                equity += units * price
    return equity, floor


def run_cycle(
    *,
    source,
    executor,
    journal: Journal,
    receipts: ReceiptChain,
    store: StateStore,
    holdings: dict[str, float],
    cfg: RiskConfig,
    advisor=None,
    pretrade_publisher=None,
    cfg_selector: Callable[[RiskConfig, PortfolioState, MarketSignals], RiskConfig]
    | None = None,
    now: datetime | None = None,
) -> dict:
    """One full decision cycle. Returns a summary dict (for logs/alerts)."""
    now = now or datetime.now(timezone.utc)
    cycle_id = now.strftime("%Y%m%dT%H")
    day = now.strftime("%Y-%m-%d")

    signals, purchases = source.fetch()
    equity, floor = compute_equity(holdings, signals, cfg.floor_symbols)
    _sync_position_notional(store, holdings, signals)

    if store.start_equity_usd <= 0 and equity > 0:
        store.start_equity_usd = equity
    if equity > store.peak_equity_usd:
        store.peak_equity_usd = equity

    confirmed_today = journal.confirmed_trades_on(day)
    state = PortfolioState(
        equity_usd=equity,
        start_equity_usd=store.start_equity_usd or equity,
        peak_equity_usd=store.peak_equity_usd or equity,
        floor_usd=floor,
        position=store.position_obj(),
        trades_today=confirmed_today,
        qualified_today=confirmed_today > 0,
        now=now,
    )

    if cfg_selector is not None:
        cfg = cfg_selector(cfg, state, signals)

    deterministic_regime = classify_regime(signals)
    advice = None
    if advisor is not None:
        pos = state.position.symbol if state.position is not None else None
        advice = advisor(signals, deterministic_regime, pos)
    effective_regime = (
        more_conservative(deterministic_regime, advice.regime)
        if advice is not None
        else deterministic_regime
    )
    regime = effective_regime.value

    intents: list[TradeIntent] = decide(
        state, signals, cfg, regime_override=effective_regime
    )
    qual = qualification_intent(state, cfg)
    if qual is not None:
        intents.append(qual)

    executions: list[ExecutionResult] = []
    for intent in intents:
        key = intent_key(intent, cycle_id)
        payload = intent_payload(intent, cycle_id)
        pre_trade = receipts.append(
            ts=now.isoformat(),
            phase="pre_trade_commit",
            cycle_id=cycle_id,
            intent_key=key,
            signals={
                "fear_greed": signals.fear_greed,
                "btc_funding_rate": signals.btc_funding_rate,
                "momentum": {k: round(v, 3) for k, v in signals.momentum.items()},
                "percent_change_1h": {
                    k: round(v, 3) for k, v in signals.percent_change_1h.items()
                },
                "volume_change_24h": {
                    k: round(v, 3) for k, v in signals.volume_change_24h.items()
                },
                "volume_24h_usd": {
                    k: round(v, 2) for k, v in signals.volume_24h_usd.items()
                },
                "market_cap_usd": {
                    k: round(v, 2) for k, v in signals.market_cap_usd.items()
                },
                "degraded": signals.degraded,
                "regime_deterministic": deterministic_regime.value,
            },
            regime=regime,
            thesis=intent.reason,
            intents=[payload],
            executions=[],
            equity_usd=round(equity, 2),
            dq_headroom_pct=round(state.dq_headroom_pct, 4),
        )
        pre_trade_anchor_tx_hash = None
        if pretrade_publisher is not None:
            pre_trade_anchor_tx_hash = pretrade_publisher.publish(
                cycle_id=cycle_id, intent_key=key, commit_hash=pre_trade.hash
            )
        result = executor.execute(intent, cycle_id)
        executions.append(result)
        receipts.append(
            ts=now.isoformat(),
            phase="execution_seal",
            cycle_id=cycle_id,
            intent_key=key,
            pre_trade_hash=pre_trade.hash,
            regime=regime,
            thesis=result.detail[:200],
            intents=[payload],
            executions=[
                {"key": result.intent_key, "ok": result.ok, "tx_hash": result.tx_hash}
            ],
            execution_seal={
                "ok": result.ok,
                "tx_hash": result.tx_hash,
                "pre_trade_anchor_tx_hash": pre_trade_anchor_tx_hash,
                "detail": result.detail[:500],
            },
            equity_usd=round(equity, 2),
            dq_headroom_pct=round(state.dq_headroom_pct, 4),
        )
        if result.ok:
            _apply_position_effect(store, intent, signals)

    receipt = receipts.append(
        ts=now.isoformat(),
        phase="cycle_summary",
        cycle_id=cycle_id,
        data_purchases=purchases,
        signals={
            "fear_greed": signals.fear_greed,
            "btc_funding_rate": signals.btc_funding_rate,
            "momentum": {k: round(v, 3) for k, v in signals.momentum.items()},
            "percent_change_1h": {
                k: round(v, 3) for k, v in signals.percent_change_1h.items()
            },
            "volume_change_24h": {
                k: round(v, 3) for k, v in signals.volume_change_24h.items()
            },
            "volume_24h_usd": {
                k: round(v, 2) for k, v in signals.volume_24h_usd.items()
            },
            "market_cap_usd": {
                k: round(v, 2) for k, v in signals.market_cap_usd.items()
            },
            "active_risk_profile": _risk_profile_name(cfg),
            "degraded": signals.degraded,
            "regime_deterministic": deterministic_regime.value,
            "advisor": (
                {
                    "regime": advice.regime.value,
                    "confidence": round(advice.confidence, 3),
                    "thesis": advice.thesis,
                    "ranked_symbols": advice.ranked_symbols,
                }
                if advice is not None
                else None
            ),
        },
        regime=regime,
        thesis=(advice.thesis if advice is not None else None)
        or "; ".join(i.reason for i in intents)
        or f"hold ({regime})",
        intents=[
            {
                "kind": i.kind.value,
                "from": i.from_symbol,
                "to": i.to_symbol,
                "notional_usd": round(i.notional_usd, 2),
            }
            for i in intents
        ],
        executions=[
            {"key": e.intent_key, "ok": e.ok, "tx_hash": e.tx_hash} for e in executions
        ],
        equity_usd=round(equity, 2),
        dq_headroom_pct=round(state.dq_headroom_pct, 4),
    )
    store.save()
    summary = {
        "cycle": cycle_id,
        "regime": regime,
        "active_risk_profile": _risk_profile_name(cfg),
        "equity_usd": round(equity, 2),
        "intents": len(intents),
        "executed_ok": sum(1 for e in executions if e.ok),
        "receipt_seq": receipt.seq,
        "receipt_hash": receipt.hash,
        "data_cost_usd": round(sum(p.cost_usdc for p in purchases), 4),
        "degraded": signals.degraded,
    }
    logger.info("cycle done: %s", summary)
    return summary


def _apply_position_effect(
    store: StateStore, intent: TradeIntent, signals: MarketSignals
) -> None:
    """Track the sleeve position through confirmed intents."""
    if intent.kind is IntentKind.ENTER:
        price = signals.prices.get(intent.to_symbol, 0.0)
        store.position = {
            "symbol": intent.to_symbol,
            "entry_price_usd": price,
            "entry_momo_score": signals.momentum.get(intent.to_symbol, 0.0),
            "notional_usd": intent.notional_usd,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "high_price_usd": price,
            "profit_taken": False,
        }
    elif intent.kind is IntentKind.EXIT:
        store.position = None
    elif (
        intent.kind in (IntentKind.DELEVERAGE, IntentKind.TAKE_PROFIT)
        and store.position
    ):
        store.position["notional_usd"] = max(
            0.0, store.position["notional_usd"] - intent.notional_usd
        )
        if intent.kind is IntentKind.TAKE_PROFIT:
            store.position["profit_taken"] = True


def _sync_position_notional(
    store: StateStore, holdings: dict[str, float], signals: MarketSignals
) -> None:
    """Mark the persisted sleeve size to the current wallet value."""
    if not store.position:
        return
    symbol = store.position.get("symbol")
    if not isinstance(symbol, str):
        return
    price = signals.prices.get(symbol)
    units = holdings.get(symbol)
    if price is None or units is None:
        return
    store.position["notional_usd"] = max(0.0, units * price)
    store.position["high_price_usd"] = max(
        float(store.position.get("high_price_usd") or 0.0),
        float(store.position.get("entry_price_usd") or 0.0),
        price,
    )
