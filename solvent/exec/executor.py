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
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..kernel.allocator import TradeIntent

logger = logging.getLogger(__name__)


_TX_HASH_RE = re.compile(r"0x[a-fA-F0-9]{64}")


@dataclass(frozen=True)
class ExecutionResult:
    intent_key: str
    ok: bool
    tx_hash: str | None
    detail: str
    outcome: str = ""
    verification: dict | None = None

    def __post_init__(self) -> None:
        if not self.outcome:
            object.__setattr__(self, "outcome", "executed_now" if self.ok else "failed")

    @property
    def applies_state_change(self) -> bool:
        return self.outcome == "executed_now"


def _extract_tx_hash(output: str) -> str | None:
    try:
        payload = json.loads(output)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        tx_hash = (
            payload.get("txHash")
            or payload.get("transactionHash")
            or payload.get("hash")
        )
        if isinstance(tx_hash, str):
            return tx_hash
    match = _TX_HASH_RE.search(output)
    return match.group(0) if match else None


def intent_key(intent: TradeIntent, cycle_id: str) -> str:
    """Stable identity for an intent within a cycle."""
    return (
        f"{cycle_id}:{intent.kind.value}:{intent.from_symbol}->"
        f"{intent.to_symbol}:{intent.notional_usd:.2f}"
    )


def intent_payload(intent: TradeIntent, cycle_id: str) -> dict:
    """Canonical intent payload committed before execution."""
    return {
        "key": intent_key(intent, cycle_id),
        "cycle_id": cycle_id,
        "kind": intent.kind.value,
        "from": intent.from_symbol,
        "to": intent.to_symbol,
        "notional_usd": round(intent.notional_usd, 2),
        "reason": intent.reason,
    }


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
            f.flush()
            os.fsync(f.fileno())
        self._entries[entry["key"]] = entry

    def state_of(self, key: str) -> str | None:
        e = self._entries.get(key)
        return e["state"] if e else None

    def latest_entry(self, key: str) -> dict | None:
        e = self._entries.get(key)
        return dict(e) if e else None

    def pending_entries(self) -> list[dict]:
        return [dict(e) for e in self._entries.values() if e["state"] == self.PENDING]

    def mark_attempted(
        self,
        key: str,
        intent: TradeIntent,
        *,
        pre_balances: dict[str, float] | None = None,
    ) -> None:
        entry = {
            "key": key,
            "state": self.PENDING,
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": intent.kind.value,
            "from": intent.from_symbol,
            "to": intent.to_symbol,
            "notional_usd": intent.notional_usd,
            "reason": intent.reason,
        }
        if pre_balances is not None:
            entry["pre_balances"] = pre_balances
        self._write(entry)

    def mark_result(
        self,
        key: str,
        ok: bool,
        tx_hash: str | None,
        detail: str,
        ts: datetime | None = None,
        verification: dict | None = None,
    ) -> None:
        entry = {
            "key": key,
            "state": "CONFIRMED" if ok else "FAILED",
            "ts": (ts or datetime.now(timezone.utc)).isoformat(),
            "tx_hash": tx_hash,
            "detail": detail[:500],
        }
        if verification is not None:
            entry["verification"] = verification
        self._write(entry)

    def resolve_attempt(
        self,
        key: str,
        *,
        ok: bool,
        tx_hash: str | None,
        detail: str,
        ts: datetime | None = None,
        verification: dict | None = None,
    ) -> dict:
        state = self.state_of(key)
        if state is None:
            raise KeyError(key)
        if state != self.PENDING:
            raise ValueError(f"{key} is {state}, not {self.PENDING}")
        if ok and not tx_hash:
            raise ValueError("confirmed attempts require a tx_hash")
        self.mark_result(
            key,
            ok=ok,
            tx_hash=tx_hash,
            detail=detail,
            ts=ts,
            verification=verification,
        )
        entry = self.latest_entry(key)
        assert entry is not None
        return entry

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
        receipt_verifier: Callable[[TradeIntent, str, dict[str, float] | None], dict]
        | None = None,
        balance_reader: Callable[[TradeIntent], dict[str, float]] | None = None,
    ) -> None:
        self.journal = journal
        self._password = password
        self.twak_bin = twak_bin
        self.chain = chain
        self.slippage_pct = slippage_pct
        self.timeout_s = timeout_s
        self.receipt_verifier = receipt_verifier
        self.balance_reader = balance_reader

    def execute(self, intent: TradeIntent, cycle_id: str) -> ExecutionResult:
        key = intent_key(intent, cycle_id)
        prior = self.journal.state_of(key)
        if prior == "CONFIRMED":
            entry = self.journal.latest_entry(key) or {}
            return ExecutionResult(
                key,
                True,
                entry.get("tx_hash"),
                "already confirmed; skipped",
                "already_confirmed",
            )
        if prior == Journal.PENDING:
            return ExecutionResult(
                key,
                False,
                None,
                "prior attempt unresolved; refusing to re-send",
                "unresolved",
            )
        if prior == "FAILED":
            return ExecutionResult(
                key,
                False,
                None,
                "prior attempt failed; refusing to re-send same key",
                "failed",
            )
        if self.journal.has_unresolved():
            return ExecutionResult(
                key,
                False,
                None,
                "journal has unresolved attempts; trading halted",
                "unresolved",
            )

        try:
            pre_balances = self.balance_reader(intent) if self.balance_reader else None
        except Exception as exc:
            logger.error("pre-trade balance snapshot failed; refusing to broadcast")
            return ExecutionResult(
                key, False, None, str(exc)[:200], "failed", verification=None
            )

        self.journal.mark_attempted(key, intent, pre_balances=pre_balances)
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
            return ExecutionResult(
                key, False, None, "timeout: outcome unknown", "unresolved"
            )

        out = proc.stdout.strip() or proc.stderr.strip()
        tx_hash = _extract_tx_hash(out)
        ok = proc.returncode == 0 and tx_hash is not None
        if not ok:
            logger.error("twak attempt outcome UNKNOWN - halting further sends")
            return ExecutionResult(key, False, tx_hash, out[:200], "unresolved")
        verification = None
        if self.receipt_verifier is not None:
            try:
                verification = self.receipt_verifier(intent, tx_hash, pre_balances)
            except Exception as exc:
                logger.error("tx receipt verification failed; outcome UNKNOWN")
                return ExecutionResult(
                    key, False, tx_hash, str(exc)[:200], "unresolved"
                )
        self.journal.mark_result(key, ok, tx_hash, out, verification=verification)
        return ExecutionResult(
            key, ok, tx_hash, out[:200], "executed_now", verification=verification
        )


class PaperExecutor:
    """Paper-mode executor — fills instantly at signal price, no chain."""

    def __init__(self, journal: Journal) -> None:
        self.journal = journal

    def execute(self, intent: TradeIntent, cycle_id: str) -> ExecutionResult:
        key = intent_key(intent, cycle_id)
        if self.journal.state_of(key) == "CONFIRMED":
            entry = self.journal.latest_entry(key) or {}
            return ExecutionResult(
                key,
                True,
                entry.get("tx_hash"),
                "already confirmed; skipped",
                "already_confirmed",
            )
        self.journal.mark_attempted(key, intent)
        self.journal.mark_result(key, True, f"paper-{key[-8:]}", "paper fill")
        return ExecutionResult(
            key, True, f"paper-{key[-8:]}", "paper fill", "executed_now"
        )
