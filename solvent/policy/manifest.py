"""Canonical policy manifest for SOLVENT's money-moving rules."""

import argparse
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct

from ..brain.advisor import MODEL
from ..brain.proof import sha256_json
from ..exec.networks import resolve_twak_chain
from ..kernel.allowlist import ADDRESSES, ALLOWED_SYMBOLS, FLOOR_SYMBOLS, SLEEVE_SYMBOLS
from ..kernel.rules import RiskConfig, risk_config_for_profile
from ..ops.files import atomic_write_text

SCHEMA = "solvent.policy-manifest.v1"
SIGNING_PREFIX = "SOLVENT policy manifest"
POLICY_ANCHOR_KEY = "solvent:policy-manifest"


def _git_commit() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _risk_config(cfg: RiskConfig) -> dict[str, Any]:
    payload = asdict(cfg)
    payload["ratchet"] = [asdict(tier) for tier in cfg.ratchet]
    return payload


def _executable_addresses(symbols: tuple[str, ...]) -> dict[str, str]:
    return {
        symbol: ADDRESSES[symbol] for symbol in sorted(symbols) if symbol in ADDRESSES
    }


def build_policy_manifest(
    *,
    profile: str,
    generated_at: datetime | None = None,
    git_commit: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = env or os.environ
    cfg = risk_config_for_profile(profile)
    generated = generated_at or datetime.now(timezone.utc)
    trade_network = env.get("SOLVENT_TRADE_NETWORK", "bsc-mainnet")
    twak_chain = resolve_twak_chain(trade_network, env.get("SOLVENT_TWAK_CHAIN"))
    manifest = {
        "schema": SCHEMA,
        "generated_at": generated.astimezone(timezone.utc).isoformat(),
        "code": {
            "git_commit": git_commit or _git_commit(),
            "python_package": "solvent",
        },
        "agent": {
            "name": "SOLVENT",
            "erc8004_agent_id": env.get("SOLVENT_AGENT_ID"),
            "wallet": env.get("SOLVENT_WALLET_ADDRESS"),
            "public_base_url": env.get(
                "SOLVENT_RECEIPTS_URL", "https://solvent.gudman.xyz"
            ),
        },
        "strategy": {
            "profile": profile,
            "risk_config": _risk_config(cfg),
            "advisor": {
                "enabled": env.get("SOLVENT_USE_ADVISOR") == "1",
                "model": MODEL,
                "authority": "de-risk-only",
            },
            "adaptive_profile_enabled": env.get("SOLVENT_ADAPTIVE_PROFILE") == "1",
        },
        "allowlist": {
            "eligible_symbol_count": len(ALLOWED_SYMBOLS),
            "floor_symbols": list(FLOOR_SYMBOLS),
            "sleeve_symbols": list(SLEEVE_SYMBOLS),
            "pinned_addresses": _executable_addresses(tuple(ADDRESSES)),
            "non_executable_allowlist_count": len(
                [symbol for symbol in ALLOWED_SYMBOLS if symbol not in ADDRESSES]
            ),
        },
        "data": {
            "primary_provider": "CoinMarketCap Agent Hub via x402",
            "secondary_provider": "Binance public REST price cross-check",
            "max_price_deviation_pct": float(
                env.get("SOLVENT_PRICE_DEVIATION_MAX_PCT", "5")
            ),
            "max_source_age_s": 3600,
            "x402_session_budget_usd": cfg.x402_session_budget_usdc / 1e6,
            "x402_max_per_call_usd": cfg.x402_max_per_call_usdc / 1e6,
        },
        "execution": {
            "chain": trade_network,
            "twak_chain": twak_chain,
            "executor": "Trust Wallet Agent Kit",
            "one_transaction_per_intent": True,
            "settlement_verification": [
                "successful transaction receipt",
                "wallet sender match",
                "ERC-20 Transfer logs in expected direction",
                "post-trade balance deltas in expected direction",
            ],
            "result_outcomes": [
                "executed_now",
                "already_confirmed",
                "unresolved",
                "failed",
            ],
        },
        "verification": {
            "receipt_chain": "sha256 hash chain",
            "daily_erc8004_anchor": True,
            "pre_trade_anchor_required": env.get("SOLVENT_PRETRADE_ANCHOR") == "1",
            "inference_packet": "hash commitment; not TEE attestation",
        },
        "emergency": {
            "kill_switch_drawdown_pct": cfg.kill_switch_drawdown_pct,
            "dq_drawdown_pct": cfg.dq_drawdown_pct,
            "persistent_runtime_halt": True,
            "resume_requires_clear_journal": True,
            "unresolved_execution_policy": "halt value-moving sends until resolved",
        },
    }
    return {"manifest": manifest, "manifest_hash": sha256_json(manifest)}


def sign_policy_manifest(manifest_hash: str, private_key: str) -> dict[str, str]:
    message = f"{SIGNING_PREFIX}\n{manifest_hash}"
    signed = Account.sign_message(encode_defunct(text=message), private_key=private_key)
    return {
        "scheme": "eip191",
        "message": message,
        "signer": Account.from_key(private_key).address,
        "signature": signed.signature.hex(),
    }


def anchor_policy_manifest(registry, agent_id: int, manifest_hash: str) -> dict:
    result = registry.set_metadata(agent_id, POLICY_ANCHOR_KEY, manifest_hash)
    return {
        "key": POLICY_ANCHOR_KEY,
        "value": manifest_hash,
        "tx_hash": result.get("transactionHash"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", default=os.environ.get("SOLVENT_RISK_PROFILE", "safety")
    )
    parser.add_argument("--git-commit")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--sign-env",
        action="store_true",
        help="sign with SOLVENT_PRIVATE_KEY from the environment",
    )
    parser.add_argument(
        "--anchor",
        action="store_true",
        help="anchor the manifest hash under SOLVENT_AGENT_ID via ERC-8004 metadata",
    )
    args = parser.parse_args(argv)

    payload = build_policy_manifest(
        profile=args.profile,
        git_commit=args.git_commit,
    )
    if args.sign_env:
        private_key = os.environ.get("SOLVENT_PRIVATE_KEY")
        if not private_key:
            raise SystemExit("SOLVENT_PRIVATE_KEY is required for --sign-env")
        payload["signature"] = sign_policy_manifest(
            payload["manifest_hash"], private_key
        )
    if args.anchor:
        from ..receipts.anchor import _build_registry

        agent_id = os.environ.get("SOLVENT_AGENT_ID")
        if not agent_id:
            raise SystemExit("SOLVENT_AGENT_ID is required for --anchor")
        network = os.environ.get("SOLVENT_BSC_NETWORK", "bsc-mainnet")
        payload["anchor"] = anchor_policy_manifest(
            _build_registry(network), int(agent_id), payload["manifest_hash"]
        )
    body = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        atomic_write_text(args.out, body + "\n")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
