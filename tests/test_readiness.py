import json
from datetime import datetime, timezone

from solvent.exec.executor import Journal
from solvent.kernel.allocator import IntentKind, TradeIntent
from solvent.ops.readiness import readiness
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
