import json

import pytest

from solvent.exec.executor import Journal, intent_key
from solvent.kernel.allocator import IntentKind, TradeIntent
from solvent.ops.exec_recovery import auto_reconcile_no_broadcast, main


def _intent(notional=2.0):
    return TradeIntent(
        kind=IntentKind.QUALIFY,
        from_symbol="USDT",
        to_symbol="USDC",
        notional_usd=notional,
        reason="test",
    )


def _pending(tmp_path, notional=2.0):
    journal = Journal(tmp_path / "journal.jsonl")
    intent = _intent(notional)
    key = intent_key(intent, "20260624T20")
    journal.mark_attempted(key, intent)
    return journal, key


def test_list_unresolved_handles_missing_journal(tmp_path, capsys):
    assert main(["--data-dir", str(tmp_path), "list-unresolved"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"unresolved": []}
    assert not (tmp_path / "journal.jsonl").exists()


def test_list_unresolved_outputs_pending_entries(tmp_path, capsys):
    _, key = _pending(tmp_path)

    assert main(["--data-dir", str(tmp_path), "list-unresolved"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["unresolved"][0]["key"] == key
    assert payload["unresolved"][0]["state"] == Journal.PENDING


def test_mark_confirmed_requires_valid_tx_hash(tmp_path):
    _, key = _pending(tmp_path)

    with pytest.raises(SystemExit):
        main(
            [
                "--data-dir",
                str(tmp_path),
                "mark-confirmed",
                "--key",
                key,
                "--tx-hash",
                "0x1234",
            ]
        )


def test_mark_confirmed_can_use_mined_at_for_confirmed_day(tmp_path, capsys):
    _, key = _pending(tmp_path)
    tx_hash = "0x" + "ab" * 32

    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "mark-confirmed",
                "--key",
                key,
                "--tx-hash",
                tx_hash,
                "--mined-at",
                "2026-06-25T01:02:03Z",
                "--skip-chain-check",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved"]["state"] == "CONFIRMED"
    assert payload["resolved"]["tx_hash"] == tx_hash
    journal = Journal(tmp_path / "journal.jsonl")
    assert journal.has_unresolved() is False
    assert journal.confirmed_trades_on("2026-06-25") == 1


def test_mark_confirmed_requires_chain_check_by_default(tmp_path):
    _, key = _pending(tmp_path)

    with pytest.raises(SystemExit, match="wallet-address"):
        main(
            [
                "--data-dir",
                str(tmp_path),
                "mark-confirmed",
                "--key",
                key,
                "--tx-hash",
                "0x" + "ab" * 32,
            ]
        )


def test_mark_failed_requires_operator_reason(tmp_path):
    _, key = _pending(tmp_path)

    with pytest.raises(SystemExit):
        main(
            [
                "--data-dir",
                str(tmp_path),
                "mark-failed",
                "--key",
                key,
                "--reason",
                " ",
            ]
        )


def test_mark_failed_frees_only_that_journal_entry(tmp_path, capsys):
    _, key = _pending(tmp_path)
    other_intent = _intent(3.0)
    other_key = intent_key(other_intent, "20260624T20")
    Journal(tmp_path / "journal.jsonl").mark_attempted(other_key, other_intent)

    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "mark-failed",
                "--key",
                key,
                "--reason",
                "BscScan shows no matching swap from the wallet.",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved"]["state"] == "FAILED"
    journal = Journal(tmp_path / "journal.jsonl")
    assert journal.state_of(key) == "FAILED"
    assert journal.state_of(other_key) == Journal.PENDING
    assert journal.has_unresolved() is True


def test_mark_confirmed_refuses_non_pending_entry(tmp_path):
    journal, key = _pending(tmp_path)
    journal.mark_result(key, ok=True, tx_hash="0xdead", detail="already resolved")

    with pytest.raises(SystemExit):
        main(
            [
                "--data-dir",
                str(tmp_path),
                "mark-confirmed",
                "--key",
                key,
                "--tx-hash",
                "0x" + "ab" * 32,
                "--skip-chain-check",
            ]
        )


# ── Auto-recovery of no-broadcast timeouts ─────────────────────────────


class _FakeBook:
    def __init__(self, balances: dict) -> None:
        self._b = balances

    def balance(self, symbol: str) -> float:
        return self._b[symbol]


def _exit_intent() -> TradeIntent:
    return TradeIntent(
        kind=IntentKind.EXIT,
        from_symbol="ETH",
        to_symbol="USDT",
        notional_usd=13.0,
        reason="degraded unwind",
    )


def test_auto_recovery_heals_unchanged_source_balance(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")
    journal.mark_attempted(
        "k1", _exit_intent(), pre_balances={"ETH": 0.0078, "USDT": 100.0}
    )
    # ETH unchanged on-chain => the swap never broadcast => safe to fail.
    healed = auto_reconcile_no_broadcast(journal, _FakeBook({"ETH": 0.0078}))
    assert healed == ["k1"]
    assert not journal.has_unresolved()


def test_auto_recovery_leaves_attempt_when_source_spent(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")
    journal.mark_attempted(
        "k2", _exit_intent(), pre_balances={"ETH": 0.0078, "USDT": 100.0}
    )
    # ETH spent => a swap likely broadcast => leave PENDING for manual review.
    healed = auto_reconcile_no_broadcast(journal, _FakeBook({"ETH": 0.0}))
    assert healed == []
    assert journal.has_unresolved()


def test_auto_recovery_skips_without_snapshot(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")
    journal.mark_attempted("k3", _exit_intent())  # no pre_balances
    healed = auto_reconcile_no_broadcast(journal, _FakeBook({"ETH": 0.0}))
    assert healed == []
    assert journal.has_unresolved()
