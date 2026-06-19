"""Money-safety invariants of the execution layer.

TwakExecutor moves real value, so its idempotency and halt behavior are the
load-bearing safety property: ONE transaction per intent, ever, and never a
blind re-send of a value-moving call whose outcome is unknown. These tests
exercise that without a real `twak` binary by stubbing subprocess.run.
"""

import json
from types import SimpleNamespace


from solvent.exec import executor as exec_mod
from solvent.exec.executor import (
    Journal,
    PaperExecutor,
    TwakExecutor,
    intent_key,
    intent_payload,
)
from solvent.kernel.allocator import IntentKind, TradeIntent

CYCLE = "20260624T12"


def _intent(notional=2.0, to="USDC"):
    return TradeIntent(
        kind=IntentKind.QUALIFY,
        from_symbol="USDT",
        to_symbol=to,
        notional_usd=notional,
        reason="test",
    )


def _ok_proc(tx="0x" + "ab" * 32):
    return SimpleNamespace(returncode=0, stdout=f'{{"txHash":"{tx}"}}', stderr="")


def _twak(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")
    ex = TwakExecutor(journal, password="pw")
    return ex, journal


# ── intent_key + Journal ──────────────────────────────────────────────


def test_intent_key_is_stable_and_specific():
    a = intent_key(_intent(2.0), CYCLE)
    assert a == intent_key(_intent(2.0), CYCLE)  # deterministic
    assert a != intent_key(_intent(3.0), CYCLE)  # notional matters
    assert a != intent_key(_intent(2.0, to="DAI"), CYCLE)  # destination matters
    assert "USDT->USDC" in a and "2.00" in a


def test_intent_payload_uses_intent_key():
    payload = intent_payload(_intent(), CYCLE)

    assert payload["key"] == intent_key(_intent(), CYCLE)
    assert payload["kind"] == "qualify"
    assert payload["notional_usd"] == 2.0


def test_journal_persists_and_counts(tmp_path):
    j = Journal(tmp_path / "j.jsonl")
    key = intent_key(_intent(), CYCLE)
    j.mark_attempted(key, _intent())
    assert j.has_unresolved() is True
    assert j.state_of(key) == Journal.PENDING
    j.mark_result(key, ok=True, tx_hash="0xdead", detail="ok")
    assert j.has_unresolved() is False
    # Reload from disk: state survives.
    j2 = Journal(tmp_path / "j.jsonl")
    assert j2.state_of(key) == "CONFIRMED"
    day = j2._entries[key]["ts"][:10]
    assert j2.confirmed_trades_on(day) == 1
    assert j2.confirmed_trades_on("1999-01-01") == 0


# ── TwakExecutor happy path + idempotency ─────────────────────────────


def test_execute_confirms_and_records_tx(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        exec_mod.subprocess, "run", lambda *a, **k: calls.append(a) or _ok_proc()
    )
    ex, journal = _twak(tmp_path)
    result = ex.execute(_intent(), CYCLE)
    assert result.ok is True
    assert result.tx_hash.startswith("0x")
    assert len(calls) == 1
    assert journal.state_of(result.intent_key) == "CONFIRMED"


def test_execute_requires_receipt_verifier_when_configured(tmp_path, monkeypatch):
    tx = "0x" + "ab" * 32
    seen = []
    monkeypatch.setattr(exec_mod.subprocess, "run", lambda *a, **k: _ok_proc(tx=tx))
    journal = Journal(tmp_path / "journal.jsonl")
    ex = TwakExecutor(
        journal,
        receipt_verifier=lambda _intent, h, _before: seen.append(h) or {},
    )

    result = ex.execute(_intent(), CYCLE)

    assert result.ok is True
    assert result.outcome == "executed_now"
    assert seen == [tx]
    assert journal.state_of(result.intent_key) == "CONFIRMED"


def test_execute_stores_pre_balances_and_verification(tmp_path, monkeypatch):
    tx = "0x" + "ab" * 32
    seen = []
    monkeypatch.setattr(exec_mod.subprocess, "run", lambda *a, **k: _ok_proc(tx=tx))
    journal = Journal(tmp_path / "journal.jsonl")

    def verifier(intent, tx_hash, before):
        seen.append((intent.from_symbol, tx_hash, before))
        return {"settled": True}

    ex = TwakExecutor(
        journal,
        receipt_verifier=verifier,
        balance_reader=lambda _intent: {"USDT": 10.0, "USDC": 0.0},
    )

    result = ex.execute(_intent(), CYCLE)

    assert result.verification == {"settled": True}
    assert seen == [("USDT", tx, {"USDT": 10.0, "USDC": 0.0})]
    confirmed = journal.latest_entry(result.intent_key)
    assert confirmed["verification"] == {"settled": True}
    attempted = [
        json.loads(line)
        for line in (tmp_path / "journal.jsonl").read_text().splitlines()
    ][0]
    assert attempted["pre_balances"] == {"USDT": 10.0, "USDC": 0.0}


def test_receipt_verifier_failure_leaves_attempt_unresolved(tmp_path, monkeypatch):
    monkeypatch.setattr(exec_mod.subprocess, "run", lambda *a, **k: _ok_proc())

    def fail(_intent, _tx_hash, _before):
        raise RuntimeError("receipt missing")

    journal = Journal(tmp_path / "journal.jsonl")
    ex = TwakExecutor(journal, receipt_verifier=fail)

    result = ex.execute(_intent(), CYCLE)

    assert result.ok is False
    assert result.outcome == "unresolved"
    assert "receipt missing" in result.detail
    assert journal.state_of(result.intent_key) == Journal.PENDING


def test_already_confirmed_intent_is_skipped(tmp_path, monkeypatch):
    runs = []
    monkeypatch.setattr(
        exec_mod.subprocess, "run", lambda *a, **k: runs.append(1) or _ok_proc()
    )
    ex, _ = _twak(tmp_path)
    ex.execute(_intent(), CYCLE)  # confirms it
    again = ex.execute(_intent(), CYCLE)  # same key, same cycle
    assert again.ok is True
    assert again.outcome == "already_confirmed"
    assert again.applies_state_change is False
    assert again.tx_hash is not None
    assert "already confirmed" in again.detail
    assert len(runs) == 1  # NOT re-sent


def test_unresolved_prior_attempt_refuses_resend(tmp_path, monkeypatch):
    ex, journal = _twak(tmp_path)
    key = intent_key(_intent(), CYCLE)
    journal.mark_attempted(key, _intent())  # left PENDING, no result
    ran = []
    monkeypatch.setattr(exec_mod.subprocess, "run", lambda *a, **k: ran.append(1))
    result = ex.execute(_intent(), CYCLE)
    assert result.ok is False
    assert result.outcome == "unresolved"
    assert "unresolved" in result.detail
    assert ran == []  # never sent


def test_any_unresolved_entry_halts_a_new_intent(tmp_path, monkeypatch):
    ex, journal = _twak(tmp_path)
    # A different intent left dangling PENDING from a prior cycle.
    journal.mark_attempted(intent_key(_intent(9.0), "20260624T11"), _intent(9.0))
    ran = []
    monkeypatch.setattr(exec_mod.subprocess, "run", lambda *a, **k: ran.append(1))
    result = ex.execute(_intent(), CYCLE)  # a brand-new intent
    assert result.ok is False
    assert result.outcome == "unresolved"
    assert "halted" in result.detail
    assert ran == []  # trading frozen until the dangling attempt resolves


def test_failed_prior_attempt_refuses_resend_same_key(tmp_path, monkeypatch):
    ex, journal = _twak(tmp_path)
    key = intent_key(_intent(), CYCLE)
    journal.mark_attempted(key, _intent())
    journal.mark_result(key, ok=False, tx_hash=None, detail="operator failed")
    ran = []
    monkeypatch.setattr(exec_mod.subprocess, "run", lambda *a, **k: ran.append(1))

    result = ex.execute(_intent(), CYCLE)

    assert result.ok is False
    assert result.outcome == "failed"
    assert "failed" in result.detail
    assert ran == []


def test_timeout_leaves_attempt_unresolved_and_halts_next(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise exec_mod.subprocess.TimeoutExpired(cmd="twak", timeout=1)

    monkeypatch.setattr(exec_mod.subprocess, "run", boom)
    ex, journal = _twak(tmp_path)
    result = ex.execute(_intent(), CYCLE)
    assert result.ok is False
    assert result.outcome == "unresolved"
    assert "timeout" in result.detail
    assert journal.has_unresolved() is True  # outcome unknown -> stays PENDING
    # The very next intent is now halted, even with a working binary.
    monkeypatch.setattr(exec_mod.subprocess, "run", lambda *a, **k: _ok_proc())
    nxt = ex.execute(_intent(3.0), CYCLE)
    assert nxt.ok is False and "halted" in nxt.detail


def test_nonzero_exit_marks_failed_not_confirmed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        exec_mod.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    ex, journal = _twak(tmp_path)
    result = ex.execute(_intent(), CYCLE)
    assert result.ok is False
    assert result.outcome == "unresolved"
    assert journal.state_of(result.intent_key) == Journal.PENDING
    assert journal.has_unresolved() is True


def test_ok_output_without_json_is_parsed_as_tx_hash(tmp_path, monkeypatch):
    tx = "0x" + "bb" * 32

    monkeypatch.setattr(
        exec_mod.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=f"completed: {tx}", stderr=""
        ),
    )
    ex, journal = _twak(tmp_path)
    result = ex.execute(_intent(), CYCLE)

    assert result.ok is True
    assert result.outcome == "executed_now"
    assert result.applies_state_change is True
    assert result.tx_hash == tx
    assert journal.state_of(result.intent_key) == "CONFIRMED"


def test_zero_exit_without_tx_hash_stays_unresolved(tmp_path, monkeypatch):
    monkeypatch.setattr(
        exec_mod.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout='{"status":"ok"}', stderr=""
        ),
    )
    ex, journal = _twak(tmp_path)
    result = ex.execute(_intent(), CYCLE)

    assert result.ok is False
    assert result.outcome == "unresolved"
    assert journal.state_of(result.intent_key) == Journal.PENDING
    assert journal.has_unresolved() is True


