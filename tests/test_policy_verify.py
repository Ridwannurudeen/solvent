import json
from datetime import datetime, timedelta, timezone

from eth_account import Account

from solvent.exec.executor import Journal
from solvent.kernel.allocator import IntentKind, TradeIntent
from solvent.policy.manifest import build_policy_manifest, sign_policy_manifest
from solvent.policy.verify import policy_compliance_report
from solvent.receipts.chain import DataPurchase, ReceiptChain


NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
TX = "0x" + "11" * 32


def _intent():
    return TradeIntent(
        kind=IntentKind.QUALIFY,
        from_symbol="USDT",
        to_symbol="USDC",
        notional_usd=2.0,
        reason="test qualification",
    )


def _seed_policy(data_dir, wallet):
    payload = build_policy_manifest(
        profile="safety",
        generated_at=NOW,
        git_commit="abc123",
        env={
            "SOLVENT_AGENT_ID": "136384",
            "SOLVENT_WALLET_ADDRESS": wallet.address,
            "SOLVENT_PRETRADE_ANCHOR": "1",
        },
    )
    payload["signature"] = sign_policy_manifest(
        payload["manifest_hash"], wallet.key.hex()
    )
    payload["anchor"] = {"tx_hash": "0x" + "22" * 32}
    (data_dir / "policy-manifest.json").write_text(json.dumps(payload))
    return payload


def _seed_receipts(data_dir, *, pretrade_anchor=True):
    chain = ReceiptChain(data_dir / "receipts.jsonl")
    intent = _intent()
    intent_payload = {
        "key": "cycle:qualify:USDT->USDC:2.00",
        "cycle_id": "cycle",
        "kind": "qualify",
        "from": "USDT",
        "to": "USDC",
        "notional_usd": 2.0,
        "reason": intent.reason,
    }
    pre = chain.append(
        ts=NOW.isoformat(),
        phase="pre_trade_commit",
        cycle_id="cycle",
        intent_key=intent_payload["key"],
        signals={"active_risk_profile": "safety"},
        regime="risk-off",
        thesis=intent.reason,
        intents=[intent_payload],
        equity_usd=50.0,
    )
    verification = {
        "tx_hash": TX,
        "status": 1,
        "from_symbol": "USDT",
        "to_symbol": "USDC",
        "from_transfer_out": 2.0,
        "to_transfer_in": 1.99,
    }
    chain.append(
        ts=NOW.isoformat(),
        phase="execution_seal",
        cycle_id="cycle",
        intent_key=intent_payload["key"],
        pre_trade_hash=pre.hash,
        regime="risk-off",
        thesis="settled",
        intents=[intent_payload],
        executions=[
            {
                "key": intent_payload["key"],
                "ok": True,
                "tx_hash": TX,
                "outcome": "executed_now",
                "verification": verification,
            }
        ],
        execution_seal={
            "ok": True,
            "outcome": "executed_now",
            "applies_state_change": True,
            "tx_hash": TX,
            "pre_trade_anchor_tx_hash": "0x" + "33" * 32 if pretrade_anchor else None,
            "verification": verification,
        },
        equity_usd=50.0,
    )
    chain.append(
        ts=NOW.isoformat(),
        phase="cycle_summary",
        cycle_id="cycle",
        data_purchases=[
            DataPurchase(
                tool="get_crypto_quotes_latest",
                cost_usdc=0.01,
                ok=True,
                response_hash="0x" + "44" * 32,
                response_bytes=128,
            )
        ],
        signals={"active_risk_profile": "safety"},
        regime="risk-off",
        thesis="done",
        intents=[intent_payload],
        executions=[],
        equity_usd=50.0,
    )
    (data_dir / "anchors.json").write_text(
        json.dumps(
            {
                "2026-06-21": {
                    "head_hash": chain.head_hash,
                    "tx_hash": "0x" + "55" * 32,
                }
            }
        )
    )
    journal = Journal(data_dir / "journal.jsonl")
    journal.mark_attempted(intent_payload["key"], intent)
    journal.mark_result(
        intent_payload["key"], True, TX, "settled", verification=verification
    )


def test_policy_compliance_passes_for_verified_execution(tmp_path):
    wallet = Account.create()
    _seed_policy(tmp_path, wallet)
    _seed_receipts(tmp_path)

    report = policy_compliance_report(tmp_path, now=NOW)

    assert report["ok"] is True
    assert report["passport"]["executed_now_count"] == 1
    assert report["passport"]["unresolved_execution_count"] == 0
    assert report["passport"]["anchor_coverage"]["unanchored_count"] == 0


def test_policy_compliance_fails_missing_pretrade_anchor(tmp_path):
    wallet = Account.create()
    _seed_policy(tmp_path, wallet)
    _seed_receipts(tmp_path, pretrade_anchor=False)

    report = policy_compliance_report(tmp_path, now=NOW)

    assert report["ok"] is False
    assert any(
        check["name"].endswith("_pretrade_anchor_present") and check["ok"] is False
        for check in report["checks"]
    )


def test_policy_compliance_ignores_pre_manifest_journal_rows(tmp_path):
    wallet = Account.create()
    _seed_policy(tmp_path, wallet)
    _seed_receipts(tmp_path)
    Journal(tmp_path / "journal.jsonl").mark_result(
        "old-rehearsal",
        True,
        "0x" + "66" * 32,
        "pre-policy rehearsal",
        ts=NOW - timedelta(days=1),
    )

    report = policy_compliance_report(tmp_path, now=NOW)

    assert report["ok"] is True
