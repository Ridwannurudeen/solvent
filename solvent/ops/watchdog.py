"""Heartbeat watchdog — alerts when the decision cycle stops running.

run.py writes <data-dir>/heartbeat (an ISO-8601 UTC timestamp) at the end
of every cycle. This checks that file's freshness and alerts via Telegram
when it goes stale, catching a wedged timer, a crash loop, or a dead box.

With the oneshot+timer unit design, a single crashed cycle self-heals on
the next timer fire (Persistent=true also replays missed runs after
downtime); the watchdog exists for the case where firing itself stops.

    python -m solvent.ops.watchdog --data-dir /opt/solvent/data --max-age 5400
"""

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from .alerts import alert

logger = logging.getLogger(__name__)


def heartbeat_age(data_dir: Path, now: datetime | None = None) -> float | None:
    """Seconds since the last heartbeat, or None if it was never written."""
    hb = data_dir / "heartbeat"
    if not hb.exists():
        return None
    now = now or datetime.now(timezone.utc)
    last = datetime.fromisoformat(hb.read_text().strip())
    return (now - last).total_seconds()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument(
        "--max-age",
        type=int,
        default=5400,
        metavar="SECONDS",
        help="stale threshold; default 5400 (1.5h) for an hourly cycle",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    age = heartbeat_age(args.data_dir)
    if age is None:
        logger.error("no heartbeat file at %s", args.data_dir / "heartbeat")
        alert("SOLVENT WATCHDOG: no heartbeat file — cycle has never run")
        return 1
    if age > args.max_age:
        logger.error("heartbeat stale: %.0fs > %ds", age, args.max_age)
        alert(
            f"SOLVENT WATCHDOG: heartbeat stale ({age / 3600:.1f}h) — "
            f"decision cycle appears down"
        )
        return 1
    logger.info("heartbeat fresh: %.0fs old", age)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
