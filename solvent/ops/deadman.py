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
from datetime import datetime, timezone
from pathlib import Path

from ..exec.executor import Journal, PaperExecutor
from ..kernel.allocator import IntentKind, TradeIntent
from ..kernel.rules import RiskConfig
from .alerts import alert

logger = logging.getLogger(__name__)


def run_deadman(
    *,
    executor,
    journal: Journal,
    cfg: RiskConfig,
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
    result = executor.execute(intent, now.strftime("deadman-%Y%m%d"))
    return {
        "action": "qualify",
        "ok": result.ok,
        "tx_hash": result.tx_hash,
        "detail": result.detail,
        "day": day,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["paper", "live"], required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.mode == "live":
        raise SystemExit(
            "live mode is enabled in Phase 2 (needs TWAK credentials + funded wallet)"
        )

    journal = Journal(args.data_dir / "journal.jsonl")
    summary = run_deadman(
        executor=PaperExecutor(journal), journal=journal, cfg=RiskConfig()
    )
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
