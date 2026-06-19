"""x402 payment client for the CMC AI Agent Hub MCP server.

Implements the x402 v2 protocol (verified against
docs/x402-spec-v2.md and the live PAYMENT-REQUIRED challenge captured in
tests/fixtures/payment_required.json):

  POST tools/call -> 402 + PAYMENT-REQUIRED header (base64 PaymentRequired)
  -> pick an offer -> sign EIP-3009 TransferWithAuthorization (EIP-712)
  -> retry with PAYMENT-SIGNATURE header (base64 PaymentPayload) -> data.

Signing goes through bnbagent's X402Signer, which pins the recipient,
caps per-call value, and tracks a session budget — the agent's
"information budget" is enforced here, not in prose.

The live CMC challenge offers payment on Base USDC (6 decimals) and on
BSC in USDC / United Stables / USD1 (18 decimals). We prefer EIP-3009
offers on BSC so the agent's entire economic life stays on one chain.
"""

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from bnbagent.x402 import X402Signer

from ..receipts.chain import DataPurchase

# Token decimals by (network, asset) from the captured challenge:
# amount "10000" on Base USDC vs "10000000000000000" on the BSC tokens,
# both = $0.01.
TOKEN_DECIMALS = {
    ("eip155:8453", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"): 6,
    ("eip155:56", "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d"): 18,
    ("eip155:56", "0xcE24439F2D9C6a2289F741120FE202248B666666"): 18,
    ("eip155:56", "0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d"): 18,
}
BSC_USD1 = "0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d"
BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BSC_UNITED_STABLES = "0xcE24439F2D9C6a2289F741120FE202248B666666"

EIP3009_TYPE_FIELDS = [
    {"name": "from", "type": "address"},
    {"name": "to", "type": "address"},
    {"name": "value", "type": "uint256"},
    {"name": "validAfter", "type": "uint256"},
    {"name": "validBefore", "type": "uint256"},
    {"name": "nonce", "type": "bytes32"},
]


def _canonical_response(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _response_commitment(payload: dict) -> tuple[str, int]:
    canonical = _canonical_response(payload)
    return "0x" + hashlib.sha256(canonical.encode()).hexdigest(), len(
        canonical.encode()
    )


@dataclass(frozen=True)
class PaymentOffer:
    scheme: str
    network: str  # CAIP-2, e.g. "eip155:56"
    asset: str
    amount: int
    pay_to: str
    max_timeout_seconds: int
    token_name: str
    token_version: str
    method: str  # "eip3009" | "permit2-exact"
    raw: dict  # verbatim requirements object, echoed back as `accepted`

    @property
    def chain_id(self) -> int:
        return int(self.network.split(":")[1])

    @property
    def cost_usd(self) -> float:
        dec = TOKEN_DECIMALS.get((self.network, self.asset))
        if dec is None:
            return float("nan")
        return self.amount / 10**dec


def parse_payment_required(header_b64: str) -> list[PaymentOffer]:
    data = json.loads(base64.b64decode(header_b64))
    offers = []
    for req in data.get("accepts", []):
        extra = req.get("extra", {})
        offers.append(
            PaymentOffer(
                scheme=req["scheme"],
                network=req["network"],
                asset=req["asset"],
                amount=int(req["amount"]),
                pay_to=req["payTo"],
                max_timeout_seconds=int(req.get("maxTimeoutSeconds", 30)),
                token_name=extra.get("name", ""),
                token_version=extra.get("version", "1"),
                method=extra.get("assetTransferMethod", "eip3009"),
                raw=req,
            )
        )
    return offers


def choose_offer(
    offers: list[PaymentOffer],
    preferred_networks: tuple[str, ...] = ("eip155:56", "eip155:8453"),
    preferred_assets: tuple[str, ...] = (BSC_USD1, BASE_USDC, BSC_UNITED_STABLES),
) -> PaymentOffer:
    """First EIP-3009 offer in preferred-network/asset order.

    permit2-exact requires an on-chain Permit2 approval step; eip3009 is
    a pure signature, so it is the only method we implement.
    """
    for net in preferred_networks:
        network_offers = [
            offer
            for offer in offers
            if offer.network == net and offer.method == "eip3009"
        ]
        for asset in preferred_assets:
            for offer in network_offers:
                if offer.asset.lower() == asset.lower():
                    return offer
        if network_offers:
            return network_offers[0]
    raise ValueError(
        f"no eip3009 offer on preferred networks; got "
        f"{[(o.network, o.method) for o in offers]}"
    )


class X402Payer:
    """Builds PAYMENT-SIGNATURE headers via a guarded X402Signer."""

    def __init__(self, signer: X402Signer) -> None:
        self._signer = signer

    def payment_header(self, offer: PaymentOffer, resource: dict | None) -> str:
        now = int(time.time())
        authorization = {
            "from": self._signer.wallet_address,
            "to": offer.pay_to,
            "value": offer.amount,
            "validAfter": now - 60,
            "validBefore": now + offer.max_timeout_seconds,
            "nonce": "0x" + secrets.token_bytes(32).hex(),
        }
        domain = {
            "name": offer.token_name,
            "version": offer.token_version,
            "chainId": offer.chain_id,
            "verifyingContract": offer.asset,
        }
        types = {"TransferWithAuthorization": EIP3009_TYPE_FIELDS}
        signed = self._signer.sign_payment(
            domain=domain,
            types=types,
            message=authorization,
            expected_to=offer.pay_to,
        )
        # bnbagent returns the eth_account HexBytes signature; normalize to 0x-hex.
        signature_hex = "0x" + bytes(signed["signature"]).hex()
        payload = {
            "x402Version": 2,
            "accepted": offer.raw,
            "payload": {
                "signature": signature_hex,
                "authorization": {
                    **{k: str(v) for k, v in authorization.items() if k != "nonce"},
                    "nonce": authorization["nonce"],
                },
            },
            "extensions": {},
        }
        if resource:
            payload["resource"] = resource
        return base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()


class SpendLedger:
    """Durable x402 spend guard shared across one-shot process restarts."""

    def __init__(self, path: Path, *, daily_budget_usd: float) -> None:
        self.path = path
        self.daily_budget_usd = daily_budget_usd

    def _entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]

    def spent_on(self, day: str) -> float:
        return sum(
            float(entry.get("cost_usd") or 0.0)
            for entry in self._entries()
            if str(entry.get("ts", ""))[:10] == day
        )

    def authorize(self, tool: str, offer: PaymentOffer) -> None:
        now = datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        projected = self.spent_on(day) + offer.cost_usd
        if projected > self.daily_budget_usd:
            raise ValueError(
                f"x402 daily budget exceeded: projected ${projected:.4f} "
                f"> ${self.daily_budget_usd:.4f}"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": now.isoformat(),
            "tool": tool,
            "network": offer.network,
            "asset": offer.asset,
            "amount": offer.amount,
            "cost_usd": offer.cost_usd,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())


class X402MCPClient:
    """MCP tools/call client that pays per request.

    `payer=None` runs unpaid (returns the 402 as an error) — used by
    paper mode and tests.
    """

    def __init__(
        self,
        url: str = "https://mcp.coinmarketcap.com/x402/mcp",
        payer: X402Payer | None = None,
        spend_ledger: SpendLedger | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.url = url
        self.payer = payer
        self.spend_ledger = spend_ledger
        self._http = httpx.Client(timeout=timeout)
        self._id = 0

    def _post(self, body: dict, headers: dict | None = None) -> httpx.Response:
        base = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if headers:
            base.update(headers)
        return self._http.post(self.url, json=body, headers=base)

    def call_tool(
        self, name: str, arguments: dict | None = None
    ) -> tuple[dict | None, DataPurchase]:
        self._id += 1
        body = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        resp = self._post(body)
        cost = 0.0
        if resp.status_code == 402:
            if self.payer is None:
                return None, DataPurchase(tool=name, cost_usdc=0.0, ok=False)
            challenge = resp.headers.get("PAYMENT-REQUIRED", "")
            offer = choose_offer(parse_payment_required(challenge))
            if self.spend_ledger is not None:
                self.spend_ledger.authorize(name, offer)
            header = self.payer.payment_header(
                offer, resource={"url": f"X402_{name}", "description": name}
            )
            resp = self._post(body, headers={"PAYMENT-SIGNATURE": header})
            cost = offer.cost_usd
        ok = resp.status_code == 200
        response_payload = resp.json() if ok else {}
        response_hash, response_bytes = (
            _response_commitment(response_payload) if response_payload else (None, 0)
        )
        result = response_payload.get("result") if ok else None
        if isinstance(result, dict) and result.get("isError"):
            ok = False
        purchase = DataPurchase(
            tool=name,
            cost_usdc=cost if ok else 0.0,
            ok=ok,
            response_hash=response_hash,
            response_bytes=response_bytes,
        )
        if not ok:
            return None, purchase
        return result, purchase
