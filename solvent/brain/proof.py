"""Hash-bound inference commitment packets for decision receipts."""

import hashlib
import json
from typing import Any

from ..kernel.allocator import Regime, classify_regime, more_conservative
from ..kernel.state import MarketSignals
from .advisor import MODEL, RegimeAdvice

SCHEMA = "solvent.inference-commitment.v1"
VERIFICATION_SCHEMA = "solvent.inference-reexecution.v1"


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
        "source_deviation_pct": _round_map(signals.source_deviation_pct, 6),
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
    """Create a reproducible commitment packet for the regime decision.

    This is not a TEE attestation or proof that a model ran. It is a stable
    input hash, output hash, and commitment hash that binds the model/kernel
    read to the receipt hash chain so reviewers can recompute the committed
    bytes.
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
        "commitment_hash": proof_hash,
        "proof_hash": proof_hash,
        "proof_type": "hash_commitment",
        "verification_scope": "commits declared inputs and outputs; does not attest runtime execution",
        "input": input_payload,
        "output": output_payload,
    }


def verify_inference_proof(proof: dict[str, Any]) -> dict[str, Any]:
    """Recompute the deterministic parts of an inference commitment.

    This is stronger than a hash-only receipt check because it re-runs the
    deterministic regime classifier from the committed signal bytes. It still
    does not attest that an external model process ran.
    """
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, *, required: bool = True) -> None:
        checks.append({"name": name, "ok": ok, "required": required, "detail": detail})

    input_payload = proof.get("input")
    output_payload = proof.get("output")
    input_ok = isinstance(input_payload, dict)
    output_ok = isinstance(output_payload, dict)
    add("input_payload_present", input_ok, f"present={input_ok}")
    add("output_payload_present", output_ok, f"present={output_ok}")

    expected_input_hash = sha256_json(input_payload) if input_ok else None
    expected_output_hash = sha256_json(output_payload) if output_ok else None
    add(
        "input_hash_matches",
        bool(expected_input_hash and proof.get("input_hash") == expected_input_hash),
        f"declared={proof.get('input_hash')} recomputed={expected_input_hash}",
    )
    add(
        "output_hash_matches",
        bool(output_ok and proof.get("output_hash") == expected_output_hash),
        f"declared={proof.get('output_hash')} recomputed={expected_output_hash}",
    )

    expected_commitment_hash = sha256_json(
        {
            "schema": proof.get("schema"),
            "mode": proof.get("mode"),
            "model_id": proof.get("model_id"),
            "input_hash": proof.get("input_hash"),
            "output_hash": proof.get("output_hash"),
        }
    )
    commitment_hash = proof.get("commitment_hash") or proof.get("proof_hash")
    add(
        "commitment_hash_matches",
        commitment_hash == expected_commitment_hash,
        f"declared={commitment_hash} recomputed={expected_commitment_hash}",
    )

    recomputed_regime = None
    declared_regime = None
    expected_effective = None
    if input_ok:
        try:
            signals = _signals_from_commitment_input(input_payload)
            recomputed_regime = classify_regime(signals)
            declared_regime = Regime(input_payload.get("deterministic_regime"))
            add(
                "deterministic_regime_reexecutes",
                recomputed_regime is declared_regime,
                f"declared={input_payload.get('deterministic_regime')} recomputed={recomputed_regime.value}",
            )
        except (TypeError, ValueError) as exc:
            add("deterministic_regime_reexecutes", False, str(exc))

    if output_ok and recomputed_regime is not None:
        mode = proof.get("mode")
        try:
            effective_regime = Regime(output_payload.get("effective_regime"))
            if mode == "deterministic":
                expected_effective = recomputed_regime
                detail = (
                    f"declared={effective_regime.value} "
                    f"recomputed={expected_effective.value}"
                )
                ok = effective_regime is expected_effective
            elif mode == "advisor":
                advisor = output_payload.get("advisor")
                if not isinstance(advisor, dict):
                    raise ValueError("advisor output missing")
                advisor_regime = Regime(advisor.get("regime"))
                expected_effective = more_conservative(
                    recomputed_regime, advisor_regime
                )
                detail = (
                    f"declared={effective_regime.value} "
                    f"deterministic={recomputed_regime.value} "
                    f"advisor={advisor_regime.value} "
                    f"recomputed={expected_effective.value}"
                )
                ok = effective_regime is expected_effective
            else:
                raise ValueError(f"unknown mode: {mode}")
            add("effective_regime_reexecutes", ok, detail)
        except (TypeError, ValueError) as exc:
            add("effective_regime_reexecutes", False, str(exc))

    ok = all(check["ok"] for check in checks if check["required"])
    return {
        "schema": VERIFICATION_SCHEMA,
        "ok": ok,
        "proof_hash": proof.get("proof_hash"),
        "commitment_hash": commitment_hash,
        "mode": proof.get("mode"),
        "model_id": proof.get("model_id"),
        "attestation_type": "deterministic_reexecution",
        "model_runtime_attested": False,
        "verification_scope": (
            "recomputes hashes and deterministic regime reconciliation; "
            "does not provide TEE, zk, or external model runtime attestation"
        ),
        "recomputed": {
            "deterministic_regime": (
                recomputed_regime.value if recomputed_regime is not None else None
            ),
            "effective_regime": (
                expected_effective.value if expected_effective is not None else None
            ),
        },
        "checks": checks,
    }


def _signals_from_commitment_input(payload: dict[str, Any]) -> MarketSignals:
    signals = payload.get("signals")
    if not isinstance(signals, dict):
        raise ValueError("input.signals missing")
    return MarketSignals(
        fear_greed=_optional_int(signals.get("fear_greed")),
        btc_funding_rate=_optional_float(signals.get("btc_funding_rate")),
        momentum=_float_map(signals.get("momentum")),
        percent_change_1h=_float_map(signals.get("percent_change_1h")),
        volume_change_24h=_float_map(signals.get("volume_change_24h")),
        source_deviation_pct=_float_map(signals.get("source_deviation_pct")),
        degraded=bool(signals.get("degraded")),
    )


def _float_map(value: object) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("signal map is not an object")
    return {str(k): float(v) for k, v in value.items()}


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)
