"""Live on-chain holdings and settlement verification."""

import time
from collections.abc import Iterable

from bnbagent.config import resolve_network
from bnbagent.erc20 import MinimalERC20Client
from web3 import Web3

from ..kernel.allowlist import ADDRESSES, FLOOR_SYMBOLS, SLEEVE_SYMBOLS
from ..kernel.allocator import TradeIntent

# Sub-dust balances (rounding noise) are dropped from snapshots.
DUST = 1e-9
TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()


def make_web3(network: str) -> Web3:
    """Web3 bound to the network's RPC (bsc-mainnet / bsc-testnet)."""
    return Web3(Web3.HTTPProvider(resolve_network(network).rpc_url))


class LiveBook:
    """Reads the agent wallet's on-chain token balances as {symbol: units}."""

    def __init__(
        self, web3: Web3, wallet_address: str, client_factory=MinimalERC20Client
    ):
        self.w3 = web3
        self.account = Web3.to_checksum_address(wallet_address)
        self._factory = client_factory
        self._clients: dict[str, object] = {}
        self._decimals: dict[str, int] = {}

    def _client(self, symbol: str):
        if symbol not in self._clients:
            self._clients[symbol] = self._factory(self.w3, ADDRESSES[symbol])
        return self._clients[symbol]

    def _dec(self, symbol: str) -> int:
        if symbol not in self._decimals:
            self._decimals[symbol] = self._client(symbol).decimals()
        return self._decimals[symbol]

    def balance(self, symbol: str) -> float:
        """Human-unit balance of one token (raw balanceOf / 10**decimals)."""
        raw = self._client(symbol).balance_of(self.account)
        return raw / 10 ** self._dec(symbol)

    def balances(self, symbols: Iterable[str]) -> dict[str, float]:
        """Human-unit balances for pinned symbols only."""
        out: dict[str, float] = {}
        for sym in dict.fromkeys(symbols):
            if sym not in ADDRESSES:
                continue
            out[sym] = self.balance(sym)
        return out

    def snapshot(self, position_symbol: str | None = None) -> dict[str, float]:
        """Current holdings: floor stables + the open sleeve token, if any."""
        symbols = list(FLOOR_SYMBOLS)
        if position_symbol and position_symbol not in symbols:
            symbols.append(position_symbol)
        out: dict[str, float] = {}
        for sym in symbols:
            if sym not in ADDRESSES:
                continue
            bal = self.balance(sym)
            if bal > DUST:
                out[sym] = bal
        return out

    def snapshot_all(self) -> dict[str, float]:
        """Current holdings across every pinned executable token."""
        symbols = list(dict.fromkeys((*FLOOR_SYMBOLS, *SLEEVE_SYMBOLS, *ADDRESSES)))
        out: dict[str, float] = {}
        for sym in symbols:
            if sym not in ADDRESSES:
                continue
            bal = self.balance(sym)
            if bal > DUST:
                out[sym] = bal
        return out

    def transfer_summary(
        self, receipt: dict, symbols: Iterable[str]
    ) -> dict[str, dict]:
        """ERC-20 Transfer flow involving the wallet for each requested symbol."""
        wanted = {sym for sym in symbols if sym in ADDRESSES}
        by_address = {ADDRESSES[sym].lower(): sym for sym in wanted}
        out = {sym: {"in": 0.0, "out": 0.0} for sym in wanted}
        for log in receipt.get("logs", []):
            symbol = by_address.get(str(log.get("address", "")).lower())
            if symbol is None:
                continue
            topics = log.get("topics") or []
            if len(topics) < 3 or _clean_hex(topics[0]) != TRANSFER_TOPIC:
                continue
            from_addr = Web3.to_checksum_address(_topic_address(topics[1]))
            to_addr = Web3.to_checksum_address(_topic_address(topics[2]))
            units = _transfer_units(log.get("data"), self._dec(symbol))
            if from_addr == self.account:
                out[symbol]["out"] += units
            if to_addr == self.account:
                out[symbol]["in"] += units
        return out


