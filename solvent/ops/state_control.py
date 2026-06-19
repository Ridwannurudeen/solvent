"""Operator controls for the persistent runtime halt latch."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..engine import StateStore
from ..exec.executor import Journal


def _print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load(data_dir: Path) -> StateStore:
    return StateStore.load(data_dir / "state.json")


def status(data_dir: Path) -> int:
    store = _load(data_dir)
    _print(
        {
            "runtime_status": store.runtime_status,
            "halted_at": store.halted_at,
            "halt_reason": store.halt_reason,
            "position": store.position,
            "journal_has_unresolved": Journal(
                data_dir / "journal.jsonl"
            ).has_unresolved(),
        }
    )
    return 0


def halt(data_dir: Path, *, reason: str) -> int:
    if not reason.strip():
        raise SystemExit("--reason is required")
    store = _load(data_dir)
    store.latch_halt(now=datetime.now(timezone.utc), reason=reason)
    store.save()
    _print(
        {
            "runtime_status": store.runtime_status,
            "halted_at": store.halted_at,
            "halt_reason": store.halt_reason,
        }
    )
    return 0


def resume(data_dir: Path, *, reason: str) -> int:
    if not reason.strip():
        raise SystemExit("--reason is required")
    journal = Journal(data_dir / "journal.jsonl")
    if journal.has_unresolved():
        raise SystemExit("cannot resume while journal has unresolved attempts")
    store = _load(data_dir)
    store.runtime_status = "ACTIVE"
    store.halted_at = None
    store.halt_reason = f"resumed: {reason}"
    store.save()
    _print(
        {
            "runtime_status": store.runtime_status,
            "halt_reason": store.halt_reason,
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("SOLVENT_DATA_DIR", "/opt/solvent/data")),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    halt_parser = subparsers.add_parser("halt")
    halt_parser.add_argument("--reason", required=True)
    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("--reason", required=True)

    args = parser.parse_args(argv)
    if args.command == "status":
        return status(args.data_dir)
    if args.command == "halt":
        return halt(args.data_dir, reason=args.reason)
    return resume(args.data_dir, reason=args.reason)


if __name__ == "__main__":
    raise SystemExit(main())
