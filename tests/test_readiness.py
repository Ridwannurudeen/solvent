import json
import os
from datetime import datetime, timezone

from solvent.exec.executor import Journal
from solvent.kernel.allocator import IntentKind, TradeIntent
from solvent.ops.readiness import load_alert_env_file, readiness
from solvent.receipts.chain import DataPurchase, ReceiptChain


def _intent():
    return TradeIntent(
        kind=IntentKind.QUALIFY,
        from_symbol="USDT",
        to_symbol="USDC",
        notional_usd=2.0,
        reason="test",
    )


def _data_dir(tmp_path, *, paper=True):
    chain = ReceiptChain(tmp_path / "receipts.jsonl")
    chain.append(
        ts="2026-06-18T12:00:00Z",
        data_purchases=[
            DataPurchase(tool="get_global_metrics_latest", cost_usdc=0.01, ok=True)
        ],
        signals={"fear_greed": 25},
        regime="risk-off",
        thesis="hold",
        intents=[],
        executions=[],
        equity_usd=50.0,
        dq_headroom_pct=0.3,
    )
    (tmp_path / "heartbeat").write_text(datetime.now(timezone.utc).isoformat())
    (tmp_path / "anchors.json").write_text(
        json.dumps(
            {
                "2026-06-16": {
                    "head_hash": chain.head_hash,
                    "tx_hash": "0x" + "11" * 32,
                }
            }
        )
    )
    if paper:
        (tmp_path / "paper-holdings.json").write_text("{}")
    return tmp_path, chain.head_hash


def _fetcher(head_hash):
    def fetch(url):
        if url.endswith("/verify"):
            return 200, json.dumps({"ok": True, "count": 1, "head_hash": head_hash})
        if url.endswith("/state"):
            return 200, json.dumps(
                {
                    "alive": True,
                    "agent_id": 136384,
                    "anchor_network": "bsc-mainnet",
                    "anchors": [{"day": "2026-06-16"}],
                }
            )
        if url.endswith("/signal"):
            return 200, json.dumps(
                {
                    "schema": "solvent.erc8183.signal.v1",
                    "signal_hash": "0x" + "22" * 32,
                }
            )
        if url.endswith("/policy"):
            return 200, json.dumps(
                {
                    "manifest_hash": "0x" + "33" * 32,
                    "signature": {
                        "signer": "0x" + "12" * 20,
                        "signature": "0x" + "44" * 65,
                    },
                    "anchor": {"tx_hash": "0x" + "55" * 32},
                }
            )
        if url.endswith("/inference-commitments"):
            return 200, json.dumps([])
        if url.endswith("/inference-verification"):
            return 200, json.dumps(
                {
                    "schema": "solvent.inference-verification-log.v1",
                    "ok": True,
                    "count": 1,
                    "verified_count": 1,
                }
            )
        if url.endswith("/strategy-evidence"):
            return 200, json.dumps(
                {
                    "schema": "solvent.strategy-evidence.v1",
                    "edge_claim": {"guaranteed": False},
                    "scenarios": {"uptrend": {}},
                }
            )
        if url.endswith("/policy-compliance"):
            return 200, json.dumps(
                {"schema": "solvent.policy-compliance.v1", "ok": True}
            )
        if url.endswith("/proof"):
            return 200, "Mainnet evidence ... Track 1 registration"
        raise AssertionError(url)

    return fetch


