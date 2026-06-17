"""Trade execution — TWAK is the sole path to the chain.

Invariant (hard rule): ONE transaction per intent, ever. The journal
records an intent BEFORE the swap is attempted; an attempt whose outcome
is unknown (timeout, crash mid-call) leaves the journal entry in
ATTEMPTED state, and the executor refuses to re-send that intent until
the outcome is resolved from chain history. We never blind-retry a
value-moving call.
"""

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..kernel.allocator import TradeIntent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionResult:
    intent_key: str
    ok: bool
    tx_hash: str | None
    detail: str


def intent_key(intent: TradeIntent, cycle_id: str) -> str:
    """Stable identity for an intent within a cycle."""
    return (
        f"{cycle_id}:{intent.kind.value}:{intent.from_symbol}->"
        f"{intent.to_symbol}:{intent.notional_usd:.2f}"
    )


class Journal:
    """Append-only JSONL execution journal (also drives trades_today /
    qualified_today in PortfolioState)."""

    PENDING = "ATTEMPTED"

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    e = json.loads(line)
                    self._entries[e["key"]] = e

    def _write(self, entry: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
        self._entries[entry["key"]] = entry

    def state_of(self, key: str) -> str | None:
        e = self._entries.get(key)
        return e["state"] if e else None

    def mark_attempted(self, key: str, intent: TradeIntent) -> None:
        self._write(
            {
                "key": key,
                "state": self.PENDING,
                "ts": datetime.now(timezone.utc).isoformat(),
                "kind": intent.kind.value,
                "from": intent.from_symbol,
                "to": intent.to_symbol,
                "notional_usd": intent.notional_usd,
                "reason": intent.reason,
            }
        )

    def mark_result(self, key: str, ok: bool, tx_hash: str | None, detail: str) -> None:
        self._write(
            {
                "key": key,
                "state": "CONFIRMED" if ok else "FAILED",
                "ts": datetime.now(timezone.utc).isoformat(),
                "tx_hash": tx_hash,
                "detail": detail[:500],
            }
        )

    def confirmed_trades_on(self, day_utc: str) -> int:
        return sum(
            1
            for e in self._entries.values()
            if e["state"] == "CONFIRMED" and e["ts"][:10] == day_utc
        )

    def has_unresolved(self) -> bool:
        return any(e["state"] == self.PENDING for e in self._entries.values())


class TwakExecutor:
    """Executes intents through the TWAK CLI on BSC."""

    def __init__(
        self,
        journal: Journal,
        password: str | None = None,
        twak_bin: str = "twak",
        chain: str = "bsc",
        slippage_pct: float = 1.0,
        timeout_s: int = 180,
    ) -> None:
        self.journal = journal
        self._password = password
        self.twak_bin = twak_bin
        self.chain = chain
        self.slippage_pct = slippage_pct
        self.timeout_s = timeout_s

    def execute(self, intent: TradeIntent, cycle_id: str) -> ExecutionResult:
        key = intent_key(intent, cycle_id)
        prior = self.journal.state_of(key)
        if prior == "CONFIRMED":
            return ExecutionResult(key, True, None, "already confirmed; skipped")
        if prior == Journal.PENDING:
            return ExecutionResult(
                key, False, None, "prior attempt unresolved; refusing to re-send"
            )
        if self.journal.has_unresolved():
            return ExecutionResult(
                key, False, None, "journal has unresolved attempts; trading halted"
            )

        self.journal.mark_attempted(key, intent)
        cmd = [
            self.twak_bin,
            "swap",
            intent.from_symbol,
            intent.to_symbol,
            "--usd",
            f"{intent.notional_usd:.2f}",
            "--chain",
            self.chain,
            "--slippage",
            str(self.slippage_pct),
            "--json",
        ]
        # Password via env, never argv — argv is world-readable in /proc
        # on the shared host.
        env = {**os.environ}
        if self._password:
            env["TWAK_WALLET_PASSWORD"] = self._password
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout_s, env=env
            )
        except subprocess.TimeoutExpired:
            logger.error("swap timed out; outcome UNKNOWN — halting further sends")
            return ExecutionResult(key, False, None, "timeout: outcome unknown")

        out = proc.stdout.strip() or proc.stderr.strip()
        tx_hash = None
        try:
            payload = json.loads(out)
            tx_hash = (
                payload.get("txHash")
                or payload.get("transactionHash")
                or payload.get("hash")
            )
        except ValueError:
            pass
        ok = proc.returncode == 0 and tx_hash is not None
        if not ok and proc.returncode != 0:
            logger.error(
                "twak exited nonzero after attempt; outcome UNKNOWN — halting further sends"
            )
            return ExecutionResult(key, False, tx_hash, out[:200])
        self.journal.mark_result(key, ok, tx_hash, out)
        return ExecutionResult(key, ok, tx_hash, out[:200])


class PaperExecutor:
    """Paper-mode executor — fills instantly at signal price, no chain."""

    def __init__(self, journal: Journal) -> None:
        self.journal = journal

    def execute(self, intent: TradeIntent, cycle_id: str) -> ExecutionResult:
        key = intent_key(intent, cycle_id)
        if self.journal.state_of(key) == "CONFIRMED":
            return ExecutionResult(key, True, None, "already confirmed; skipped")
        self.journal.mark_attempted(key, intent)
        self.journal.mark_result(key, True, f"paper-{key[-8:]}", "paper fill")
        return ExecutionResult(key, True, f"paper-{key[-8:]}", "paper fill")
