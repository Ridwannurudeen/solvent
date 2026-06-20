import pytest

from solvent.exec.livebook import LiveBook, LiveReceiptVerifier, TRANSFER_TOPIC
from solvent.kernel.allocator import IntentKind, TradeIntent
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


def test_snapshot_all_includes_residual_pinned_tokens():
    raw = {
        ADDRESSES["USDT"]: 40 * 10**18,
        ADDRESSES["CAKE"]: 3 * 10**18,
    }
    dec = {ADDRESSES["USDT"]: 18, ADDRESSES["CAKE"]: 18}
    book, _ = make_book(raw, dec)
    snap = book.snapshot_all()
    assert snap["USDT"] == 40.0
    assert snap["CAKE"] == 3.0


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


def _topic_addr(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def _data(value: int) -> str:
    return "0x" + f"{value:064x}"


class FakeEth:
    block_number = 10

    def __init__(self, receipt, tx):
        self.receipt = receipt
        self.tx = tx

    def wait_for_transaction_receipt(self, tx_hash, timeout):
        return self.receipt

    def get_transaction(self, tx_hash):
        return self.tx


class FakeW3:
    def __init__(self, receipt, tx):
        self.eth = FakeEth(receipt, tx)


def _swap_intent():
    return TradeIntent(
        kind=IntentKind.QUALIFY,
        from_symbol="USDT",
        to_symbol="USDC",
        notional_usd=2.0,
        reason="test",
    )


def test_live_receipt_verifier_checks_logs_and_balance_deltas():
    router = "0x1111111111111111111111111111111111111111"
    tx_hash = "0x" + "ab" * 32
    raw = {ADDRESSES["USDT"]: 100 * 10**18, ADDRESSES["USDC"]: 0}
    dec = {ADDRESSES["USDT"]: 18, ADDRESSES["USDC"]: 18}
    receipt = {
        "status": 1,
        "blockNumber": 9,
        "logs": [
            {
                "address": ADDRESSES["USDT"],
                "topics": [TRANSFER_TOPIC, _topic_addr(WALLET), _topic_addr(router)],
                "data": _data(2 * 10**18),
            },
            {
                "address": ADDRESSES["USDC"],
                "topics": [TRANSFER_TOPIC, _topic_addr(router), _topic_addr(WALLET)],
                "data": _data(int(1.99 * 10**18)),
            },
        ],
    }
    book, _ = make_book(raw, dec)
    book.w3 = FakeW3(receipt, {"from": WALLET})
    verifier = LiveReceiptVerifier(book, slippage_pct=1.0)
    before = verifier.before(_swap_intent())
    raw[ADDRESSES["USDT"]] = 98 * 10**18
    raw[ADDRESSES["USDC"]] = int(1.99 * 10**18)

    proof = verifier(_swap_intent(), tx_hash, before)

    assert proof["from_transfer_out"] == 2.0
    assert proof["to_transfer_in"] == pytest.approx(1.99)
    assert proof["from_balance_delta"] == -2.0
    assert proof["to_balance_delta"] == pytest.approx(1.99)


def _buy_intent(price=2.5, notional=60.0):
    return TradeIntent(
        kind=IntentKind.ENTER,
        from_symbol="USDT",
        to_symbol="CAKE",
        notional_usd=notional,
        reason="enter",
        expected_price_usd=price,
    )


def _buy_receipt(router, cake_units_raw):
    return {
        "status": 1,
        "blockNumber": 9,
        "logs": [
            {
                "address": ADDRESSES["USDT"],
                "topics": [TRANSFER_TOPIC, _topic_addr(WALLET), _topic_addr(router)],
                "data": _data(60 * 10**18),
            },
            {
                "address": ADDRESSES["CAKE"],
                "topics": [TRANSFER_TOPIC, _topic_addr(router), _topic_addr(WALLET)],
                "data": _data(cake_units_raw),
            },
        ],
    }


def test_buy_verifier_accepts_within_slippage_fill():
    # $60 buy at $2.5 -> min 23.52 CAKE; a 24-CAKE fill passes. (#3)
    router = "0x1111111111111111111111111111111111111111"
    raw = {ADDRESSES["USDT"]: 100 * 10**18, ADDRESSES["CAKE"]: 0}
    dec = {ADDRESSES["USDT"]: 18, ADDRESSES["CAKE"]: 18}
    book, _ = make_book(raw, dec)
    book.w3 = FakeW3(_buy_receipt(router, 24 * 10**18), {"from": WALLET})
    verifier = LiveReceiptVerifier(book, slippage_pct=1.0)
    before = verifier.before(_buy_intent())
    raw[ADDRESSES["USDT"]] = 40 * 10**18
    raw[ADDRESSES["CAKE"]] = 24 * 10**18

    proof = verifier(_buy_intent(), "0x" + "cd" * 32, before)
    assert proof["to_transfer_in"] == 24.0


def test_buy_verifier_rejects_dust_fill():
    # A sandwich/honeypot returning ~1 CAKE for a $60 buy must fail, not record
    # as a full fill. (#3)
    router = "0x1111111111111111111111111111111111111111"
    raw = {ADDRESSES["USDT"]: 100 * 10**18, ADDRESSES["CAKE"]: 0}
    dec = {ADDRESSES["USDT"]: 18, ADDRESSES["CAKE"]: 18}
    book, _ = make_book(raw, dec)
    book.w3 = FakeW3(_buy_receipt(router, 1 * 10**18), {"from": WALLET})
    verifier = LiveReceiptVerifier(book, slippage_pct=1.0)
    before = verifier.before(_buy_intent())
    raw[ADDRESSES["USDT"]] = 40 * 10**18
    raw[ADDRESSES["CAKE"]] = 1 * 10**18

    with pytest.raises(RuntimeError, match="units below minimum"):
        verifier(_buy_intent(), "0x" + "cd" * 32, before)


def test_live_receipt_verifier_rejects_missing_incoming_transfer():
    router = "0x1111111111111111111111111111111111111111"
    raw = {ADDRESSES["USDT"]: 100 * 10**18, ADDRESSES["USDC"]: 0}
    dec = {ADDRESSES["USDT"]: 18, ADDRESSES["USDC"]: 18}
    receipt = {
        "status": 1,
        "blockNumber": 9,
        "logs": [
            {
                "address": ADDRESSES["USDT"],
                "topics": [TRANSFER_TOPIC, _topic_addr(WALLET), _topic_addr(router)],
                "data": _data(2 * 10**18),
            }
        ],
    }
    book, _ = make_book(raw, dec)
    book.w3 = FakeW3(receipt, {"from": WALLET})
    verifier = LiveReceiptVerifier(book, slippage_pct=1.0)
    before = verifier.before(_swap_intent())
    raw[ADDRESSES["USDT"]] = 98 * 10**18

    with pytest.raises(RuntimeError, match="balance did not increase"):
        verifier(_swap_intent(), "0x" + "ab" * 32, before)
