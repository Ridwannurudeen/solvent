"""Hash-bound inference proof packets for decision receipts."""

import hashlib
import json
from typing import Any

from ..kernel.allocator import Regime
from ..kernel.state import MarketSignals
from .advisor import MODEL, RegimeAdvice

SCHEMA = "solvent.inference-proof.v1"


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sha256_json(payload: dict[str, Any]) -> str:
    return "0x" + hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _round_map(values: dict[str, float], digits: int) -> dict[str, float]:
    return {k: round(v, digits) for k, v in sorted(values.items())}


def compact_signals(signals: MarketSignals) -> dict[str, Any]:
    return {
        "fear_greed": signals.fear_greed,
        "btc_funding_rate": signals.btc_funding_rate,
        "momentum": _round_map(signals.momentum, 6),
        "percent_change_1h": _round_map(signals.percent_change_1h, 6),
        "volume_change_24h": _round_map(signals.volume_change_24h, 6),
        "degraded": signals.degraded,
    }


def compact_advice(advice: RegimeAdvice | None) -> dict[str, Any] | None:
    if advice is None:
        return None
    return {
        "regime": advice.regime.value,
        "confidence": round(advice.confidence, 6),
        "thesis": advice.thesis,
        "ranked_symbols": advice.ranked_symbols,
    }


def build_inference_proof(
    *,
    cycle_id: str,
    signals: MarketSignals,
    deterministic_regime: Regime,
    effective_regime: Regime,
    active_risk_profile: str,
    position_symbol: str | None,
    advice: RegimeAdvice | None,
) -> dict[str, Any]:
    """Create a reproducible proof packet for the regime decision.

    This is not a TEE attestation. It is a proof-of-inference style packet:
    a stable input hash, output hash, and proof hash that binds the model/kernel
    read to the receipt hash chain and lets reviewers recompute what was used.
    """
    mode = "advisor" if advice is not None else "deterministic"
    input_payload = {
        "cycle_id": cycle_id,
        "active_risk_profile": active_risk_profile,
        "position_symbol": position_symbol,
        "deterministic_regime": deterministic_regime.value,
        "signals": compact_signals(signals),
    }
    output_payload = {
        "effective_regime": effective_regime.value,
        "advisor": compact_advice(advice),
    }
    input_hash = sha256_json(input_payload)
    output_hash = sha256_json(output_payload)
    model_id = MODEL if advice is not None else "solvent-deterministic-kernel-v1"
    proof_hash = sha256_json(
        {
            "schema": SCHEMA,
            "mode": mode,
            "model_id": model_id,
            "input_hash": input_hash,
            "output_hash": output_hash,
        }
    )
    return {
        "schema": SCHEMA,
        "mode": mode,
        "model_id": model_id,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "proof_hash": proof_hash,
        "input": input_payload,
        "output": output_payload,
    }
