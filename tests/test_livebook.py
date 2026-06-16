from solvent.exec.livebook import LiveBook
from solvent.kernel.allowlist import ADDRESSES

# A real checksum address (Web3.to_checksum_address validates it offline).
WALLET = "0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d"


class FakeERC20:
    """Stand-in for MinimalERC20Client: fixed raw balance + decimals."""

    def __init__(self, web3, token_address, raw_by_addr, dec_by_addr):
        self.token_address = token_address
        self._raw = raw_by_addr
        self._dec = dec_by_addr
        self.balance_calls = 0
        self.decimals_calls = 0

    def balance_of(self, account):
        self.balance_calls += 1
        return self._raw.get(self.token_address, 0)

    def decimals(self):
        self.decimals_calls += 1
        return self._dec.get(self.token_address, 18)


def make_book(raw_by_addr, dec_by_addr):
    created = []

    def factory(web3, token_address):
        c = FakeERC20(web3, token_address, raw_by_addr, dec_by_addr)
        created.append(c)
        return c

    book = LiveBook(web3=None, wallet_address=WALLET, client_factory=factory)
    return book, created


def test_snapshot_floor_only():
    raw = {ADDRESSES["USDT"]: 240 * 10**18, ADDRESSES["USDC"]: 2 * 10**18}
    dec = {ADDRESSES["USDT"]: 18, ADDRESSES["USDC"]: 18}
    book, _ = make_book(raw, dec)
    snap = book.snapshot()
    assert snap == {"USDT": 240.0, "USDC": 2.0}
    # FDUSD/DAI/USD1 had zero balance and are dropped.


def test_snapshot_includes_position_with_nondefault_decimals():
    # DOGE uses 8 decimals on BSC — must not be hard-coded to 18.
    raw = {ADDRESSES["USDT"]: 200 * 10**18, ADDRESSES["DOGE"]: 1500 * 10**8}
    dec = {ADDRESSES["USDT"]: 18, ADDRESSES["DOGE"]: 8}
    book, _ = make_book(raw, dec)
    snap = book.snapshot(position_symbol="DOGE")
    assert snap["USDT"] == 200.0
    assert snap["DOGE"] == 1500.0


def test_dust_balances_dropped():
    raw = {ADDRESSES["USDT"]: 5, ADDRESSES["USDC"]: 0}  # 5 wei of an 18-dec token
    dec = {ADDRESSES["USDT"]: 18, ADDRESSES["USDC"]: 18}
    book, _ = make_book(raw, dec)
    assert book.snapshot() == {}


def test_decimals_cached_across_calls():
    raw = {ADDRESSES["USDT"]: 100 * 10**18}
    dec = {ADDRESSES["USDT"]: 18}
    book, created = make_book(raw, dec)
    book.balance("USDT")
    book.balance("USDT")
    usdt_client = next(c for c in created if c.token_address == ADDRESSES["USDT"])
    assert usdt_client.decimals_calls == 1  # cached after first read
    assert usdt_client.balance_calls == 2


def test_unpinned_symbol_skipped():
    # TRX is held out (no pinned address) — snapshot must skip it, not crash.
    raw = {ADDRESSES["USDT"]: 50 * 10**18}
    dec = {ADDRESSES["USDT"]: 18}
    book, _ = make_book(raw, dec)
    snap = book.snapshot(position_symbol="TRX")
    assert snap == {"USDT": 50.0}
