import json
from datetime import datetime, timezone

from solvent.receipts.chain import ReceiptChain
from solvent.receipts.server import (
    anchor_coverage,
    inference_commitments,
    inference_proofs,
    load_entries,
    policy_manifest,
    state,
    summary,
    verify,
)


def _seed(path):
    chain = ReceiptChain(path)
    chain.append(
        ts="2026-06-24T12:00:00+00:00",
        regime="risk-off",
        thesis="hold",
        equity_usd=300.0,
        dq_headroom_pct=0.3,
    )
    chain.append(
        ts="2026-06-24T13:00:00+00:00",
        regime="risk-on",
        thesis="enter CAKE",
        equity_usd=305.0,
        dq_headroom_pct=0.28,
    )


def test_empty_log(tmp_path):
    p = tmp_path / "receipts.jsonl"
    assert load_entries(p) == []
    assert verify(p) == {"ok": True, "count": 0, "head_hash": "0x" + "0" * 64}
    s = summary(p)
    assert s["receipts"] == 0 and s["latest_equity_usd"] is None


def test_load_and_verify(tmp_path):
    p = tmp_path / "receipts.jsonl"
    _seed(p)
    entries = load_entries(p)
    assert len(entries) == 2
    v = verify(p)
    assert v["ok"] is True
    assert v["count"] == 2
    assert v["head_hash"] == entries[-1]["hash"]


def test_summary_reports_latest(tmp_path):
    p = tmp_path / "receipts.jsonl"
    _seed(p)
    s = summary(p)
    assert s["agent"] == "SOLVENT"
    assert s["receipts"] == 2
    assert s["chain_ok"] is True
    assert s["latest_equity_usd"] == 305.0
    assert s["latest_regime"] == "risk-on"


def test_inference_proofs_reports_proof_receipts(tmp_path):
    p = tmp_path / "receipts.jsonl"
    chain = ReceiptChain(p)
    proof = {"schema": "solvent.inference-proof.v1", "proof_hash": "0xabc"}
    rec = chain.append(
        ts="2026-06-24T12:00:00+00:00",
        phase="cycle_summary",
        cycle_id="20260624T12",
        inference_proof=proof,
        regime="risk-off",
    )

    out = inference_proofs(p)

    assert out == [
        {
            "seq": rec.seq,
            "phase": "cycle_summary",
            "cycle_id": "20260624T12",
            "receipt_hash": rec.hash,
            "proof": proof,
        }
    ]


def test_inference_commitments_alias_matches_legacy_proofs(tmp_path):
    p = tmp_path / "receipts.jsonl"
    chain = ReceiptChain(p)
    chain.append(
        ts="2026-06-24T12:00:00+00:00",
        phase="cycle_summary",
        cycle_id="20260624T12",
        inference_proof={"schema": "solvent.inference-commitment.v1"},
        regime="risk-off",
    )

    assert inference_commitments(p) == inference_proofs(p)


def test_summary_ignores_latest_execution_seal(tmp_path):
    p = tmp_path / "receipts.jsonl"
    chain = ReceiptChain(p)
    chain.append(
        ts="2026-06-24T13:00:00+00:00",
        phase="cycle_summary",
        regime="risk-on",
        thesis="enter CAKE",
        equity_usd=305.0,
        dq_headroom_pct=0.28,
    )
    chain.append(
        ts="2026-06-24T13:00:01+00:00",
        phase="execution_seal",
        cycle_id="20260624T13",
        intent_key="k",
        execution_seal={"ok": True},
    )
    s = summary(p)
    assert s["latest_equity_usd"] == 305.0
    assert s["latest_regime"] == "risk-on"


def test_verify_detects_tamper(tmp_path):
    p = tmp_path / "receipts.jsonl"
    _seed(p)
    lines = p.read_text().splitlines()
    # Flip equity in the first receipt without recomputing hashes.
    lines[0] = lines[0].replace('"equity_usd":300.0', '"equity_usd":999.0')
    p.write_text("\n".join(lines) + "\n")
    assert verify(p)["ok"] is False


def test_state_empty(tmp_path):
    s = state(tmp_path)
    assert s["holdings"] == {}
    assert s["position"] is None
    assert s["heartbeat_age_s"] is None
    assert s["alive"] is False
    assert s["agent_id"] is None


def test_state_reports_holdings_and_liveness(tmp_path):
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "start_equity_usd": 300.0,
                "peak_equity_usd": 312.0,
                "position": {"symbol": "CAKE", "notional_usd": 60.0},
                "runtime_status": "HALTED",
                "halt_reason": "operator review required",
            }
        )
    )
    (tmp_path / "paper-holdings.json").write_text(
        json.dumps({"USDT": 240.0, "CAKE": 25.0})
    )
    (tmp_path / "heartbeat").write_text(datetime.now(timezone.utc).isoformat())
    s = state(tmp_path)
    assert s["start_equity_usd"] == 300.0
    assert s["peak_equity_usd"] == 312.0
    assert s["position"]["symbol"] == "CAKE"
    assert s["runtime_status"] == "HALTED"
    assert s["halt_reason"] == "operator review required"
    assert s["holdings"]["USDT"] == 240.0
    assert s["holdings_source"] == "paper"
    assert s["holdings_error"] is None
    assert s["alive"] is True
    assert s["heartbeat_age_s"] is not None and s["heartbeat_age_s"] < 60


