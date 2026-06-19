"""Independent deadman — guarantees the daily qualification trade.

The hourly decision cycle already fires a qualification micro-trade when
no trade has happened by the deadline (allocator.qualification_intent).
But that path runs *inside* run_cycle, so it only fires if the cycle
process ran at all. If the timer, the box, or the interpreter died, no
cycle runs and the agent silently misses the >=1-trade/day rule -> DQ.

This module is that failure's backstop: a separate process, on a separate
timer, that reads only the journal and fires a stable<->stable micro-swap
through the executor when no confirmed trade exists for the UTC day. It
never touches signals, the kernel decision, or portfolio state -> it works
when every data feed (and the hourly cycle) is down.

    python -m solvent.ops.deadman --mode paper --data-dir /opt/solvent/data
"""

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from ..exec.executor import (
    Journal,
    PaperExecutor,
    TwakExecutor,
    intent_key,
    intent_payload,
)
from ..kernel.allocator import IntentKind, TradeIntent
from ..kernel.rules import RiskConfig
from ..receipts.chain import ReceiptChain
from ..receipts.pretrade import build_pretrade_publisher
from .alerts import alert
from .lock import SingleWriterLock

logger = logging.getLogger(__name__)


def run_deadman(
    *,
    executor,
    journal: Journal,
    cfg: RiskConfig,
    receipts: ReceiptChain | None = None,
    pretrade_publisher=None,
    now: datetime | None = None,
) -> dict:
    """Fire the fallback qualification trade if the day is unqualified.

    Returns a summary dict; "action" is "qualify" only when a trade was
    actually attempted this call.
    """
    now = now or datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")

    if journal.confirmed_trades_on(day) > 0:
        return {"action": "none", "reason": "already qualified today", "day": day}
    if now.hour < cfg.qual_deadline_hour_utc:
        return {
            "action": "none",
            "reason": f"before {cfg.qual_deadline_hour_utc:02d}:00 UTC deadline",
            "day": day,
        }

    intent = TradeIntent(
        kind=IntentKind.QUALIFY,
        from_symbol=cfg.floor_symbols[0],
        to_symbol=cfg.floor_symbols[1],
        notional_usd=cfg.qual_trade_usd,
        reason="DEADMAN: independent fallback qualification trade",
    )
    cycle_id = now.strftime("deadman-%Y%m%d")
    key = intent_key(intent, cycle_id)
    payload = intent_payload(intent, cycle_id)
    pre_trade = None
    if receipts is not None:
        pre_trade = receipts.append(
            ts=now.isoformat(),
            phase="pre_trade_commit",
            cycle_id=cycle_id,
            intent_key=key,
            signals={"source": "deadman"},
            regime="qualification",
            thesis=intent.reason,
            intents=[payload],
            executions=[],
        )
    pre_trade_anchor_tx_hash = None
    if pretrade_publisher is not None and pre_trade is not None:
        pre_trade_anchor_tx_hash = pretrade_publisher.publish(
            cycle_id=cycle_id, intent_key=key, commit_hash=pre_trade.hash
        )
    result = executor.execute(intent, cycle_id)
    if receipts is not None:
        receipts.append(
            ts=now.isoformat(),
            phase="execution_seal",
            cycle_id=cycle_id,
            intent_key=key,
            pre_trade_hash=pre_trade.hash if pre_trade is not None else None,
            regime="qualification",
            thesis=result.detail[:200],
            intents=[payload],
            executions=[
                {
                    "key": result.intent_key,
                    "ok": result.ok,
                    "tx_hash": result.tx_hash,
                    "outcome": result.outcome,
                    "verification": result.verification,
                }
            ],
            execution_seal={
                "ok": result.ok,
                "outcome": result.outcome,
                "applies_state_change": result.applies_state_change,
                "tx_hash": result.tx_hash,
                "pre_trade_anchor_tx_hash": pre_trade_anchor_tx_hash,
                "verification": result.verification,
                "detail": result.detail[:500],
            },
        )
    return {
        "action": "qualify",
        "ok": result.ok,
        "tx_hash": result.tx_hash,
        "detail": result.detail,
        "day": day,
    }


def make_executor(mode: str, journal: Journal, cfg: RiskConfig):
    if mode == "paper":
        return PaperExecutor(journal)
    receipt_verifier = None
    wallet_address = os.environ.get("SOLVENT_WALLET_ADDRESS")
    if wallet_address:
        from ..exec.livebook import LiveBook, LiveReceiptVerifier, make_web3

        network = os.environ.get("SOLVENT_TRADE_NETWORK", "bsc-mainnet")
        book = LiveBook(make_web3(network), wallet_address)
        receipt_verifier = LiveReceiptVerifier(
            book,
            slippage_pct=cfg.max_slippage_pct,
            timeout_s=int(os.environ.get("SOLVENT_TX_RECEIPT_TIMEOUT_S", "180")),
            confirmations=int(os.environ.get("SOLVENT_TX_CONFIRMATIONS", "1")),
        )
    return TwakExecutor(
        journal,
        password=os.environ.get("TWAK_WALLET_PASSWORD"),
        chain=os.environ.get("SOLVENT_TWAK_CHAIN", "bsc"),
        slippage_pct=cfg.max_slippage_pct,
        receipt_verifier=receipt_verifier,
        balance_reader=receipt_verifier.before if receipt_verifier else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["paper", "live"], required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    cfg = RiskConfig()
    try:
        with SingleWriterLock(args.data_dir / "writer.lock"):
            journal = Journal(args.data_dir / "journal.jsonl")
            summary = run_deadman(
                executor=make_executor(args.mode, journal, cfg),
                journal=journal,
                cfg=cfg,
                receipts=ReceiptChain(args.data_dir / "receipts.jsonl"),
                pretrade_publisher=build_pretrade_publisher(),
            )
    except RuntimeError as exc:
        if "state writer already active" in str(exc):
            logger.warning("deadman skipped: %s", exc)
            return 0
        raise
    logger.info("deadman: %s", summary)
    if summary["action"] == "qualify":
        ok = summary["ok"]
        alert(
            f"SOLVENT DEADMAN [{args.mode}] qualification trade {'OK' if ok else 'FAILED'}: {json.dumps(summary)}"
        )
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
