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
    BASE_USDC,
    BSC_USD1,
    MAX_PAYMENT_TIMEOUT_SECONDS,
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


def tampered_header(**changes) -> str:
    payload = json.loads(FIXTURE.read_text())
    payload["accepts"][0].update(changes)
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_parse_live_challenge():
    offers = parse_payment_required(fixture_header())
    assert len(offers) == 6
    # Every offer in the live challenge costs exactly $0.01.
    for o in offers:
        assert o.cost_usd == pytest.approx(0.01)
    networks = {o.network for o in offers}
    assert networks == {"eip155:8453", "eip155:56"}


def test_choose_prefers_base_usdc():
    # Only the Base USDC rail settles through CMC's facilitator; the BSC
    # EIP-3009 rails return "X402 submit error", so Base USDC is preferred.
    offer = choose_offer(parse_payment_required(fixture_header()))
    assert offer.network == "eip155:8453"
    assert offer.method == "eip3009"
    assert offer.asset == BASE_USDC


def test_choose_falls_back_to_bsc():
    offers = [
        o
        for o in parse_payment_required(fixture_header())
        if not (o.network == "eip155:8453" and o.method == "eip3009")
    ]
    offer = choose_offer(offers)
    assert offer.network == "eip155:56"
    assert offer.method == "eip3009"
    assert offer.asset == BSC_USD1


def test_choose_rejects_permit2_only():
    offers = [
        o for o in parse_payment_required(fixture_header()) if o.method != "eip3009"
    ]
    with pytest.raises(ValueError):
        choose_offer(offers)


def test_choose_rejects_unknown_assets_on_preferred_network():
    offer = PaymentOffer(
        scheme="exact",
        network="eip155:8453",
        asset="0x0000000000000000000000000000000000000001",
        amount=10_000,
        pay_to="0x3C5f3a6cE224BB89D72f5EB4232ecC27F67B3eeA",
        max_timeout_seconds=30,
        token_name="Unknown",
        token_version="1",
        method="eip3009",
        raw={},
    )

    with pytest.raises(ValueError, match="no eip3009 offer"):
        choose_offer([offer])


def test_unknown_asset_cost_is_refused():
    offer = PaymentOffer(
        scheme="exact",
        network="eip155:8453",
        asset="0x0000000000000000000000000000000000000001",
        amount=10_000,
        pay_to="0x3C5f3a6cE224BB89D72f5EB4232ecC27F67B3eeA",
        max_timeout_seconds=30,
        token_name="Unknown",
        token_version="1",
        method="eip3009",
        raw={},
    )

    with pytest.raises(ValueError, match="unsupported x402 asset"):
        _ = offer.cost_usd


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


class _BadPayeeClient(X402MCPClient):
    def __init__(self, ledger):
        super().__init__(payer=_DummyPayer(), spend_ledger=ledger)
        self._responses = [
            _FakeResponse(
                402,
                headers={
                    "PAYMENT-REQUIRED": tampered_header(
                        payTo="0x000000000000000000000000000000000000dEaD"
                    )
                },
            ),
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


def test_bad_payee_refused_before_ledger_write(tmp_path):
    ledger = SpendLedger(tmp_path / "x402-spend.jsonl", daily_budget_usd=1.0)

    with pytest.raises(ValueError, match="x402 payee mismatch"):
        _BadPayeeClient(ledger).call_tool("get_global_metrics_latest", {})

    assert not ledger.path.exists()


def test_spend_ledger_refuses_restart_budget_overrun(tmp_path):
    ledger = SpendLedger(tmp_path / "x402-spend.jsonl", daily_budget_usd=0.015)
    _PaidClient(ledger).call_tool("get_global_metrics_latest", {})

    with pytest.raises(ValueError, match="x402 daily budget exceeded"):
        _PaidClient(ledger).call_tool("get_crypto_quotes_latest", {})


def test_spend_ledger_refuses_unknown_asset_without_writing(tmp_path):
    ledger = SpendLedger(tmp_path / "x402-spend.jsonl", daily_budget_usd=1.0)
    offer = PaymentOffer(
        scheme="exact",
        network="eip155:8453",
        asset="0x0000000000000000000000000000000000000001",
        amount=10_000,
        pay_to="0x3C5f3a6cE224BB89D72f5EB4232ecC27F67B3eeA",
        max_timeout_seconds=30,
        token_name="Unknown",
        token_version="1",
        method="eip3009",
        raw={},
    )

    with pytest.raises(ValueError, match="unsupported x402 asset"):
        ledger.authorize("get_crypto_quotes_latest", offer)

    assert not ledger.path.exists()


# ── End-to-end signing with a throwaway key ───────────────────────────


def throwaway_payer(offer: PaymentOffer) -> tuple[X402Payer, str]:
    acct = Account.create()
    policy = SigningPolicy(
        domain_allowlist=frozenset({(offer.chain_id, offer.asset)}),
        primary_type_allowlist=frozenset({"TransferWithAuthorization"}),
        validity_required_primary_types=frozenset({"TransferWithAuthorization"}),
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


def test_payment_header_caps_challenge_timeout():
    offer = choose_offer(parse_payment_required(fixture_header()))
    payer, _ = throwaway_payer(offer)
    slow_offer = PaymentOffer(
        **{
            **offer.__dict__,
            "max_timeout_seconds": MAX_PAYMENT_TIMEOUT_SECONDS * 10,
        }
    )

    header = payer.payment_header(slow_offer, None)
    payload = json.loads(base64.b64decode(header))
    auth = payload["payload"]["authorization"]

    assert int(auth["validBefore"]) - int(auth["validAfter"]) <= (
        MAX_PAYMENT_TIMEOUT_SECONDS + 60
    )


def test_session_budget_exhausts():
    from bnbagent.x402.errors import X402BudgetExhaustedError

    offer = choose_offer(parse_payment_required(fixture_header()))
    acct = Account.create()
    policy = SigningPolicy(
        domain_allowlist=frozenset({(offer.chain_id, offer.asset)}),
        primary_type_allowlist=frozenset({"TransferWithAuthorization"}),
        validity_required_primary_types=frozenset({"TransferWithAuthorization"}),
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
    offers = parse_payment_required(fixture_header())
    offer = choose_offer(offers)
    payer, _ = throwaway_payer(offer)
    evil = PaymentOffer(
        **{
            **offer.__dict__,
            "pay_to": "0x000000000000000000000000000000000000dEaD",
        }
    )
    with pytest.raises(ValueError, match="x402 payee mismatch"):
        payer.payment_header(evil, None)
