import json
from datetime import datetime, timezone

from solvent.ops.watcher import append_attestation, public_attestation


NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


def _fetcher(url):
    if url.endswith("/verify"):
        return 200, json.dumps(
            {
                "ok": True,
                "count": 3,
                "head_hash": "0x" + "11" * 32,
                "anchor_coverage": {
                    "anchor_matches_local_log": True,
                    "unanchored_count": 0,
                },
            }
        )
    if url.endswith("/state"):
        return 200, json.dumps(
            {
                "alive": True,
                "agent_id": 136384,
                "runtime_status": "ACTIVE",
            }
        )
    if url.endswith("/policy"):
        return 200, json.dumps({"manifest_hash": "0x" + "22" * 32})
    if url.endswith("/policy-compliance"):
        return 200, json.dumps({"schema": "solvent.policy-compliance.v1", "ok": True})
    if url.endswith("/signal"):
        return 200, json.dumps({"signal_hash": "0x" + "33" * 32})
    if url.endswith("/inference-verification"):
        return 200, json.dumps(
            {
                "schema": "solvent.inference-verification-log.v1",
                "ok": True,
                "verified_count": 7,
            }
        )
    if url.endswith("/strategy-evidence"):
        return 200, json.dumps(
            {
                "schema": "solvent.strategy-evidence.v1",
                "edge_claim": {"guaranteed": False},
                "scenarios": {"uptrend": {}, "crash": {}},
            }
        )
    raise AssertionError(url)


def test_public_attestation_hash_binds_observed_evidence():
    attestation = public_attestation(
        public_base="https://example.test", fetcher=_fetcher, now=NOW
    )

    assert attestation["ok"] is True
    assert attestation["evidence"]["receipt_count"] == 3
    assert attestation["checks"]["inference_reexecution_ok"] is True
    assert attestation["checks"]["strategy_claims_no_guarantee"] is True
    assert attestation["evidence"]["inference_verified_count"] == 7
    assert attestation["evidence"]["strategy_scenarios"] == ["crash", "uptrend"]
    assert attestation["attestation_hash"].startswith("0x")


def test_append_attestation_writes_jsonl(tmp_path):
    path = tmp_path / "attestations.jsonl"
    attestation = public_attestation(
        public_base="https://example.test", fetcher=_fetcher, now=NOW
    )

    append_attestation(path, attestation)

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows == [attestation]
