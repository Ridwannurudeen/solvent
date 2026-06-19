"""Append-only recovery for unresolved TWAK execution attempts.

This command never broadcasts. It only appends terminal journal records after
an operator verifies BSC wallet history for an existing ATTEMPTED entry.
"""

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from ..exec.executor import Journal
from ..exec.livebook import LiveBook, LiveReceiptVerifier
from ..exec.livebook import make_web3
from ..kernel.allocator import IntentKind, TradeIntent

TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


def _print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _validate_tx_hash(tx_hash: str | None, *, required: bool) -> str | None:
    if tx_hash is None:
        if required:
            raise SystemExit("--tx-hash 0x<64 hex chars> is required")
        return None
    if not TX_HASH_RE.fullmatch(tx_hash):
        raise SystemExit("--tx-hash must be 0x followed by 64 hex chars")
    return tx_hash


def _parse_mined_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit("--mined-at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SystemExit("--mined-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _intent_from_entry(entry: dict) -> TradeIntent:
    return TradeIntent(
        kind=IntentKind(entry["kind"]),
        from_symbol=entry["from"],
        to_symbol=entry["to"],
        notional_usd=float(entry["notional_usd"]),
        reason=entry.get("reason", "manual recovery"),
    )


def _verify_chain_receipt(
    tx_hash: str,
    *,
    wallet_address: str,
    network: str,
    entry: dict,
    slippage_pct: float,
) -> dict:
    w3 = make_web3(network)
    book = LiveBook(w3, wallet_address)
    verifier = LiveReceiptVerifier(book, slippage_pct=slippage_pct, timeout_s=1)
    try:
        proof = verifier(
            _intent_from_entry(entry),
            tx_hash,
            entry.get("pre_balances"),
        )
    except Exception as exc:
        raise SystemExit(str(exc)) from exc
    return {"network": network, **proof}


def _journal(data_dir: Path) -> Journal:
    return Journal(data_dir / "journal.jsonl")


def list_unresolved(data_dir: Path) -> int:
    _print({"unresolved": _journal(data_dir).pending_entries()})
    return 0


def mark_confirmed(
    data_dir: Path,
    *,
    key: str,
    tx_hash: str,
    mined_at: datetime | None,
    detail: str | None,
    wallet_address: str | None,
    network: str,
    skip_chain_check: bool,
    slippage_pct: float,
) -> int:
    valid_tx_hash = _validate_tx_hash(tx_hash, required=True)
    journal = _journal(data_dir)
    entry = journal.latest_entry(key)
    if entry is None:
        raise KeyError(key)
    chain_check = None
    if not skip_chain_check:
        if not wallet_address:
            raise SystemExit(
                "--wallet-address or SOLVENT_WALLET_ADDRESS is required "
                "unless --skip-chain-check is set"
            )
        chain_check = _verify_chain_receipt(
            valid_tx_hash,
            wallet_address=wallet_address,
            network=network,
            entry=entry,
            slippage_pct=slippage_pct,
        )
    entry = journal.resolve_attempt(
        key,
        ok=True,
        tx_hash=valid_tx_hash,
        detail=detail
        or (
            f"chain-verified transaction on {network}"
            if chain_check
            else "operator confirmed transaction on-chain without RPC check"
        ),
        ts=mined_at,
        verification=chain_check,
    )
    _print({"resolved": entry, "chain_check": chain_check})
    return 0


def mark_failed(
    data_dir: Path,
    *,
    key: str,
    reason: str,
    tx_hash: str | None,
) -> int:
    if not reason.strip():
        raise SystemExit("--reason is required")
    journal = _journal(data_dir)
    entry = journal.resolve_attempt(
        key,
        ok=False,
        tx_hash=_validate_tx_hash(tx_hash, required=False),
        detail=reason,
    )
    _print({"resolved": entry})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("SOLVENT_DATA_DIR", "/opt/solvent/data")),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-unresolved")

    confirmed = subparsers.add_parser("mark-confirmed")
    confirmed.add_argument("--key", required=True)
    confirmed.add_argument("--tx-hash", required=True)
    confirmed.add_argument("--mined-at")
    confirmed.add_argument("--detail")
    confirmed.add_argument(
        "--network", default=os.environ.get("SOLVENT_TRADE_NETWORK", "bsc-mainnet")
    )
    confirmed.add_argument("--slippage-pct", type=float, default=1.0)
    confirmed.add_argument(
        "--wallet-address", default=os.environ.get("SOLVENT_WALLET_ADDRESS")
    )
    confirmed.add_argument(
        "--skip-chain-check",
        action="store_true",
        help="operator override: do not query RPC before appending CONFIRMED",
    )

    failed = subparsers.add_parser("mark-failed")
    failed.add_argument("--key", required=True)
    failed.add_argument("--reason", required=True)
    failed.add_argument("--tx-hash")

    args = parser.parse_args(argv)
    try:
        if args.command == "list-unresolved":
            return list_unresolved(args.data_dir)
        if args.command == "mark-confirmed":
            return mark_confirmed(
                args.data_dir,
                key=args.key,
                tx_hash=args.tx_hash,
                mined_at=_parse_mined_at(args.mined_at),
                detail=args.detail,
                wallet_address=args.wallet_address,
                network=args.network,
                skip_chain_check=args.skip_chain_check,
                slippage_pct=args.slippage_pct,
            )
        return mark_failed(
            args.data_dir,
            key=args.key,
            reason=args.reason,
            tx_hash=args.tx_hash,
        )
    except KeyError as exc:
        raise SystemExit(f"unknown journal key: {exc.args[0]}") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
