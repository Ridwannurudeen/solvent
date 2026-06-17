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
) -> int:
    journal = _journal(data_dir)
    entry = journal.resolve_attempt(
        key,
        ok=True,
        tx_hash=_validate_tx_hash(tx_hash, required=True),
        detail=detail or "operator confirmed transaction on-chain",
        ts=mined_at,
    )
    _print({"resolved": entry})
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
