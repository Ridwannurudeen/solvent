from datetime import datetime, timezone

from solvent.exec.executor import Journal
from solvent.kernel.allocator import IntentKind, TradeIntent
from solvent.ops.preflight import preflight


def _intent():
    return TradeIntent(
        kind=IntentKind.QUALIFY,
        from_symbol="USDT",
        to_symbol="USDC",
        notional_usd=2.0,
        reason="test",
    )


def test_preflight_reports_non_secret_env_and_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_PRIVATE_KEY", "not-printed")
    monkeypatch.setenv("SOLVENT_WALLET_PASSWORD", "not-printed")
    monkeypatch.setenv("SOLVENT_WALLET_ADDRESS", "0x" + "12" * 20)
    monkeypatch.setenv("SOLVENT_DATA_DIR", str(tmp_path))
    (tmp_path / "heartbeat").write_text(datetime.now(timezone.utc).isoformat())
    (tmp_path / "paper-holdings.json").write_text("{}")
    journal = Journal(tmp_path / "journal.jsonl")
    journal.mark_attempted("pending", _intent())

    report = preflight(tmp_path, include_twak=False)

    assert report["env"]["required_live"]["SOLVENT_PRIVATE_KEY"] is True
    assert report["env"]["required_live"]["SOLVENT_WALLET_ADDRESS"] is True
    assert report["env"]["secrets_present"]["SOLVENT_PRIVATE_KEY"] is True
    assert "not-printed" not in str(report)
    assert report["paper_data_in_dir"] is True
    assert report["journal_has_unresolved"] is True
    assert "twak" not in report
