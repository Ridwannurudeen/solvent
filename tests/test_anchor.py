from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from solvent.receipts.anchor import (
    AnchorMarkers,
    TwakRegistry,
    _agent_id,
    _twak_chain,
    _tx_hash,
    anchor_key,
    run_anchor,
)
from solvent.receipts.chain import GENESIS_HASH, ReceiptChain

DAY = datetime(2026, 6, 24, 23, 30, tzinfo=timezone.utc)


class _Registry:
    """Fake ERC8004Agent — records the metadata write."""

    def __init__(self):
        self.calls = []

    def set_metadata(self, agent_id, key, value):
        self.calls.append((agent_id, key, value))
        return {"transactionHash": "0xdeadbeef", "success": True}


def _seeded_chain(tmp_path) -> ReceiptChain:
    chain = ReceiptChain(tmp_path / "receipts.jsonl")
    chain.append(ts="2026-06-24T12:00:00+00:00", regime="risk-off", equity_usd=300.0)
    return chain


def test_anchor_key():
    assert anchor_key("2026-06-24") == "solvent:anchor:2026-06-24"


def test_twak_chain_maps_repo_network_names():
    assert _twak_chain("bsc-mainnet") == "bsc"
    assert _twak_chain("bsc-testnet") == "bsctestnet"
    assert _twak_chain("bsc") == "bsc"
    assert _twak_chain("bsctestnet") == "bsctestnet"


def test_twak_output_helpers_read_nested_payloads():
    payload = {
        "data": {
            "agent": {"agent_id": "42"},
            "tx": {"transactionHash": "0xabc"},
        }
    }
    assert _agent_id(payload) == 42
    assert _tx_hash(payload) == "0xabc"


def test_twak_agent_id_requires_registered_id():
    with pytest.raises(RuntimeError, match="agent id"):
        _agent_id({"transactionHash": "0xabc"})


def test_twak_registry_register_agent_builds_mainnet_command():
    calls = []

    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout='{"data":{"agent":{"id":"9"},"tx":{"hash":"0x123"}}}',
            stderr="",
        )

    reg = TwakRegistry("bsc-mainnet", twak_bin="twak-test", runner=runner)
    out = reg.register_agent("https://solvent.gudman.xyz")

    assert calls == [
        (
            [
                "twak-test",
                "erc8004",
                "register",
                "--uri",
                "https://solvent.gudman.xyz",
                "--chain",
                "bsc",
                "--json",
            ],
            {"capture_output": True, "text": True, "timeout": 180},
        )
    ]
    assert out["agentId"] == 9
    assert out["transactionHash"] == "0x123"


def test_twak_registry_set_metadata_builds_command():
    calls = []

    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout='{"result":{"txHash":"0x456"}}',
            stderr="",
        )

    reg = TwakRegistry("bsc-testnet", twak_bin="twak-test", runner=runner)
    out = reg.set_metadata(9, "solvent:anchor:2026-06-24", "0xhead")

    assert calls == [
        (
            [
                "twak-test",
                "erc8004",
                "set-metadata",
                "9",
                "--key",
                "solvent:anchor:2026-06-24",
                "--value",
                "0xhead",
                "--chain",
                "bsctestnet",
                "--json",
            ],
            {"capture_output": True, "text": True, "timeout": 180},
        )
    ]
    assert out["transactionHash"] == "0x456"


def test_anchors_head(tmp_path):
    chain = _seeded_chain(tmp_path)
    reg = _Registry()
    markers = AnchorMarkers(tmp_path / "anchors.json")
    out = run_anchor(chain=chain, registry=reg, agent_id=7, markers=markers, now=DAY)
    assert out["action"] == "anchor"
    assert out["tx_hash"] == "0xdeadbeef"
    assert out["head_hash"] == chain.head_hash
    assert reg.calls == [(7, "solvent:anchor:2026-06-24", chain.head_hash)]
    assert markers.has("2026-06-24")


def test_skips_when_no_receipts(tmp_path):
    chain = ReceiptChain(tmp_path / "receipts.jsonl")
    assert chain.head_hash == GENESIS_HASH
    reg = _Registry()
    out = run_anchor(
        chain=chain,
        registry=reg,
        agent_id=7,
        markers=AnchorMarkers(tmp_path / "anchors.json"),
        now=DAY,
    )
    assert out["action"] == "none"
    assert reg.calls == []


def test_idempotent_same_day(tmp_path):
    chain = _seeded_chain(tmp_path)
    reg = _Registry()
    markers = AnchorMarkers(tmp_path / "anchors.json")
    run_anchor(chain=chain, registry=reg, agent_id=7, markers=markers, now=DAY)
    # Second run same day -> no second write.
    out = run_anchor(chain=chain, registry=reg, agent_id=7, markers=markers, now=DAY)
    assert out["action"] == "none"
    assert len(reg.calls) == 1


def test_markers_persist_across_instances(tmp_path):
    chain = _seeded_chain(tmp_path)
    reg = _Registry()
    path = tmp_path / "anchors.json"
    run_anchor(
        chain=chain, registry=reg, agent_id=7, markers=AnchorMarkers(path), now=DAY
    )
    # Fresh markers object reads the persisted record.
    out = run_anchor(
        chain=chain, registry=reg, agent_id=7, markers=AnchorMarkers(path), now=DAY
    )
    assert out["action"] == "none"
    assert len(reg.calls) == 1
