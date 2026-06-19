"""Independent public proof watcher for SOLVENT.

The watcher is read-only: it fetches public endpoints, summarizes their health,
and can append a hash-bound attestation JSONL record. It never signs, trades,
or reads local secrets.
"""

import argparse
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from ..brain.proof import sha256_json

DEFAULT_PUBLIC_BASE = "https://solvent.gudman.xyz"
Fetcher = Callable[[str], tuple[int, str]]


def _urlopen_fetch(url: str) -> tuple[int, str]:
    with urlopen(url, timeout=15) as response:
        return response.status, response.read().decode("utf-8")


def _fetch_json(fetcher: Fetcher, url: str) -> tuple[dict | None, str]:
    try:
        status, body = fetcher(url)
    except (OSError, URLError, TimeoutError) as exc:
        return None, str(exc)
    if status != 200:
        return None, f"HTTP {status}"
    try:
        return json.loads(body), "ok"
    except ValueError as exc:
        return None, f"invalid JSON: {exc}"


def public_attestation(
    *,
    public_base: str = DEFAULT_PUBLIC_BASE,
    fetcher: Fetcher = _urlopen_fetch,
    now: datetime | None = None,
) -> dict:
    base = public_base.rstrip("/")
    fetched: dict[str, dict | None] = {}
    errors: dict[str, str] = {}
    for name, path in (
        ("verify", "/verify"),
        ("state", "/state"),
        ("policy", "/policy"),
        ("policy_compliance", "/policy-compliance"),
        ("signal", "/signal"),
        ("inference_verification", "/inference-verification"),
        ("strategy_evidence", "/strategy-evidence"),
    ):
        payload, detail = _fetch_json(fetcher, f"{base}{path}")
        fetched[name] = payload
        if detail != "ok":
            errors[name] = detail

    verify = fetched.get("verify") or {}
    state = fetched.get("state") or {}
    compliance = fetched.get("policy_compliance") or {}
    signal = fetched.get("signal") or {}
    policy_payload = fetched.get("policy") or {}
    inference_verification = fetched.get("inference_verification") or {}
    strategy_evidence = fetched.get("strategy_evidence") or {}
    coverage = verify.get("anchor_coverage") or state.get("anchor_coverage") or {}
    body = {
        "schema": "solvent.public-attestation.v1",
        "observed_at": (now or datetime.now(timezone.utc)).isoformat(),
        "public_base": base,
        "checks": {
            "receipt_chain_ok": verify.get("ok") is True,
            "state_alive": state.get("alive") is True,
            "policy_compliant": compliance.get("ok") is True,
            "signal_hash_present": bool(signal.get("signal_hash")),
            "anchor_matches_local_log": coverage.get("anchor_matches_local_log")
            is True,
            "inference_reexecution_ok": inference_verification.get("ok") is True,
            "strategy_claims_no_guarantee": strategy_evidence.get("edge_claim", {}).get(
                "guaranteed"
            )
            is False,
        },
        "evidence": {
            "receipt_count": verify.get("count"),
            "head_hash": verify.get("head_hash"),
            "agent_id": state.get("agent_id"),
            "runtime_status": state.get("runtime_status"),
            "policy_hash": compliance.get("policy_hash")
            or policy_payload.get("manifest_hash"),
            "unanchored_count": coverage.get("unanchored_count"),
            "signal_hash": signal.get("signal_hash"),
            "inference_verified_count": inference_verification.get("verified_count"),
            "strategy_scenarios": sorted(
                (strategy_evidence.get("scenarios") or {}).keys()
            ),
        },
        "errors": errors,
    }
    body["ok"] = all(body["checks"].values()) and not errors
    body["attestation_hash"] = sha256_json(body)
    return body


def append_attestation(path: Path, attestation: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(attestation, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-base", default=DEFAULT_PUBLIC_BASE)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    attestation = public_attestation(public_base=args.public_base)
    if args.out:
        append_attestation(args.out, attestation)
    print(json.dumps(attestation, indent=2, sort_keys=True))
    return 0 if attestation["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
