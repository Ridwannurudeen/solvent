import pytest

from solvent.receipts.pretrade import (
    PreTradeAnchorPublisher,
    build_pretrade_publisher,
    pretrade_anchor_key,
)


def test_pretrade_anchor_key_is_stable_and_namespaced():
    key = pretrade_anchor_key("20260624T12", "intent-key")

    assert key.startswith("solvent:pretrade:20260624T12:")
    assert key == pretrade_anchor_key("20260624T12", "intent-key")
    assert key != pretrade_anchor_key("20260624T12", "other")


def test_pretrade_publisher_sets_metadata():
    class Registry:
        def __init__(self):
            self.calls = []

        def set_metadata(self, agent_id, key, value):
            self.calls.append((agent_id, key, value))
            return {"transactionHash": "0xabc"}

    registry = Registry()
    publisher = PreTradeAnchorPublisher(registry, agent_id=7)
    tx_hash = publisher.publish(cycle_id="c1", intent_key="k1", commit_hash="0x123")

    assert tx_hash == "0xabc"
    assert registry.calls[0][0] == 7
    assert registry.calls[0][1].startswith("solvent:pretrade:c1:")
    assert registry.calls[0][2] == "0x123"


def test_build_pretrade_publisher_default_is_disabled(monkeypatch):
    monkeypatch.delenv("SOLVENT_PRETRADE_ANCHOR", raising=False)
    assert build_pretrade_publisher() is None


def test_build_pretrade_publisher_requires_agent_id(monkeypatch):
    monkeypatch.setenv("SOLVENT_PRETRADE_ANCHOR", "1")
    monkeypatch.delenv("SOLVENT_AGENT_ID", raising=False)
    with pytest.raises(SystemExit, match="SOLVENT_AGENT_ID"):
        build_pretrade_publisher()
