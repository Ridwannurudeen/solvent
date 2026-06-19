"""Decision receipts — the glass box.

Every decision cycle emits one receipt: the data the agent bought (and
what it cost), the signals it saw, the regime it inferred, the intents
it produced, and the transactions that resulted. Receipts form a hash
chain (each commits to the previous receipt's hash), and the chain head
is anchored on-chain daily under the agent's ERC-8004 identity — so the
log is tamper-evident and publicly verifiable after the fact.
"""

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

GENESIS_HASH = "0x" + "0" * 64


@dataclass(frozen=True)
class DataPurchase:
    tool: str
    cost_usdc: float
    ok: bool
    response_hash: str | None = None
    response_bytes: int = 0


@dataclass(frozen=True)
class Receipt:
    seq: int
    prev_hash: str
    ts: str  # ISO-8601 UTC
    phase: str = "cycle_summary"
    cycle_id: str = ""
    intent_key: str | None = None
    pre_trade_hash: str | None = None
    execution_seal: dict = field(default_factory=dict)
    data_purchases: list[DataPurchase] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    inference_proof: dict = field(default_factory=dict)
    regime: str = ""
    thesis: str = ""
    intents: list[dict] = field(default_factory=list)
    executions: list[dict] = field(default_factory=list)  # incl. tx hashes
    equity_usd: float = 0.0
    dq_headroom_pct: float = 0.0

    def canonical(self) -> str:
        """Deterministic JSON for hashing (sorted keys, no whitespace)."""
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @property
    def hash(self) -> str:
        return "0x" + hashlib.sha256(self.canonical().encode()).hexdigest()


class ReceiptChain:
    """Append-only JSONL store. Line N holds receipt seq N and embeds the
    hash of line N-1, so any mutation breaks every subsequent hash."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._head_hash = GENESIS_HASH
        self._next_seq = 0
        if path.exists():
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                self._head_hash = rec["hash"]
                self._next_seq = rec["receipt"]["seq"] + 1

    @property
    def head_hash(self) -> str:
        return self._head_hash

    @property
    def next_seq(self) -> int:
        return self._next_seq

    def append(self, **fields) -> Receipt:
        receipt = Receipt(seq=self._next_seq, prev_hash=self._head_hash, **fields)
        entry = {"receipt": asdict(receipt), "hash": receipt.hash}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._head_hash = receipt.hash
        self._next_seq += 1
        return receipt


def verify_chain(path: Path) -> tuple[bool, int, str]:
    """Recompute the whole chain. Returns (ok, count, head_hash).

    Public auditors run this against the published log and compare
    head_hash with the on-chain anchors.
    """
    prev = GENESIS_HASH
    count = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        rec = entry["receipt"]
        if rec["prev_hash"] != prev:
            return False, count, prev
        canonical = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        if "0x" + hashlib.sha256(canonical.encode()).hexdigest() != entry["hash"]:
            return False, count, prev
        prev = entry["hash"]
        count += 1
    return True, count, prev
