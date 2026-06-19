"""Daily on-chain anchor for the receipt chain — under SOLVENT's identity.

Once a day this posts the receipt chain's head hash as ERC-8004 metadata
under the agent's registered identity (one cheap BSC tx). Because every
receipt commits to the previous one, anchoring just the head makes the
whole day's reasoning tamper-evident: anyone can pull the public log,
recompute the chain (solvent.receipts.chain.verify_chain), and check the
head against the on-chain anchor.

The on-chain write needs a funded wallet + a registered agent ID, so the
CLI is gated on credentials (env), exactly like live trading. The anchor
logic itself is pure and unit-tested against a fake registry.

    python -m solvent.receipts.anchor --data-dir /opt/solvent/data            # daily anchor
    python -m solvent.receipts.anchor --data-dir /opt/solvent/data --register # one-time identity
"""

import argparse
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..ops.alerts import alert
from .chain import GENESIS_HASH, ReceiptChain

logger = logging.getLogger(__name__)


def anchor_key(day_utc: str) -> str:
    """Metadata key the day's chain head is anchored under."""
    return f"solvent:anchor:{day_utc}"


def _pick(payload: dict, *names: str):
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    for value in payload.values():
        if isinstance(value, dict):
            picked = _pick(value, *names)
            if picked is not None:
                return picked
    return None


def _tx_hash(payload: dict) -> str | None:
    return _pick(payload, "transactionHash", "txHash", "hash")


def _agent_id(payload: dict) -> int:
    value = _pick(payload, "agentId", "agent_id", "tokenId", "id")
    if value is None:
        raise RuntimeError(
            f"TWAK register output did not include an agent id: {payload}"
        )
    return int(value)


def _twak_chain(network: str) -> str:
    return {
        "bsc": "bsc",
        "bsc-mainnet": "bsc",
        "bsctestnet": "bsctestnet",
        "bsc-testnet": "bsctestnet",
    }.get(network, network)


class TwakRegistry:
    """ERC-8004 registry adapter backed by the TWAK CLI/keychain."""

    def __init__(
        self, chain: str, twak_bin: str = "twak", runner=subprocess.run
    ) -> None:
        self.chain = _twak_chain(chain)
        self.twak_bin = twak_bin
        self._runner = runner

    def _json(self, cmd: list[str]) -> dict:
        proc = self._runner(cmd, capture_output=True, text=True, timeout=180)
        out = proc.stdout.strip()
        if proc.returncode != 0:
            detail = proc.stderr.strip() or out
            raise RuntimeError(detail)
        return json.loads(out)

    def register_agent(self, agent_uri: str) -> dict:
        payload = self._json(
            [
                self.twak_bin,
                "erc8004",
                "register",
                "--uri",
                agent_uri,
                "--chain",
                self.chain,
                "--json",
            ]
        )
        return {
            **payload,
            "agentId": _agent_id(payload),
            "transactionHash": _tx_hash(payload),
        }

    def set_metadata(self, agent_id: int, key: str, value: str) -> dict:
        payload = self._json(
            [
                self.twak_bin,
                "erc8004",
                "set-metadata",
                str(agent_id),
                "--key",
                key,
                "--value",
                value,
                "--chain",
                self.chain,
                "--json",
            ]
        )
        return {**payload, "transactionHash": _tx_hash(payload)}


