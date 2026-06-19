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
    BSC_USD1,
    EIP3009_TYPE_FIELDS,
    PaymentOffer,
    SpendLedger,
    X402MCPClient,
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
    assert offer.asset == BSC_USD1


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


class _FakeResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}

    def json(self):
        return self._body


class _DummyPayer:
    def payment_header(self, offer, resource):
        return "paid"


class _ToolErrorClient(X402MCPClient):
    def __init__(self):
        super().__init__(payer=_DummyPayer())
        self._responses = [
            _FakeResponse(402, headers={"PAYMENT-REQUIRED": fixture_header()}),
            _FakeResponse(200, {"result": {"content": [], "isError": True}}),
        ]

    def _post(self, body, headers=None):
        return self._responses.pop(0)


class _PaidClient(X402MCPClient):
    def __init__(self, ledger):
        super().__init__(payer=_DummyPayer(), spend_ledger=ledger)
        self._responses = [
            _FakeResponse(402, headers={"PAYMENT-REQUIRED": fixture_header()}),
            _FakeResponse(200, {"result": {"content": [{"text": "ok"}]}}),
        ]

    def _post(self, body, headers=None):
        return self._responses.pop(0)


def test_paid_tool_level_error_is_failed_purchase():
    result, purchase = _ToolErrorClient().call_tool("get_global_metrics_latest", {})

    assert result is None
    assert purchase.tool == "get_global_metrics_latest"
    assert purchase.ok is False
    assert purchase.cost_usdc == 0.0


def test_spend_ledger_persists_daily_authorizations(tmp_path):
    ledger = SpendLedger(tmp_path / "x402-spend.jsonl", daily_budget_usd=0.02)

    result, purchase = _PaidClient(ledger).call_tool("get_global_metrics_latest", {})

    assert result is not None
    assert purchase.ok is True
    assert purchase.cost_usdc == pytest.approx(0.01)
    assert purchase.response_hash and purchase.response_hash.startswith("0x")
    assert purchase.response_bytes > 0
    entry = json.loads((tmp_path / "x402-spend.jsonl").read_text().splitlines()[0])
    assert ledger.spent_on(entry["ts"][:10]) == pytest.approx(0.01)


def test_spend_ledger_refuses_restart_budget_overrun(tmp_path):
    ledger = SpendLedger(tmp_path / "x402-spend.jsonl", daily_budget_usd=0.015)
    _PaidClient(ledger).call_tool("get_global_metrics_latest", {})

    with pytest.raises(ValueError, match="x402 daily budget exceeded"):
        _PaidClient(ledger).call_tool("get_crypto_quotes_latest", {})


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
