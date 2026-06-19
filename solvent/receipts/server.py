"""Read-only public endpoint over the receipt log — the glass box's window.

Serves the hash-chained receipts and a live verification so anyone (a
judge, the dashboard) can pull the log and recompute the chain. No writes,
no auth, no secrets — it only ever reads the receipts file.

    python -m solvent.receipts.server --data-dir /opt/solvent/data --port 3078
"""

import argparse
import json
import logging
import os
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..commerce.signal import build_signal_payload
from ..kernel.rules import RiskConfig
from ..ops.watchdog import heartbeat_age
from .chain import verify_chain

logger = logging.getLogger(__name__)

# Cycle is hourly; treat the agent as alive within ~1.5 windows (matches the
# watchdog's staleness threshold).
ALIVE_MAX_AGE_S = 5400


def load_entries(path: Path) -> list[dict]:
    """Every receipt line as a {receipt, hash} dict, in chain order."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _live_holdings(position: dict | None) -> dict[str, float]:
    from ..exec.livebook import LiveBook, make_web3

    wallet = os.environ.get("SOLVENT_WALLET_ADDRESS")
    if not wallet:
        return {}
    network = os.environ.get("SOLVENT_TRADE_NETWORK", "bsc-mainnet")
    position_symbol = position.get("symbol") if position else None
    return LiveBook(make_web3(network), wallet).snapshot(position_symbol)


def _holdings(
    data_dir: Path,
    position: dict | None,
    live_reader: Callable[[dict | None], dict[str, float]],
) -> tuple[dict[str, float], str, str | None]:
    paper_path = data_dir / "paper-holdings.json"
    if paper_path.exists():
        return _read_json(paper_path, {}), "paper", None
    if os.environ.get("SOLVENT_MODE") != "live":
        return {}, "none", None
    try:
        return live_reader(position), "live", None
    except Exception as exc:
        return {}, "live-error", str(exc)[:200]


def state(
    data_dir: Path,
    *,
    live_reader: Callable[[dict | None], dict[str, float]] = _live_holdings,
) -> dict:
    """Live portfolio + liveness snapshot — the barbell's current shape.

    Reads the same files the cycle writes (state.json, paper-holdings.json,
    heartbeat); never recomputes equity (the latest receipt is authoritative
    for that). Purely read-only, no secrets.
    """
    st = _read_json(data_dir / "state.json", {})
    position = st.get("position")
    holdings, holdings_source, holdings_error = _holdings(
        data_dir, position, live_reader
    )
    age = heartbeat_age(data_dir)
    cfg = RiskConfig()
    anchors_raw = _read_json(data_dir / "anchors.json", {})
    anchors = sorted(
        (
            {
                "day": day,
                "head_hash": rec.get("head_hash"),
                "tx_hash": rec.get("tx_hash"),
            }
            for day, rec in anchors_raw.items()
        ),
        key=lambda a: a["day"],
        reverse=True,
    )
    agent_id = os.environ.get("SOLVENT_AGENT_ID")
    return {
        "start_equity_usd": st.get("start_equity_usd"),
        "peak_equity_usd": st.get("peak_equity_usd"),
        "position": position,
        "holdings": holdings,
        "holdings_source": holdings_source,
        "holdings_error": holdings_error,
        "heartbeat_age_s": age,
        "alive": age is not None and age < ALIVE_MAX_AGE_S,
        "anchors": anchors,
        "anchor_network": os.environ.get("SOLVENT_BSC_NETWORK", "bsc-testnet"),
        "agent_id": int(agent_id) if agent_id else None,
        "x402": {
            "session_budget_usd": cfg.x402_session_budget_usdc / 1e6,
            "max_per_call_usd": cfg.x402_max_per_call_usdc / 1e6,
        },
    }


def verify(path: Path) -> dict:
    if not path.exists():
        return {"ok": True, "count": 0, "head_hash": "0x" + "0" * 64}
    ok, count, head = verify_chain(path)
    return {"ok": ok, "count": count, "head_hash": head}


def summary(path: Path) -> dict:
    entries = load_entries(path)
    v = verify(path)
    latest = None
    for entry in reversed(entries):
        receipt = entry["receipt"]
        if receipt.get("phase", "cycle_summary") == "cycle_summary":
            latest = receipt
            break
    return {
        "agent": "SOLVENT",
        "receipts": v["count"],
        "chain_ok": v["ok"],
        "head_hash": v["head_hash"],
        "latest_equity_usd": latest["equity_usd"] if latest else None,
        "latest_regime": latest["regime"] if latest else None,
        "latest_ts": latest["ts"] if latest else None,
    }


def inference_proofs(path: Path) -> list[dict]:
    proofs = []
    for entry in load_entries(path):
        receipt = entry["receipt"]
        proof = receipt.get("inference_proof") or {}
        if proof:
            proofs.append(
                {
                    "seq": receipt["seq"],
                    "phase": receipt.get("phase", "cycle_summary"),
                    "cycle_id": receipt.get("cycle_id"),
                    "receipt_hash": entry["hash"],
                    "proof": proof,
                }
            )
    return proofs


class ReceiptHandler(BaseHTTPRequestHandler):
    data_dir: Path  # set on the class before serving

    @property
    def receipts_path(self) -> Path:
        return self.data_dir / "receipts.jsonl"

    def _send(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")  # read-only public data
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0].rstrip("/") or "/"
        if route == "/":
            self._send(summary(self.receipts_path))
        elif route == "/receipts":
            self._send(load_entries(self.receipts_path))
        elif route == "/verify":
            self._send(verify(self.receipts_path))
        elif route == "/state":
            self._send(state(self.data_dir))
        elif route == "/inference-proofs":
            self._send(inference_proofs(self.receipts_path))
        elif route == "/signal":
            try:
                self._send(build_signal_payload(self.data_dir))
            except ValueError as exc:
                self._send({"error": str(exc)}, status=404)
        else:
            self._send({"error": "not found"}, status=404)

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)


def serve(data_dir: Path, host: str, port: int) -> None:
    ReceiptHandler.data_dir = data_dir
    httpd = ThreadingHTTPServer((host, port), ReceiptHandler)
    logger.info("serving receipts from %s on %s:%d", data_dir, host, port)
    httpd.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3078)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    serve(args.data_dir, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
