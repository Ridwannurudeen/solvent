import json
from datetime import datetime, timezone

from solvent.exec.executor import Journal, PaperExecutor, TwakExecutor
from solvent.kernel.rules import RiskConfig
from solvent.ops.deadman import make_executor, run_deadman
from solvent.receipts.chain import ReceiptChain

CFG = RiskConfig()
# A fixed UTC day; hour chosen relative to the qualification deadline.
TODAY = datetime.now(timezone.utc).date()


def _at(hour: int) -> datetime:
    return datetime(TODAY.year, TODAY.month, TODAY.day, hour, 0, tzinfo=timezone.utc)


def _journal(tmp_path) -> Journal:
    return Journal(tmp_path / "journal.jsonl")


def test_fires_when_unqualified_and_past_deadline(tmp_path):
    journal = _journal(tmp_path)
    summary = run_deadman(
        executor=PaperExecutor(journal),
        journal=journal,
        cfg=CFG,
        now=_at(CFG.qual_deadline_hour_utc + 1),
    )
    assert summary["action"] == "qualify"
    assert summary["ok"] is True
    assert journal.confirmed_trades_on(str(TODAY)) == 1


def test_deadman_emits_pre_trade_and_seal_receipts(tmp_path):
    journal = _journal(tmp_path)
    receipts = ReceiptChain(tmp_path / "receipts.jsonl")
    summary = run_deadman(
        executor=PaperExecutor(journal),
        journal=journal,
        cfg=CFG,
        receipts=receipts,
        now=_at(CFG.qual_deadline_hour_utc + 1),
    )
    entries = [
        json.loads(line)["receipt"]
        for line in (tmp_path / "receipts.jsonl").read_text().splitlines()
    ]

    assert summary["action"] == "qualify"
    assert [entry["phase"] for entry in entries] == [
        "pre_trade_commit",
        "execution_seal",
    ]
    assert entries[1]["pre_trade_hash"]


def test_deadman_pretrade_publisher_runs_before_execution(tmp_path):
    class Publisher:
        def __init__(self):
            self.calls = []

        def publish(self, *, cycle_id, intent_key, commit_hash):
            self.calls.append((cycle_id, intent_key, commit_hash))
            return "0x" + "55" * 32

    journal = _journal(tmp_path)
    receipts = ReceiptChain(tmp_path / "receipts.jsonl")
    publisher = Publisher()
    run_deadman(
        executor=PaperExecutor(journal),
        journal=journal,
        cfg=CFG,
        receipts=receipts,
        pretrade_publisher=publisher,
        now=_at(CFG.qual_deadline_hour_utc + 1),
    )
    entries = [
        json.loads(line)["receipt"]
        for line in (tmp_path / "receipts.jsonl").read_text().splitlines()
    ]

    assert len(publisher.calls) == 1
    assert (
        publisher.calls[0][2]
        == json.loads((tmp_path / "receipts.jsonl").read_text().splitlines()[0])["hash"]
    )
    assert entries[1]["execution_seal"]["pre_trade_anchor_tx_hash"] == "0x" + "55" * 32


def test_noop_before_deadline(tmp_path):
    journal = _journal(tmp_path)
    summary = run_deadman(
        executor=PaperExecutor(journal),
        journal=journal,
        cfg=CFG,
        now=_at(CFG.qual_deadline_hour_utc - 1),
    )
    assert summary["action"] == "none"
    assert journal.confirmed_trades_on(str(TODAY)) == 0


def test_noop_when_already_qualified(tmp_path):
    journal = _journal(tmp_path)
    # Seed a confirmed trade earlier today (as the hourly cycle would).
    journal.mark_attempted("seed", _intent())
    journal.mark_result("seed", True, "0xabc", "prior cycle trade")
    assert journal.confirmed_trades_on(str(TODAY)) == 1

    summary = run_deadman(
        executor=PaperExecutor(journal),
        journal=journal,
        cfg=CFG,
        now=_at(CFG.qual_deadline_hour_utc + 1),
    )
    assert summary["action"] == "none"
    assert summary["reason"] == "already qualified today"


def test_idempotent_across_two_runs(tmp_path):
    journal = _journal(tmp_path)
    now = _at(CFG.qual_deadline_hour_utc + 1)
    run_deadman(executor=PaperExecutor(journal), journal=journal, cfg=CFG, now=now)
    # Re-run (e.g. timer double-fires): same intent key -> no second trade.
    run_deadman(executor=PaperExecutor(journal), journal=journal, cfg=CFG, now=now)
    assert journal.confirmed_trades_on(str(TODAY)) == 1


def test_independent_of_signals():
    """run_deadman's signature takes no source/signals — the path cannot
    depend on market data by construction."""
    import inspect

    params = set(inspect.signature(run_deadman).parameters)
    assert params == {
        "executor",
        "journal",
        "cfg",
        "receipts",
        "pretrade_publisher",
        "now",
    }
    assert "source" not in params and "signals" not in params


def test_live_mode_uses_twak_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_TWAK_CHAIN", "bsc")
    journal = _journal(tmp_path)
    executor = make_executor("live", journal, CFG)
    assert isinstance(executor, TwakExecutor)
    assert executor.chain == "bsc"


def _intent():
    from solvent.kernel.allocator import IntentKind, TradeIntent

    return TradeIntent(
        kind=IntentKind.QUALIFY,
        from_symbol="USDT",
        to_symbol="USDC",
        notional_usd=2.0,
        reason="seed",
    )