def test_submission_readiness_allows_paper_public_state(tmp_path, monkeypatch):
    data_dir, head_hash = _data_dir(tmp_path, paper=True)
    monkeypatch.setenv("SOLVENT_PRIVATE_KEY", "not-printed")
    monkeypatch.setenv("SOLVENT_WALLET_PASSWORD", "not-printed")
    monkeypatch.setenv("SOLVENT_WALLET_ADDRESS", "0x" + "12" * 20)
    monkeypatch.setenv("SOLVENT_TRADE_NETWORK", "bsc-mainnet")
    monkeypatch.setenv("SOLVENT_TWAK_CHAIN", "bsc")

    report = readiness(data_dir, fetcher=_fetcher(head_hash))

    assert report["ok"] is True
    assert report["profile"] == "submission"
    assert any(
        c["name"] == "live_data_dir_isolated" and c["required"] is False
        for c in report["checks"]
    )
    assert any(c["name"] == "public_inference_verification" for c in report["checks"])
    assert any(c["name"] == "public_strategy_evidence" for c in report["checks"])
    assert "not-printed" not in str(report)


def test_live_readiness_requires_isolated_data_dir(tmp_path, monkeypatch):
    data_dir, head_hash = _data_dir(tmp_path, paper=True)
    monkeypatch.setenv("SOLVENT_PRIVATE_KEY", "not-printed")
    monkeypatch.setenv("SOLVENT_WALLET_PASSWORD", "not-printed")
    monkeypatch.setenv("SOLVENT_WALLET_ADDRESS", "0x" + "12" * 20)
    monkeypatch.setenv("SOLVENT_TRADE_NETWORK", "bsc-mainnet")
    monkeypatch.setenv("SOLVENT_TWAK_CHAIN", "bsc")

    report = readiness(data_dir, profile="live", fetcher=_fetcher(head_hash))

    assert report["ok"] is False
    assert any(
        c["name"] == "live_data_dir_isolated"
        and c["required"] is True
        and c["ok"] is False
        for c in report["checks"]
    )


def test_readiness_fails_on_public_head_mismatch(tmp_path, monkeypatch):
    data_dir, _ = _data_dir(tmp_path, paper=False)
    monkeypatch.setenv("SOLVENT_PRIVATE_KEY", "not-printed")
    monkeypatch.setenv("SOLVENT_WALLET_PASSWORD", "not-printed")
    monkeypatch.setenv("SOLVENT_WALLET_ADDRESS", "0x" + "12" * 20)
    monkeypatch.setenv("SOLVENT_TRADE_NETWORK", "bsc-mainnet")
    monkeypatch.setenv("SOLVENT_TWAK_CHAIN", "bsc")

    report = readiness(data_dir, fetcher=_fetcher("0x" + "ff" * 32))

    assert report["ok"] is False
    assert any(
        c["name"] == "public_head_matches_local" and c["ok"] is False
        for c in report["checks"]
    )