class LiveReceiptVerifier:
    """Verifies mined TWAK swaps against status, logs, and balances."""

    def __init__(
        self,
        book: LiveBook,
        *,
        slippage_pct: float,
        timeout_s: int = 180,
        confirmations: int = 1,
    ) -> None:
        self.book = book
        self.slippage_pct = slippage_pct
        self.timeout_s = timeout_s
        self.confirmations = max(1, confirmations)

    def before(self, intent: TradeIntent) -> dict[str, float]:
        return self.book.balances((intent.from_symbol, intent.to_symbol))

    def __call__(
        self,
        intent: TradeIntent,
        tx_hash: str,
        before_balances: dict[str, float] | None = None,
    ) -> dict:
        receipt = self.book.w3.eth.wait_for_transaction_receipt(
            tx_hash, timeout=self.timeout_s
        )
        if receipt.get("status") != 1:
            raise RuntimeError(f"transaction reverted: {tx_hash}")
        self._wait_for_confirmations(receipt.get("blockNumber"))
        tx = self.book.w3.eth.get_transaction(tx_hash)
        sender = tx.get("from")
        if Web3.to_checksum_address(sender) != self.book.account:
            raise RuntimeError(
                f"transaction sender {sender} does not match {self.book.account}"
            )

        symbols = (intent.from_symbol, intent.to_symbol)
        after = self.before(intent)
        flows = self.book.transfer_summary(receipt, symbols)
        from_flow = flows.get(intent.from_symbol, {}).get("out", 0.0)
        to_flow = flows.get(intent.to_symbol, {}).get("in", 0.0)

        before = before_balances or {}
        from_delta = after.get(intent.from_symbol, 0.0) - before.get(
            intent.from_symbol, 0.0
        )
        to_delta = after.get(intent.to_symbol, 0.0) - before.get(intent.to_symbol, 0.0)

        if before and from_delta >= -DUST:
            raise RuntimeError(
                f"{intent.from_symbol} balance did not decrease after {tx_hash}"
            )
        if before and to_delta <= DUST:
            raise RuntimeError(
                f"{intent.to_symbol} balance did not increase after {tx_hash}"
            )
        if from_flow <= DUST:
            raise RuntimeError(
                f"no outgoing {intent.from_symbol} Transfer from wallet in {tx_hash}"
            )
        if to_flow <= DUST:
            raise RuntimeError(
                f"no incoming {intent.to_symbol} Transfer to wallet in {tx_hash}"
            )
        if intent.to_symbol in FLOOR_SYMBOLS:
            min_received = intent.notional_usd * (1.0 - 2.0 * self.slippage_pct / 100.0)
            observed = to_delta if before else to_flow
            if observed + DUST < min_received:
                raise RuntimeError(
                    f"{intent.to_symbol} received {observed:.8f} below "
                    f"minimum {min_received:.8f}"
                )

        return {
            "tx_hash": tx_hash,
            "block_number": receipt.get("blockNumber"),
            "status": receipt.get("status"),
            "confirmations": self.confirmations,
            "from_symbol": intent.from_symbol,
            "to_symbol": intent.to_symbol,
            "from_balance_delta": from_delta if before else None,
            "to_balance_delta": to_delta if before else None,
            "from_transfer_out": from_flow,
            "to_transfer_in": to_flow,
        }

    def _wait_for_confirmations(self, block_number: int | None) -> None:
        if block_number is None or self.confirmations <= 1:
            return
        while True:
            latest = self.book.w3.eth.block_number
            if latest - block_number + 1 >= self.confirmations:
                return
            time.sleep(2)


def _topic_hex(topic) -> str:
    if isinstance(topic, bytes):
        return "0x" + topic.hex()
    return str(topic)


def _clean_hex(topic) -> str:
    raw = _topic_hex(topic).lower()
    return raw[2:] if raw.startswith("0x") else raw


def _topic_address(topic) -> str:
    raw = _topic_hex(topic)
    if raw.startswith("0x"):
        raw = raw[2:]
    return "0x" + raw[-40:]


def _transfer_units(data, decimals: int) -> float:
    if isinstance(data, bytes):
        raw = int.from_bytes(data, "big")
    elif isinstance(data, int):
        raw = data
    else:
        text = str(data)
        raw = int(text, 16) if text.startswith("0x") else int(text)
    return raw / 10**decimals
