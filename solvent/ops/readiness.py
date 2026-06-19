"""No-broadcast readiness checks for submission and live cutover.

The command reports public proof health, local receipt-chain integrity, preflight
posture, and remaining approval-gated items. It never prints secret values and
never calls a value-moving TWAK command.
"""

import argparse
import json
import os
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import urlopen

from ..receipts.chain import GENESIS_HASH, verify_chain
from .preflight import REQUIRED_LIVE_ENV, load_env_file, preflight

DEFAULT_PUBLIC_BASE = "https://solvent.gudman.xyz"

Fetcher = Callable[[str], tuple[int, str]]


def _urlopen_fetch(url: str) -> tuple[int, str]:
    with urlopen(url, timeout=15) as response:
        return response.status, response.read().decode("utf-8")


def _check(name: str, ok: bool, detail: str, *, required: bool = True) -> dict:
    return {"name": name, "ok": ok, "required": required, "detail": detail}


def _fetch_json(fetcher: Fetcher, url: str) -> tuple[bool, dict | None, str]:
    try:
        status, body = fetcher(url)
    except (OSError, URLError, TimeoutError) as exc:
        return False, None, str(exc)
    if status != 200:
        return False, None, f"HTTP {status}"
    try:
        return True, json.loads(body), "ok"
    except ValueError as exc:
        return False, None, f"invalid JSON: {exc}"


def _fetch_text(fetcher: Fetcher, url: str) -> tuple[bool, str]:
    try:
        status, body = fetcher(url)
    except (OSError, URLError, TimeoutError) as exc:
        return False, str(exc)
    if status != 200:
        return False, f"HTTP {status}"
    return True, body


def _local_chain(data_dir: Path) -> tuple[dict, list[dict]]:
    path = data_dir / "receipts.jsonl"
    if not path.exists():
        return (
            {"ok": True, "count": 0, "head_hash": GENESIS_HASH},
            [_check("local_receipt_chain", True, "no local receipts yet")],
        )
    ok, count, head = verify_chain(path)
    return (
        {"ok": ok, "count": count, "head_hash": head},
        [_check("local_receipt_chain", ok, f"{count} receipts, head {head}")],
    )


def _public_checks(
    public_base: str,
    local: dict,
    fetcher: Fetcher,
    *,
    skip_public: bool,
) -> tuple[dict, list[dict]]:
    if skip_public:
        return {}, [_check("public_endpoints", True, "skipped", required=False)]

    base = public_base.rstrip("/")
    checks: list[dict] = []
    public: dict = {}

    ok, verify_payload, detail = _fetch_json(fetcher, f"{base}/verify")
    public["verify"] = verify_payload
    checks.append(
        _check(
            "public_verify",
            ok and bool(verify_payload and verify_payload.get("ok") is True),
            detail if not verify_payload else f"{verify_payload.get('count')} receipts",
        )
    )
    if ok and verify_payload and local["count"] > 0:
        checks.append(
            _check(
                "public_head_matches_local",
                verify_payload.get("head_hash") == local["head_hash"],
                f"public={verify_payload.get('head_hash')} local={local['head_hash']}",
            )
        )

    ok, state_payload, detail = _fetch_json(fetcher, f"{base}/state")
    public["state"] = state_payload
    checks.append(
        _check(
            "public_state_alive",
            ok and bool(state_payload and state_payload.get("alive") is True),
            detail if not state_payload else f"alive={state_payload.get('alive')}",
        )
    )
    checks.append(
        _check(
            "public_agent_identity",
            ok
            and bool(
                state_payload
                and state_payload.get("agent_id") == 136384
                and state_payload.get("anchor_network") == "bsc-mainnet"
            ),
            (
                detail
                if not state_payload
                else f"agent_id={state_payload.get('agent_id')} "
                f"network={state_payload.get('anchor_network')}"
            ),
        )
    )
    checks.append(
        _check(
            "public_anchor_present",
            ok and bool(state_payload and state_payload.get("anchors")),
            detail
            if not state_payload
            else f"anchors={len(state_payload.get('anchors', []))}",
        )
    )

    ok, signal_payload, detail = _fetch_json(fetcher, f"{base}/signal")
    public["signal"] = signal_payload
    checks.append(
        _check(
            "public_signal_payload",
            ok
            and bool(
                signal_payload
                and signal_payload.get("schema") == "solvent.erc8183.signal.v1"
                and signal_payload.get("signal_hash")
            ),
            detail
            if not signal_payload
            else f"signal_hash={signal_payload.get('signal_hash')}",
        )
    )

    ok, policy_payload, detail = _fetch_json(fetcher, f"{base}/policy")
    public["policy"] = policy_payload
    checks.append(
        _check(
            "public_policy_manifest",
            ok and bool(policy_payload and policy_payload.get("manifest_hash")),
            detail
            if not policy_payload
            else f"manifest_hash={policy_payload.get('manifest_hash')}",
        )
    )
    checks.append(
        _check(
            "public_policy_signature",
            ok
            and bool(
                policy_payload
                and isinstance(policy_payload.get("signature"), dict)
                and policy_payload["signature"].get("signature")
                and policy_payload["signature"].get("signer")
            ),
            detail
            if not policy_payload
            else f"signed={bool(policy_payload.get('signature'))}",
        )
    )
    checks.append(
        _check(
            "public_policy_anchor",
            ok
            and bool(
                policy_payload
                and isinstance(policy_payload.get("anchor"), dict)
                and policy_payload["anchor"].get("tx_hash")
            ),
            detail
            if not policy_payload
            else f"anchored={bool(policy_payload.get('anchor'))}",
        )
    )

    ok, proofs_payload, detail = _fetch_json(fetcher, f"{base}/inference-commitments")
    public["inference_commitments"] = proofs_payload
    checks.append(
        _check(
            "public_inference_commitments",
            ok and isinstance(proofs_payload, list),
            detail
            if not isinstance(proofs_payload, list)
            else f"{len(proofs_payload)} commitments",
        )
    )

    ok, proof_body = _fetch_text(fetcher, f"{base}/proof")
    public["proof_present"] = ok
    checks.append(
        _check(
            "public_proof_page",
            ok
            and "Mainnet evidence" in proof_body
            and "Track 1 registration" in proof_body,
            "proof evidence markers present" if ok else proof_body,
        )
    )
    return public, checks


