import json
from datetime import datetime, timezone

import pytest

from solvent.commerce import server
from solvent.commerce.signal import SERVICE_ID, build_job_response, build_signal_payload
from solvent.receipts.chain import ReceiptChain


def test_signal_payload_binds_to_latest_receipt_and_anchor(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_AGENT_ID", "136384")
    monkeypatch.setenv("SOLVENT_WALLET_ADDRESS", "0x" + "12" * 20)
    chain = ReceiptChain(tmp_path / "receipts.jsonl")
    proof = {
        "proof_hash": "0x" + "ab" * 32,
        "input_hash": "0x" + "cd" * 32,
        "output_hash": "0x" + "ef" * 32,
    }
    rec = chain.append(
        ts="2026-06-24T12:00:00+00:00",
        phase="cycle_summary",
        cycle_id="20260624T12",
        signals={
            "fear_greed": 62,
            "active_risk_profile": "conviction_50",
            "momentum": {"CAKE": 4.2, "AAVE": 1.1},
        },
        inference_proof=proof,
        regime="risk-on",
        thesis="enter strongest executable momentum",
        equity_usd=50.0,
        dq_headroom_pct=0.3,
    )
    (tmp_path / "anchors.json").write_text(
        json.dumps(
            {
                "2026-06-24": {
                    "head_hash": rec.hash,
                    "tx_hash": "0x" + "34" * 32,
                }
            }
        )
    )

    payload = build_signal_payload(
        tmp_path,
        public_base_url="https://solvent.example",
        now=datetime(2026, 6, 24, 12, 1, tzinfo=timezone.utc),
    )

    assert payload["service"] == SERVICE_ID
    assert payload["source"]["receipt_hash"] == rec.hash
    assert payload["source"]["receipt_head_hash"] == rec.hash
    assert payload["source"]["latest_anchor"]["head_hash"] == rec.hash
    assert "inference_proofs_url" not in payload["source"]
    assert payload["proof"]["type"] == "hash_commitment"
    assert payload["proof"]["inference_commitment_hash"] == proof["proof_hash"]
    assert "inference_proof_hash" not in payload["proof"]
    assert payload["signal"]["top_momentum"][0]["symbol"] == "CAKE"
    assert payload["signal_hash"].startswith("0x")


def test_job_response_is_canonical_json_plus_metadata(tmp_path):
    chain = ReceiptChain(tmp_path / "receipts.jsonl")
    rec = chain.append(
        ts="2026-06-24T12:00:00+00:00",
        phase="cycle_summary",
        cycle_id="20260624T12",
        signals={"momentum": {}},
        inference_proof={"proof_hash": "0xproof"},
        regime="risk-off",
        thesis="hold",
    )

    response, metadata = build_job_response(tmp_path)
    payload = json.loads(response)

    assert payload["source"]["receipt_hash"] == rec.hash
    assert metadata["service"] == SERVICE_ID
    assert metadata["receipt_hash"] == rec.hash
    assert metadata["signal_hash"] == payload["signal_hash"]
    assert "inference_proof_hash" not in metadata


def test_service_price_rejects_zero(monkeypatch):
    monkeypatch.setenv("SOLVENT_ERC8183_SERVICE_PRICE", "0")
    with pytest.raises(RuntimeError, match="must be set > 0"):
        server._service_price()


def test_service_price_rejects_unset(monkeypatch):
    monkeypatch.delenv("SOLVENT_ERC8183_SERVICE_PRICE", raising=False)
    with pytest.raises(RuntimeError, match="raw token units"):
        server._service_price()


@pytest.mark.parametrize("raw", ["0.05", "1e18", "-1", "abc"])
def test_service_price_rejects_non_integer_raw_units(monkeypatch, raw):
    monkeypatch.setenv("SOLVENT_ERC8183_SERVICE_PRICE", raw)
    with pytest.raises(RuntimeError, match="raw token units"):
        server._service_price()


def test_service_price_accepts_positive_raw_units(monkeypatch):
    monkeypatch.setenv("SOLVENT_ERC8183_SERVICE_PRICE", "50000000000000000")
    assert server._service_price() == "50000000000000000"
