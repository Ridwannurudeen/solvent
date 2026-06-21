"""SOLVENT operational preflight.

Reports non-secret readiness signals before live mode: env presence,
configured directories, TWAK public status, wallet balances, journal state,
heartbeat freshness, and anchor freshness. It never prints private keys,
passwords, API secrets, or raw env values.
"""

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..exec.executor import Journal
from ..exec.networks import resolve_twak_chain
from .watchdog import heartbeat_age

SECRET_ENV = (
    "SOLVENT_PRIVATE_KEY",
    "SOLVENT_WALLET_PASSWORD",
    "TWAK_ACCESS_ID",
    "TWAK_HMAC_SECRET",
    "TWAK_WALLET_PASSWORD",
)
REQUIRED_LIVE_ENV = (
    "SOLVENT_PRIVATE_KEY",
    "SOLVENT_WALLET_PASSWORD",
    "SOLVENT_WALLET_ADDRESS",
    "SOLVENT_TRADE_NETWORK",
)
# The live execution path signs through the TWAK CLI. These env vars are one
# valid credential path; TWAK's local auth file + keychain wallet is another.
REQUIRED_LIVE_TWAK_ENV = (
    "TWAK_ACCESS_ID",
    "TWAK_HMAC_SECRET",
    "TWAK_WALLET_PASSWORD",
)


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"env file not found: {path}")
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _run(cmd: list[str], timeout: int = 30) -> dict:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    out = proc.stdout.strip() or proc.stderr.strip()
    payload = None
    if out:
        try:
            payload = json.loads(out)
        except ValueError:
            payload = out[:500]
    return {"ok": proc.returncode == 0, "result": payload}


def _env_status() -> dict:
    trade_network = os.environ.get("SOLVENT_TRADE_NETWORK", "bsc-mainnet")
    configured_twak_chain = os.environ.get("SOLVENT_TWAK_CHAIN")
    try:
        effective_twak_chain = resolve_twak_chain(trade_network, configured_twak_chain)
        twak_chain_ok = True
        twak_chain_error = None
    except ValueError as exc:
        effective_twak_chain = None
        twak_chain_ok = False
        twak_chain_error = str(exc)
    return {
        "mode": os.environ.get("SOLVENT_MODE", "paper"),
        "required_live": {
            name: bool(os.environ.get(name)) for name in REQUIRED_LIVE_ENV
        },
        "twak_chain": {
            "configured": bool(configured_twak_chain),
            "effective": effective_twak_chain,
            "matches_trade_network": twak_chain_ok,
            "error": twak_chain_error,
        },
        "required_live_twak": {
            name: bool(os.environ.get(name)) for name in REQUIRED_LIVE_TWAK_ENV
        },
        "secrets_present": {name: bool(os.environ.get(name)) for name in SECRET_ENV},
        "data_dir": os.environ.get("SOLVENT_DATA_DIR", "/opt/solvent/data"),
    }


def _anchors(data_dir: Path) -> dict:
    path = data_dir / "anchors.json"
    if not path.exists():
        return {"count": 0, "latest": None}
    data = json.loads(path.read_text())
    if not data:
        return {"count": 0, "latest": None}
    day = sorted(data)[-1]
    return {"count": len(data), "latest": {"day": day, **data[day]}}


def preflight(data_dir: Path, include_twak: bool = True) -> dict:
    journal = Journal(data_dir / "journal.jsonl")
    age = heartbeat_age(data_dir)
    env = _env_status()
    report = {
        "env": env,
        "paper_data_in_dir": (data_dir / "paper-holdings.json").exists(),
        "heartbeat_age_s": age,
        "journal_has_unresolved": journal.has_unresolved(),
        "confirmed_today": journal.confirmed_trades_on(
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ),
        "anchors": _anchors(data_dir),
    }
    if include_twak:
        twak_chain = env["twak_chain"]["effective"] or "bsc"
        report["twak"] = {
            "auth_status": _run(["twak", "auth", "status", "--json"]),
            "wallet_address": _run(
                ["twak", "wallet", "address", "--chain", twak_chain, "--json"]
            ),
            "wallet_balance": _run(
                ["twak", "wallet", "balance", "--chain", twak_chain, "--json"]
            ),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("SOLVENT_DATA_DIR", "/opt/solvent/data")),
    )
    parser.add_argument("--skip-twak", action="store_true")
    args = parser.parse_args()
    if args.env_file:
        load_env_file(args.env_file)
        default_dir = Path("/opt/solvent/data")
        if args.data_dir == default_dir:
            args.data_dir = Path(os.environ.get("SOLVENT_DATA_DIR", default_dir))
    print(
        json.dumps(preflight(args.data_dir, include_twak=not args.skip_twak), indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