def test_live_readiness_requires_twak_credentials(tmp_path, monkeypatch):
    # Full SOLVENT_* live env but neither explicit TWAK env nor working local
    # TWAK setup: the live trade path cannot broadcast, so readiness fails.
    data_dir, head_hash = _data_dir(tmp_path, paper=False)
    monkeypatch.setenv("SOLVENT_PRIVATE_KEY", "x")
    monkeypatch.setenv("SOLVENT_WALLET_PASSWORD", "x")
    monkeypatch.setenv("SOLVENT_WALLET_ADDRESS", "0x" + "12" * 20)
    monkeypatch.setenv("SOLVENT_TRADE_NETWORK", "bsc-mainnet")
    monkeypatch.setenv("SOLVENT_TWAK_CHAIN", "bsc")
    monkeypatch.setenv("SOLVENT_TG_BOT_TOKEN", "x")
    monkeypatch.setenv("SOLVENT_TG_CHAT_ID", "x")
    for name in ("TWAK_ACCESS_ID", "TWAK_HMAC_SECRET", "TWAK_WALLET_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    report = readiness(data_dir, profile="live", fetcher=_fetcher(head_hash))

    assert report["ok"] is False
    assert any(
        c["name"] == "live_twak_credentials_available" and c["required"] and not c["ok"]
        for c in report["checks"]
    )


def test_live_readiness_accepts_twak_file_auth(tmp_path, monkeypatch):
    data_dir, head_hash = _data_dir(tmp_path, paper=False)
    monkeypatch.setenv("SOLVENT_TG_BOT_TOKEN", "x")
    monkeypatch.setenv("SOLVENT_TG_CHAT_ID", "x")

    def fake_preflight(_data_dir, include_twak=True):
        assert include_twak is True
        return {
            "env": {
                "required_live": {
                    "SOLVENT_PRIVATE_KEY": True,
                    "SOLVENT_WALLET_PASSWORD": True,
                    "SOLVENT_WALLET_ADDRESS": True,
                    "SOLVENT_TRADE_NETWORK": True,
                    "SOLVENT_TWAK_CHAIN": True,
                },
                "required_live_twak": {
                    "TWAK_ACCESS_ID": False,
                    "TWAK_HMAC_SECRET": False,
                    "TWAK_WALLET_PASSWORD": False,
                },
            },
            "paper_data_in_dir": False,
            "journal_has_unresolved": False,
            "twak": {
                "auth_status": {"ok": True},
                "wallet_balance": {"ok": True, "result": {"totalUsd": 1.0}},
            },
        }

    monkeypatch.setattr("solvent.ops.readiness.preflight", fake_preflight)

    report = readiness(data_dir, profile="live", fetcher=_fetcher(head_hash))

    assert report["ok"] is True
    assert any(
        c["name"] == "live_twak_credentials_available"
        and c["ok"]
        and "local setup" in c["detail"]
        for c in report["checks"]
    )


def test_load_alert_env_file_overrides_blank_alert_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_TG_BOT_TOKEN", "")
    monkeypatch.setenv("SOLVENT_TG_CHAT_ID", "")
    monkeypatch.delenv("IGNORED_SECRET", raising=False)
    path = tmp_path / "telegram-alerts"
    path.write_text(
        "\n".join(
            [
                "SOLVENT_TG_BOT_TOKEN='new-token'",
                'SOLVENT_TG_CHAT_ID="new-chat"',
                "IGNORED_SECRET=not-loaded",
            ]
        )
    )

    assert load_alert_env_file(path) is True

    assert "IGNORED_SECRET" not in os.environ
    assert os.environ["SOLVENT_TG_BOT_TOKEN"] == "new-token"
    assert os.environ["SOLVENT_TG_CHAT_ID"] == "new-chat"


def test_load_alert_env_file_missing_file_is_noop(tmp_path):
    assert load_alert_env_file(tmp_path / "missing") is False


def test_empty_local_chain_surfaces_explicit_head_check(tmp_path):
    # No local receipts: the head-vs-local check must appear explicitly (as a
    # non-required note), not be silently omitted and read as a pass. (#15)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    report = readiness(
        data_dir, profile="submission", fetcher=_fetcher("0x" + "00" * 32)
    )

    head = [c for c in report["checks"] if c["name"] == "public_head_matches_local"]
    assert head and head[0]["required"] is False
    assert "no local chain" in head[0]["detail"]


def test_readiness_fails_on_unresolved_journal(tmp_path, monkeypatch):
    data_dir, head_hash = _data_dir(tmp_path, paper=False)
    Journal(data_dir / "journal.jsonl").mark_attempted("pending", _intent())
    monkeypatch.setenv("SOLVENT_PRIVATE_KEY", "not-printed")
    monkeypatch.setenv("SOLVENT_WALLET_PASSWORD", "not-printed")
    monkeypatch.setenv("SOLVENT_WALLET_ADDRESS", "0x" + "12" * 20)
    monkeypatch.setenv("SOLVENT_TRADE_NETWORK", "bsc-mainnet")
    monkeypatch.setenv("SOLVENT_TWAK_CHAIN", "bsc")

    report = readiness(data_dir, fetcher=_fetcher(head_hash))

    assert report["ok"] is False
    assert any(
        c["name"] == "journal_clear" and c["ok"] is False for c in report["checks"]
    )
