import base64
import json
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from bnbagent.signing import SigningPolicy
from bnbagent.wallets import EVMWalletProvider
from bnbagent.x402 import X402Signer

from solvent.signals.x402pay import (
    EIP3009_TYPE_FIELDS,
    PaymentOffer,
    X402Payer,
    choose_offer,
    parse_payment_required,
)

FIXTURE = Path(__file__).parent / "fixtures" / "payment_required.json"


def fixture_header() -> str:
    return base64.b64encode(FIXTURE.read_bytes()).decode()


def test_parse_live_challenge():
    offers = parse_payment_required(fixture_header())
    assert len(offers) == 6
    # Every offer in the live challenge costs exactly $0.01.
    for o in offers:
        assert o.cost_usd == pytest.approx(0.01)
    networks = {o.network for o in offers}
    assert networks == {"eip155:8453", "eip155:56"}


def test_choose_prefers_bsc_eip3009():
    offer = choose_offer(parse_payment_required(fixture_header()))
    assert offer.network == "eip155:56"
    assert offer.method == "eip3009"


def test_choose_falls_back_to_base():
    offers = [
        o
        for o in parse_payment_required(fixture_header())
        if not (o.network == "eip155:56" and o.method == "eip3009")
    ]
    offer = choose_offer(offers)
    assert offer.network == "eip155:8453"
    assert offer.method == "eip3009"


def test_choose_rejects_permit2_only():
    offers = [
        o for o in parse_payment_required(fixture_header()) if o.method != "eip3009"
    ]
    with pytest.raises(ValueError):
        choose_offer(offers)


# ── End-to-end signing with a throwaway key ───────────────────────────


def throwaway_payer(offer: PaymentOffer) -> tuple[X402Payer, str]:
    acct = Account.create()
    policy = SigningPolicy(
        domain_allowlist=frozenset({(offer.chain_id, offer.asset)}),
        primary_type_allowlist=frozenset({"TransferWithAuthorization"}),
    )
    wallet = EVMWalletProvider(
        password="test-only",
        private_key=acct.key.hex(),
        persist=False,
        signing_policy=policy,
    )
    signer = X402Signer(
        wallet,
        max_value_per_call={offer.asset: offer.amount},
        session_budget={offer.asset: offer.amount * 10},
    )
    return X402Payer(signer), acct.address


def test_payment_header_signature_recovers_wallet():
    offer = choose_offer(parse_payment_required(fixture_header()))
    payer, address = throwaway_payer(offer)

    header = payer.payment_header(offer, resource={"url": "X402_t", "description": "t"})
    payload = json.loads(base64.b64decode(header))

    assert payload["x402Version"] == 2
    assert payload["accepted"] == offer.raw
    auth = payload["payload"]["authorization"]
    assert auth["from"].lower() == address.lower()
    assert auth["to"].lower() == offer.pay_to.lower()
    assert auth["value"] == str(offer.amount)

    # Recover the EIP-712 signer from the header exactly as a verifier would.
    signable = encode_typed_data(
        domain_data={
            "name": offer.token_name,
            "version": offer.token_version,
            "chainId": offer.chain_id,
            "verifyingContract": offer.asset,
        },
        message_types={"TransferWithAuthorization": EIP3009_TYPE_FIELDS},
        message_data={
            "from": auth["from"],
            "to": auth["to"],
            "value": int(auth["value"]),
            "validAfter": int(auth["validAfter"]),
            "validBefore": int(auth["validBefore"]),
            "nonce": auth["nonce"],
        },
    )
    recovered = Account.recover_message(
        signable, signature=payload["payload"]["signature"]
    )
    assert recovered.lower() == address.lower()


def test_session_budget_exhausts():
    from bnbagent.x402.errors import X402BudgetExhaustedError

    offer = choose_offer(parse_payment_required(fixture_header()))
    acct = Account.create()
    policy = SigningPolicy(
        domain_allowlist=frozenset({(offer.chain_id, offer.asset)}),
        primary_type_allowlist=frozenset({"TransferWithAuthorization"}),
    )
    wallet = EVMWalletProvider(
        password="test-only",
        private_key=acct.key.hex(),
        persist=False,
        signing_policy=policy,
    )
    signer = X402Signer(
        wallet,
        max_value_per_call={offer.asset: offer.amount},
        session_budget={offer.asset: offer.amount * 2},  # 2 calls only
    )
    payer = X402Payer(signer)
    payer.payment_header(offer, None)
    payer.payment_header(offer, None)
    with pytest.raises(X402BudgetExhaustedError):
        payer.payment_header(offer, None)


def test_tampered_recipient_refused():
    from bnbagent.x402.errors import X402RecipientMismatchError

    offers = parse_payment_required(fixture_header())
    offer = choose_offer(offers)
    payer, _ = throwaway_payer(offer)
    evil = PaymentOffer(
        **{
            **offer.__dict__,
            "pay_to": "0x000000000000000000000000000000000000dEaD",
        }
    )
    # X402Signer pins expected_to to the offer's payTo; a swapped recipient
    # inside the signer call must be refused. Simulate by signing the evil
    # offer but asserting the guard path exists: recipient comes from the
    # same offer object, so craft a mismatch via expected_to directly.
    with pytest.raises(X402RecipientMismatchError):
        payer._signer.sign_payment(
            domain={
                "name": evil.token_name,
                "version": evil.token_version,
                "chainId": evil.chain_id,
                "verifyingContract": evil.asset,
            },
            types={"TransferWithAuthorization": EIP3009_TYPE_FIELDS},
            message={
                "from": payer._signer.wallet_address,
                "to": evil.pay_to,
                "value": evil.amount,
                "validAfter": 0,
                "validBefore": 10,
                "nonce": "0x" + "11" * 32,
            },
            expected_to=offer.pay_to,  # the real recipient
        )
