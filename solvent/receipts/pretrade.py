"""Optional on-chain pre-trade commitment publishing."""

import hashlib
import os

from .anchor import _build_registry


def pretrade_anchor_key(cycle_id: str, intent_key: str) -> str:
    digest = hashlib.sha256(intent_key.encode()).hexdigest()[:16]
    return f"solvent:pretrade:{cycle_id}:{digest}"


class PreTradeAnchorPublisher:
    def __init__(self, registry, agent_id: int) -> None:
        self.registry = registry
        self.agent_id = agent_id

    def publish(
        self, *, cycle_id: str, intent_key: str, commit_hash: str
    ) -> str | None:
        result = self.registry.set_metadata(
            self.agent_id, pretrade_anchor_key(cycle_id, intent_key), commit_hash
        )
        return result.get("transactionHash")


def build_pretrade_publisher():
    if os.environ.get("SOLVENT_PRETRADE_ANCHOR") != "1":
        return None
    agent_id = os.environ.get("SOLVENT_AGENT_ID")
    if not agent_id:
        raise SystemExit("SOLVENT_AGENT_ID is required for SOLVENT_PRETRADE_ANCHOR=1")
    network = os.environ.get("SOLVENT_BSC_NETWORK", "bsc-mainnet")
    return PreTradeAnchorPublisher(_build_registry(network), int(agent_id))