def test_state_reads_live_holdings_when_live_and_no_paper_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_MODE", "live")
    (tmp_path / "state.json").write_text(json.dumps({"position": {"symbol": "CAKE"}}))

    s = state(tmp_path, live_reader=lambda position: {"USDT": 40.0, "CAKE": 2.0})

    assert s["holdings"] == {"USDT": 40.0, "CAKE": 2.0}
    assert s["holdings_source"] == "live"
    assert s["holdings_error"] is None


def test_state_prefers_live_holdings_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_MODE", "live")
    (tmp_path / "live-holdings.json").write_text(
        json.dumps({"ts": "2026-06-24T12:00:00+00:00", "holdings": {"USDT": 40.0}})
    )

    def fail(_position):
        raise AssertionError("live reader should not run when cache exists")

    s = state(tmp_path, live_reader=fail)

    assert s["holdings"] == {"USDT": 40.0}
    assert s["holdings_source"] == "live-cache"
    assert s["holdings_error"] is None


def test_state_reports_live_holdings_error_without_failing(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_MODE", "live")

    def fail(_position):
        raise RuntimeError("rpc unavailable")

    s = state(tmp_path, live_reader=fail)

    assert s["holdings"] == {}
    assert s["holdings_source"] == "live-error"
    assert s["holdings_error"] == "rpc unavailable"


def test_state_stale_heartbeat_not_alive(tmp_path):
    (tmp_path / "heartbeat").write_text("2020-01-01T00:00:00+00:00")
    s = state(tmp_path)
    assert s["alive"] is False
    assert s["heartbeat_age_s"] > 5400


def test_state_anchors_empty_by_default(tmp_path):
    s = state(tmp_path)
    assert s["anchors"] == []
    assert s["anchor_network"] == "bsc-testnet"


def test_state_reports_configured_agent_id_and_anchor_network(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_AGENT_ID", "42")
    monkeypatch.setenv("SOLVENT_BSC_NETWORK", "bsc-mainnet")
    s = state(tmp_path)
    assert s["agent_id"] == 42
    assert s["anchor_network"] == "bsc-mainnet"


def test_state_exposes_x402_guardrails(tmp_path):
    from solvent.kernel.rules import RiskConfig

    cfg = RiskConfig()
    s = state(tmp_path)
    assert s["x402"]["session_budget_usd"] == cfg.x402_session_budget_usdc / 1e6
    assert s["x402"]["max_per_call_usd"] == cfg.x402_max_per_call_usdc / 1e6


def test_state_anchors_sorted_newest_first(tmp_path):
    (tmp_path / "anchors.json").write_text(
        json.dumps(
            {
                "2026-06-22": {"head_hash": "0xaa", "tx_hash": "0x11"},
                "2026-06-24": {"head_hash": "0xcc", "tx_hash": "0x33"},
                "2026-06-23": {"head_hash": "0xbb", "tx_hash": "0x22"},
            }
        )
    )
    s = state(tmp_path)
    assert [a["day"] for a in s["anchors"]] == [
        "2026-06-24",
        "2026-06-23",
        "2026-06-22",
    ]
    assert s["anchors"][0]["tx_hash"] == "0x33"


def test_verify_reports_anchor_coverage_when_anchor_file_supplied(tmp_path):
    p = tmp_path / "receipts.jsonl"
    chain = ReceiptChain(p)
    first = chain.append(ts="2026-06-24T12:00:00+00:00", regime="risk-off")
    chain.append(ts="2026-06-24T13:00:00+00:00", regime="risk-on")
    anchors = tmp_path / "anchors.json"
    anchors.write_text(
        json.dumps(
            {
                "2026-06-24": {
                    "head_hash": first.hash,
                    "tx_hash": "0x" + "11" * 32,
                    "ts": "2026-06-24T23:55:00+00:00",
                }
            }
        )
    )

    out = verify(p, anchors)

    assert out["ok"] is True
    assert out["count"] == 2
    assert out["anchor_coverage"]["anchored_count"] == 1
    assert out["anchor_coverage"]["anchored_seq"] == first.seq
    assert out["anchor_coverage"]["unanchored_count"] == 1
    assert out["anchor_coverage"]["anchor_matches_local_log"] is True


def test_anchor_coverage_marks_unknown_anchor_head(tmp_path):
    p = tmp_path / "receipts.jsonl"
    chain = ReceiptChain(p)
    chain.append(ts="2026-06-24T12:00:00+00:00", regime="risk-off")
    entries = load_entries(p)

    out = anchor_coverage(
        entries,
        {
            "2026-06-24": {
                "head_hash": "0x" + "22" * 32,
                "tx_hash": "0x" + "11" * 32,
            }
        },
    )

    assert out["anchored_count"] == 0
    assert out["unanchored_count"] == 1
    assert out["anchor_matches_local_log"] is False


def test_policy_manifest_reads_published_manifest(tmp_path):
    (tmp_path / "policy-manifest.json").write_text(
        json.dumps({"manifest_hash": "0x" + "ab" * 32, "manifest": {}})
    )

    out = policy_manifest(tmp_path)

    assert out["manifest_hash"] == "0x" + "ab" * 32


def test_policy_manifest_requires_published_manifest(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="no policy manifest"):
        policy_manifest(tmp_path)
