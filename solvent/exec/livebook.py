"""Live on-chain holdings — what the agent actually owns, read each cycle.

The barbell invariant is that the agent ever holds only floor stables plus
at most one sleeve token, so a cycle's holdings are the balances of the five
floor stables and the current sleeve position symbol. This is read-only —
balanceOf / decimals calls through bnbagent's MinimalERC20Client, no signing,
no value movement. Decimals are cached (they never change); on BSC they vary
per token (USDT/USDC 18, DOGE 8, FLOKI 9, ...), so we divide by the token's
own decimals rather than assuming 18.
"""

from bnbagent.config import resolve_network
from bnbagent.erc20 import MinimalERC20Client
from web3 import Web3

from ..kernel.allowlist import ADDRESSES, FLOOR_SYMBOLS

# Sub-dust balances (rounding noise) are dropped from the snapshot.
DUST = 1e-9


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

    def snapshot(self, position_symbol: str | None = None) -> dict[str, float]:
        """Current holdings: floor stables + the open sleeve token, if any.

        A symbol with no pinned contract address is skipped (it cannot be
        held through this agent), and sub-dust balances are dropped.
        """
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