def _preflight_checks(report: dict, profile: str) -> list[dict]:
    checks = [
        _check(
            "journal_clear",
            not report["journal_has_unresolved"],
            f"journal_has_unresolved={report['journal_has_unresolved']}",
        )
    ]
    required_live = report["env"]["required_live"]
    live_env_ok = all(required_live.get(name) for name in REQUIRED_LIVE_ENV)
    checks.append(
        _check(
            "live_env_present",
            live_env_ok,
            ", ".join(name for name in REQUIRED_LIVE_ENV if not required_live.get(name))
            or "all required live env present",
            required=profile == "live",
        )
    )
    checks.append(
        _check(
            "live_data_dir_isolated",
            not report["paper_data_in_dir"],
            f"paper_data_in_dir={report['paper_data_in_dir']}",
            required=profile == "live",
        )
    )
    return checks


def readiness(
    data_dir: Path,
    *,
    profile: str = "submission",
    public_base: str = DEFAULT_PUBLIC_BASE,
    include_twak: bool = False,
    skip_public: bool = False,
    fetcher: Fetcher = _urlopen_fetch,
) -> dict:
    local, checks = _local_chain(data_dir)
    report = preflight(data_dir, include_twak=include_twak)
    public, public_checks = _public_checks(
        public_base, local, fetcher, skip_public=skip_public
    )
    checks.extend(public_checks)
    checks.extend(_preflight_checks(report, profile))

    gates = [
        "record and upload demo video",
        "submit DoraHacks BUIDL",
        "change scored-week stake or risk profile",
    ]
    required_checks = [c for c in checks if c["required"]]
    return {
        "ok": all(c["ok"] for c in required_checks),
        "profile": profile,
        "data_dir": str(data_dir),
        "local": local,
        "public": public,
        "preflight": report,
        "checks": checks,
        "approval_gated": gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("SOLVENT_DATA_DIR", "/opt/solvent/data")),
    )
    parser.add_argument(
        "--profile", choices=["submission", "live"], default="submission"
    )
    parser.add_argument("--public-base", default=DEFAULT_PUBLIC_BASE)
    parser.add_argument("--include-twak", action="store_true")
    parser.add_argument("--skip-public", action="store_true")
    args = parser.parse_args()

    if args.env_file:
        load_env_file(args.env_file)
        default_dir = Path("/opt/solvent/data")
        if args.data_dir == default_dir:
            args.data_dir = Path(os.environ.get("SOLVENT_DATA_DIR", default_dir))

    report = readiness(
        args.data_dir,
        profile=args.profile,
        public_base=args.public_base,
        include_twak=args.include_twak,
        skip_public=args.skip_public,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
