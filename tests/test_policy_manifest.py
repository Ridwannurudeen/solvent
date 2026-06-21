from datetime import datetime, timezone

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from solvent.policy.manifest import (
    POLICY_ANCHOR_KEY,
    SCHEMA,
    SIGNING_PREFIX,
    anchor_policy_manifest,
    build_policy_manifest,
    sign_policy_manifest,
)


def test_policy_manifest_hash_is_stable_for_same_inputs():
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    env = {
        "SOLVENT_AGENT_ID": "136384",
        "SOLVENT_WALLET_ADDRESS": "0x" + "12" * 20,
        "SOLVENT_PRETRADE_ANCHOR": "1",
    }

    first = build_policy_manifest(
        profile="safety", generated_at=now, git_commit="abc123", env=env
    )
    second = build_policy_manifest(
        profile="safety", generated_at=now, git_commit="abc123", env=env
    )

    assert first == second
    assert first["manifest"]["schema"] == SCHEMA
    assert first["manifest"]["strategy"]["profile"] == "safety"
    assert first["manifest"]["execution"]["chain"] == "bsc-mainnet"
    assert first["manifest"]["execution"]["twak_chain"] == "bsc"
    assert first["manifest"]["execution"]["result_outcomes"] == [
        "executed_now",
        "already_confirmed",
        "unresolved",
        "failed",
    ]
    assert first["manifest"]["data"]["secondary_provider"] == (
        "Binance public REST price cross-check"
    )
    assert (
        "post-trade balance deltas in expected direction"
        in first["manifest"]["execution"]["settlement_verification"]
    )
    assert first["manifest"]["emergency"]["persistent_runtime_halt"] is True
    assert first["manifest_hash"].startswith("0x")


def test_policy_manifest_derives_testnet_twak_chain():
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)

    payload = build_policy_manifest(
        profile="safety",
        generated_at=now,
        git_commit="abc123",
        env={"SOLVENT_TRADE_NETWORK": "bsc-testnet"},
    )

    assert payload["manifest"]["execution"]["chain"] == "bsc-testnet"
    assert payload["manifest"]["execution"]["twak_chain"] == "bsctestnet"


def test_policy_manifest_rejects_trade_network_twak_chain_mismatch():
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="does not match"):
        build_policy_manifest(
            profile="safety",
            generated_at=now,
            git_commit="abc123",
            env={"SOLVENT_TRADE_NETWORK": "bsc-testnet", "SOLVENT_TWAK_CHAIN": "bsc"},
        )


def test_policy_manifest_signature_recovers_wallet():
    acct = Account.create()
    manifest_hash = "0x" + "ab" * 32

    signature = sign_policy_manifest(manifest_hash, acct.key.hex())

    recovered = Account.recover_message(
        encode_defunct(text=f"{SIGNING_PREFIX}\n{manifest_hash}"),
        signature=signature["signature"],
    )
    assert signature["signer"].lower() == acct.address.lower()
    assert recovered.lower() == acct.address.lower()


def test_policy_manifest_anchor_uses_stable_erc8004_key():
    calls = []

    class Registry:
        def set_metadata(self, agent_id, key, value):
            calls.append((agent_id, key, value))
            return {"transactionHash": "0x" + "12" * 32}

    manifest_hash = "0x" + "ab" * 32
    out = anchor_policy_manifest(Registry(), 136384, manifest_hash)

    assert calls == [(136384, POLICY_ANCHOR_KEY, manifest_hash)]
    assert out["key"] == POLICY_ANCHOR_KEY
    assert out["tx_hash"] == "0x" + "12" * 32
