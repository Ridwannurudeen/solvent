from datetime import datetime, timezone

from solvent.brain.proof import build_inference_proof, sha256_json
from solvent.kernel.allocator import Regime
from solvent.kernel.state import MarketSignals


def _signals(score=2.0):
    return MarketSignals(
        fear_greed=62,
        btc_funding_rate=0.0001,
        momentum={"CAKE": score, "AAVE": 1.2},
        percent_change_1h={"CAKE": 1.5},
        volume_change_24h={"CAKE": 4.0},
        prices={"CAKE": 2.5},
        degraded=False,
    )


def test_inference_proof_hashes_are_stable():
    proof = build_inference_proof(
        cycle_id="20260624T12",
        signals=_signals(),
        deterministic_regime=Regime.RISK_ON,
        effective_regime=Regime.RISK_ON,
        active_risk_profile="safety",
        position_symbol=None,
        advice=None,
    )
    same = build_inference_proof(
        cycle_id="20260624T12",
        signals=_signals(),
        deterministic_regime=Regime.RISK_ON,
        effective_regime=Regime.RISK_ON,
        active_risk_profile="safety",
        position_symbol=None,
        advice=None,
    )

    assert proof["proof_hash"] == same["proof_hash"]
    assert proof["commitment_hash"] == proof["proof_hash"]
    assert proof["proof_type"] == "hash_commitment"
    assert proof["input_hash"] == sha256_json(proof["input"])
    assert proof["output_hash"] == sha256_json(proof["output"])
    assert proof["mode"] == "deterministic"


def test_inference_proof_changes_when_inputs_change():
    base = build_inference_proof(
        cycle_id="20260624T12",
        signals=_signals(),
        deterministic_regime=Regime.RISK_ON,
        effective_regime=Regime.RISK_ON,
        active_risk_profile="safety",
        position_symbol=None,
        advice=None,
    )
    changed = build_inference_proof(
        cycle_id="20260624T12",
        signals=_signals(score=4.0),
        deterministic_regime=Regime.RISK_ON,
        effective_regime=Regime.RISK_ON,
        active_risk_profile="safety",
        position_symbol=None,
        advice=None,
    )

    assert base["input_hash"] != changed["input_hash"]
    assert base["proof_hash"] != changed["proof_hash"]


def test_engine_receipt_contains_inference_proof(tmp_path):
    from solvent.engine import StateStore, run_cycle
    from solvent.exec.executor import Journal, PaperExecutor
    from solvent.kernel.rules import RiskConfig
    from solvent.receipts.chain import DataPurchase, ReceiptChain

    class Source:
        def fetch(self):
            return _signals(), [DataPurchase(tool="test", cost_usdc=0.0, ok=True)]

    run_cycle(
        source=Source(),
        executor=PaperExecutor(Journal(tmp_path / "journal.jsonl")),
        journal=Journal(tmp_path / "journal.jsonl"),
        receipts=ReceiptChain(tmp_path / "receipts.jsonl"),
        store=StateStore(tmp_path / "state.json"),
        holdings={"USDT": 300.0},
        cfg=RiskConfig(),
        now=datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc),
    )

    import json

    receipt = json.loads((tmp_path / "receipts.jsonl").read_text().splitlines()[-1])[
        "receipt"
    ]
    assert receipt["inference_proof"]["schema"] == "solvent.inference-commitment.v1"
    assert receipt["inference_proof"]["proof_hash"].startswith("0x")
