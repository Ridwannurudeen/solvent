"""The regime brain — an LLM advisor that proposes; the kernel disposes.

SOLVENT's design law: money moves only through the deterministic kernel.
This module asks Claude for a regime read, a candidate ranking, and a
short thesis — but its opinion is wired into the engine so it can only
make the agent *more* conservative (downgrade the regime), never force a
trade or change a size. The thesis text is what fills the glass-box
receipt's reasoning field.

Fail-frozen: any API error, refusal, or malformed output returns None,
and the engine falls back to the deterministic regime alone.
"""

import json
import logging
from dataclasses import dataclass

import anthropic

from ..kernel.allocator import Regime
from ..kernel.state import MarketSignals

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-8"

_REGIME_FROM_STR = {
    "risk-on": Regime.RISK_ON,
    "neutral": Regime.NEUTRAL,
    "risk-off": Regime.RISK_OFF,
}

_SCHEMA = {
    "type": "object",
    "properties": {
        "regime": {"type": "string", "enum": ["risk-on", "neutral", "risk-off"]},
        "confidence": {"type": "number"},
        "thesis": {"type": "string"},
        "ranked_symbols": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["regime", "confidence", "thesis", "ranked_symbols"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are SOLVENT's market-regime advisor. You ADVISE only; you never move money. "
    "A deterministic Python kernel decides every position size, stop, and transaction, "
    "and it treats your regime read as a one-way ratchet: it can only make the agent "
    "more conservative than its own reading, never more aggressive. So be honest, not "
    "bold — when funding is crowded, momentum is thin, or fear is extreme, say so. "
    "Return your regime call, a 0-1 confidence, a one-sentence thesis a human auditor "
    "will read in the trade receipt, and the sleeve candidates ranked best-first."
)


@dataclass(frozen=True)
class RegimeAdvice:
    regime: Regime
    confidence: float
    thesis: str
    ranked_symbols: list[str]


def _prompt(signals: MarketSignals, deterministic: Regime, position: str | None) -> str:
    momo = sorted(signals.momentum.items(), key=lambda kv: kv[1], reverse=True)
    momo_str = ", ".join(f"{s}:{v:.2f}" for s, v in momo[:10]) or "none"
    return (
        f"Kernel's deterministic regime: {deterministic.value}.\n"
        f"Fear & Greed: {signals.fear_greed}.\n"
        f"BTC aggregate funding: {signals.btc_funding_rate}.\n"
        f"Open sleeve position: {position or 'none (flat)'}.\n"
        f"Sleeve momentum (top, best-first): {momo_str}.\n"
        f"Data degraded: {signals.degraded}.\n"
        "Give your regime read for the next hour and rank the candidates."
    )


def advise(
    signals: MarketSignals,
    deterministic: Regime,
    *,
    position: str | None = None,
    client: anthropic.Anthropic | None = None,
    model: str = MODEL,
) -> RegimeAdvice | None:
    """One advisory call. Returns None on any failure (fail-frozen)."""
    client = client or anthropic.Anthropic()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            system=_SYSTEM,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": _SCHEMA},
            },
            messages=[
                {"role": "user", "content": _prompt(signals, deterministic, position)}
            ],
        )
    except anthropic.APIError as e:
        logger.warning("advisor call failed: %s", e)
        return None

    if resp.stop_reason == "refusal":
        logger.warning("advisor refused: %s", getattr(resp, "stop_details", None))
        return None

    text = next((b.text for b in resp.content if b.type == "text"), None)
    if text is None:
        logger.warning("advisor returned no text block")
        return None
    try:
        data = json.loads(text)
        regime = _REGIME_FROM_STR[data["regime"]]
        return RegimeAdvice(
            regime=regime,
            confidence=min(1.0, max(0.0, float(data["confidence"]))),
            thesis=str(data["thesis"]),
            ranked_symbols=[str(s) for s in data["ranked_symbols"]],
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        logger.warning("advisor output malformed: %s", e)
        return None


def make_advisor(model: str = MODEL):
    """Build an advisor callable bound to one client for the engine.

    Returns f(signals, deterministic_regime, position) -> RegimeAdvice | None.
    """
    client = anthropic.Anthropic()

    def _advisor(
        signals: MarketSignals, deterministic: Regime, position: str | None = None
    ) -> RegimeAdvice | None:
        return advise(
            signals, deterministic, position=position, client=client, model=model
        )

    return _advisor
