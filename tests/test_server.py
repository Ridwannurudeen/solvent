import json
from datetime import datetime, timezone

from solvent.receipts.chain import ReceiptChain
from solvent.receipts.server import load_entries, state, summary, verify


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
    assert s["holdings"]["USDT"] == 240.0
    assert s["alive"] is True
    assert s["heartbeat_age_s"] is not None and s["heartbeat_age_s"] < 60


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
