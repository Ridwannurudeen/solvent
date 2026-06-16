from datetime import datetime, timezone

from solvent.exec.executor import Journal, PaperExecutor
from solvent.kernel.rules import RiskConfig
from solvent.ops.deadman import run_deadman

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
    assert params == {"executor", "journal", "cfg", "now"}
    assert "source" not in params and "signals" not in params


def _intent():
    from solvent.kernel.allocator import IntentKind, TradeIntent

    return TradeIntent(
        kind=IntentKind.QUALIFY,
        from_symbol="USDT",
        to_symbol="USDC",
        notional_usd=2.0,
        reason="seed",
    )
