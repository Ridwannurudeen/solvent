from datetime import datetime, timezone

from eth_account import Account
from eth_account.messages import encode_defunct

from solvent.policy.manifest import (
    SCHEMA,
    SIGNING_PREFIX,
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
    assert first["manifest"]["execution"]["result_outcomes"] == [
        "executed_now",
        "already_confirmed",
        "unresolved",
        "failed",
    ]
    assert first["manifest_hash"].startswith("0x")


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
