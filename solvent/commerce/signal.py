"""ERC-8183-ready regime signal deliverables."""

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from ..brain.proof import canonical_json, sha256_json
from ..receipts.chain import verify_chain

SCHEMA = "solvent.erc8183.signal.v1"
SERVICE_ID = "solvent.daily-regime-signal"


def _load_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _latest_cycle(entries: list[dict]) -> tuple[dict, str]:
    for entry in reversed(entries):
        receipt = entry["receipt"]
        if receipt.get("phase", "cycle_summary") == "cycle_summary":
            return receipt, entry["hash"]
    raise ValueError("no cycle_summary receipt found")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _top_momentum(signals: dict, limit: int = 5) -> list[dict[str, Any]]:
    momentum = signals.get("momentum") or {}
    ranked = sorted(momentum.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"symbol": symbol, "score": score} for symbol, score in ranked]


def build_signal_payload(
    data_dir: Path,
    *,
    public_base_url: str = "https://solvent.gudman.xyz",
    now: datetime | None = None,
) -> dict[str, Any]:
    entries = _load_entries(data_dir / "receipts.jsonl")
    receipt, receipt_hash = _latest_cycle(entries)
    ok, count, head_hash = verify_chain(data_dir / "receipts.jsonl")
    anchors = _read_json(data_dir / "anchors.json", {})
    latest_anchor = None
    if anchors:
        day, anchor = sorted(anchors.items())[-1]
        latest_anchor = {
            "day": day,
            "head_hash": anchor.get("head_hash"),
            "tx_hash": anchor.get("tx_hash"),
        }

    signals = receipt.get("signals") or {}
    proof = receipt.get("inference_proof") or {}
    generated_at = now.isoformat() if now is not None else receipt.get("ts")
    payload = {
        "schema": SCHEMA,
        "service": SERVICE_ID,
        "generated_at": generated_at,
        "agent": {
            "name": "SOLVENT",
            "erc8004_agent_id": os.environ.get("SOLVENT_AGENT_ID"),
            "wallet": os.environ.get("SOLVENT_WALLET_ADDRESS"),
        },
        "source": {
            "cycle_id": receipt.get("cycle_id"),
            "receipt_seq": receipt.get("seq"),
            "receipt_hash": receipt_hash,
            "receipt_head_hash": head_hash,
            "receipt_count": count,
            "chain_ok": ok,
            "receipts_url": f"{public_base_url.rstrip('/')}/receipts",
            "verify_url": f"{public_base_url.rstrip('/')}/verify",
            "inference_commitments_url": f"{public_base_url.rstrip('/')}/inference-commitments",
            "latest_anchor": latest_anchor,
        },
        "signal": {
            "regime": receipt.get("regime"),
            "thesis": receipt.get("thesis"),
            "equity_usd": receipt.get("equity_usd"),
            "dq_headroom_pct": receipt.get("dq_headroom_pct"),
            "active_risk_profile": signals.get("active_risk_profile"),
            "fear_greed": signals.get("fear_greed"),
            "btc_funding_rate": signals.get("btc_funding_rate"),
            "degraded": signals.get("degraded"),
            "top_momentum": _top_momentum(signals),
            "advisor": signals.get("advisor"),
        },
        "proof": {
            "type": "hash_commitment",
            "inference_commitment_hash": proof.get("commitment_hash")
            or proof.get("proof_hash"),
            "inference_input_hash": proof.get("input_hash"),
            "inference_output_hash": proof.get("output_hash"),
        },
        "terms": {
            "license": "single-agent-read",
            "not_financial_advice": True,
            "buyer_must_verify_receipt_head": True,
        },
    }
    payload["signal_hash"] = sha256_json(payload)
    return payload


def build_job_response(data_dir: Path) -> tuple[str, dict[str, Any]]:
    payload = build_signal_payload(
        data_dir,
        public_base_url=os.environ.get(
            "SOLVENT_RECEIPTS_URL", "https://solvent.gudman.xyz"
        ),
    )
    metadata = {
        "service": SERVICE_ID,
        "schema": SCHEMA,
        "signal_hash": payload["signal_hash"],
        "receipt_hash": payload["source"]["receipt_hash"],
        "receipt_head_hash": payload["source"]["receipt_head_hash"],
        "inference_commitment_hash": payload["proof"]["inference_commitment_hash"],
    }
    return canonical_json(payload), metadata


async def submit_signal_job(data_dir: Path, job_id: int) -> dict[str, Any]:
    from bnbagent.erc8183.config import ERC8183Config
    from bnbagent.erc8183.server.job_ops import ERC8183JobOps
    from bnbagent.storage import LocalStorageProvider
    from bnbagent.wallets import EVMWalletProvider

    password = os.environ.get("SOLVENT_WALLET_PASSWORD") or os.environ.get(
        "WALLET_PASSWORD"
    )
    if not password:
        raise SystemExit("SOLVENT_WALLET_PASSWORD or WALLET_PASSWORD is required")
    wallet = EVMWalletProvider(
        password=password,
        private_key=os.environ.get("SOLVENT_PRIVATE_KEY")
        or os.environ.get("PRIVATE_KEY"),
        address=os.environ.get("SOLVENT_WALLET_ADDRESS")
        or os.environ.get("WALLET_ADDRESS")
        or None,
    )
    network = os.environ.get("SOLVENT_ERC8183_NETWORK") or os.environ.get(
        "SOLVENT_TRADE_NETWORK", "bsc-mainnet"
    )
    config = ERC8183Config(
        network=network,
        wallet_provider=wallet,
        storage=LocalStorageProvider(
            os.environ.get("SOLVENT_ERC8183_STORAGE_DIR") or str(data_dir / "erc8183")
        ),
        service_price=os.environ.get("SOLVENT_ERC8183_SERVICE_PRICE", "0"),
        agent_url=os.environ.get(
            "SOLVENT_ERC8183_AGENT_URL", "https://solvent.gudman.xyz/erc8183"
        ),
    )
    response, metadata = build_job_response(data_dir)
    ops = ERC8183JobOps(
        config.wallet_provider,
        network=config.effective_network,
        storage_provider=config.storage,
        service_price=int(config.service_price),
        agent_url=config.agent_url,
    )
    return await ops.submit_result(job_id, response, metadata)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--job-id", type=int)
    args = parser.parse_args()

    if args.job_id is not None:
        print(json.dumps(asyncio.run(submit_signal_job(args.data_dir, args.job_id))))
        return 0

    payload = build_signal_payload(args.data_dir)
    body = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body + "\n")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
