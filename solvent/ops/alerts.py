"""Telegram alerts — best-effort, never blocks or breaks a cycle.

Configured via env: SOLVENT_TG_BOT_TOKEN + SOLVENT_TG_CHAT_ID.
Unset = alerts silently disabled (paper mode / dev).
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)


def alert(text: str) -> bool:
    token = os.environ.get("SOLVENT_TG_BOT_TOKEN")
    chat_id = os.environ.get("SOLVENT_TG_CHAT_ID")
    if not token or not chat_id:
        logger.debug("alerts disabled (no telegram env)")
        return False
    for attempt in range(2):
        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:4000]},
                timeout=10,
            )
            if resp.status_code == 200:
                return True
            logger.warning("alert non-200: %s", resp.status_code)
        except Exception as e:  # alerting must never break a cycle
            logger.warning("alert failed (attempt %d): %s", attempt + 1, e)
    return False