class AnchorMarkers:
    """Local record of which UTC days have been anchored — makes the
    daily anchor idempotent without an extra on-chain read each run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.days: dict[str, dict] = {}
        if path.exists():
            self.days = json.loads(path.read_text())

    def has(self, day: str) -> bool:
        return day in self.days

    def get(self, day: str) -> dict | None:
        return self.days.get(day)

    def record(self, day: str, head_hash: str, tx_hash: str) -> None:
        self.days[day] = {"head_hash": head_hash, "tx_hash": tx_hash}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.days, indent=1))


def run_anchor(
    *,
    chain: ReceiptChain,
    registry,
    agent_id: int,
    markers: AnchorMarkers,
    now: datetime | None = None,
    update_existing: bool = False,
) -> dict:
    """Post today's chain head on-chain if not already anchored.

    `registry` is anything exposing set_metadata(agent_id, key, value) ->
    {"transactionHash": str} (the ERC8004Agent surface).
    """
    now = now or datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")

    head = chain.head_hash
    if head == GENESIS_HASH:
        return {"action": "none", "reason": "no receipts to anchor", "day": day}
    existing = markers.get(day)
    if existing:
        if existing.get("head_hash") == head:
            return {"action": "none", "reason": "already anchored today", "day": day}
        if not update_existing:
            return {
                "action": "none",
                "reason": "already anchored today with older head",
                "day": day,
                "anchored_head_hash": existing.get("head_hash"),
                "current_head_hash": head,
            }

    result = registry.set_metadata(agent_id, anchor_key(day), head)
    tx_hash = result.get("transactionHash")
    markers.record(day, head, tx_hash)
    action = "update_anchor" if existing else "anchor"
    return {"action": action, "tx_hash": tx_hash, "head_hash": head, "day": day}


def _build_registry(network: str):
    """Construct the live ERC8004Agent from env credentials."""
    if os.environ.get("SOLVENT_ANCHOR_BACKEND") == "twak":
        return TwakRegistry(network, twak_bin=os.environ.get("TWAK_BIN", "twak"))

    from bnbagent import ERC8004Agent, EVMWalletProvider

    password = os.environ.get("SOLVENT_WALLET_PASSWORD")
    if not password:
        raise SystemExit("SOLVENT_WALLET_PASSWORD is required to sign on-chain")
    wallet = EVMWalletProvider(
        password=password, private_key=os.environ.get("SOLVENT_PRIVATE_KEY")
    )
    return ERC8004Agent(wallet_provider=wallet, network=network)


def _register(registry) -> int:
    """One-time: register SOLVENT's ERC-8004 identity. Returns the agent ID."""
    if not hasattr(registry, "generate_agent_uri"):
        endpoint = os.environ.get("SOLVENT_RECEIPTS_URL", "https://solvent.gudman.xyz")
        result = registry.register_agent(agent_uri=endpoint)
        agent_id = result["agentId"]
        logger.info(
            "registered agent id=%s tx=%s", agent_id, result.get("transactionHash")
        )
        return agent_id

    from bnbagent import AgentEndpoint

    endpoint = os.environ.get("SOLVENT_RECEIPTS_URL", "https://solvent.gudman.xyz")
    uri = registry.generate_agent_uri(
        name="SOLVENT",
        description="Glass-box autonomous BSC trading agent — anchors decision receipts.",
        endpoints=[AgentEndpoint(name="receipts", endpoint=endpoint, version="0.3.0")],
    )
    result = registry.register_agent(agent_uri=uri)
    agent_id = result["agentId"]
    logger.info("registered agent id=%s tx=%s", agent_id, result.get("transactionHash"))
    return agent_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument(
        "--register",
        action="store_true",
        help="one-time: register the ERC-8004 identity and print the agent ID",
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="re-anchor today if receipts were appended after an earlier anchor",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    network = os.environ.get("SOLVENT_BSC_NETWORK", "bsc-testnet")
    registry = _build_registry(network)

    if args.register:
        agent_id = _register(registry)
        print(f"SOLVENT_AGENT_ID={agent_id}")
        return 0

    agent_id_env = os.environ.get("SOLVENT_AGENT_ID")
    if not agent_id_env:
        raise SystemExit("SOLVENT_AGENT_ID is required (run with --register first)")

    chain = ReceiptChain(args.data_dir / "receipts.jsonl")
    markers = AnchorMarkers(args.data_dir / "anchors.json")
    summary = run_anchor(
        chain=chain,
        registry=registry,
        agent_id=int(agent_id_env),
        markers=markers,
        update_existing=args.update_existing,
    )
    logger.info("anchor: %s", summary)
    if summary["action"] in {"anchor", "update_anchor"}:
        alert(f"SOLVENT ANCHOR [{network}] {json.dumps(summary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