def test_password_passed_via_env_not_argv(tmp_path, monkeypatch):
    seen = {}

    def capture(cmd, **k):
        seen["cmd"] = cmd
        seen["env"] = k.get("env", {})
        return _ok_proc()

    monkeypatch.setattr(exec_mod.subprocess, "run", capture)
    ex, _ = _twak(tmp_path)
    ex.execute(_intent(), CYCLE)
    assert "pw" not in seen["cmd"]  # password never on the command line
    assert seen["env"].get("TWAK_WALLET_PASSWORD") == "pw"


def test_password_can_fall_back_to_twak_keychain(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.delenv("TWAK_WALLET_PASSWORD", raising=False)

    def capture(cmd, **k):
        seen["cmd"] = cmd
        seen["env"] = k.get("env", {})
        return _ok_proc()

    monkeypatch.setattr(exec_mod.subprocess, "run", capture)
    journal = Journal(tmp_path / "journal.jsonl")
    ex = TwakExecutor(journal)
    result = ex.execute(_intent(), CYCLE)

    assert result.ok is True
    assert "TWAK_WALLET_PASSWORD" not in seen["env"]
    assert "--json" in seen["cmd"]


# ── PaperExecutor ─────────────────────────────────────────────────────


def test_paper_executor_fills_and_is_idempotent(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    ex = PaperExecutor(journal)
    r1 = ex.execute(_intent(), CYCLE)
    assert r1.ok is True and r1.tx_hash.startswith("paper-")
    r2 = ex.execute(_intent(), CYCLE)
    assert r2.outcome == "already_confirmed"
    assert r2.applies_state_change is False
    assert "already confirmed" in r2.detail
