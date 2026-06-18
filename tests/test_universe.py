from solvent.kernel.allowlist import DISCOVERY_SYMBOLS, needs_manual_pin
from solvent.receipts.chain import DataPurchase
from solvent.signals.universe import CandidateScanner


def _quote(symbol="CAKE", volume=10_000_000, market_cap=100_000_000, pc24=3.0, pc7=5.0):
    return {
        "symbol": symbol,
        "quote": {
            "USD": {
                "price": 1.0,
                "percent_change_24h": pc24,
                "percent_change_7d": pc7,
                "volume_24h": volume,
                "market_cap": market_cap,
            }
        },
    }


def test_discovery_symbols_are_deduped_and_manual_pin_marks_visible_tokens():
    assert len(DISCOVERY_SYMBOLS) == len(set(DISCOVERY_SYMBOLS))
    assert needs_manual_pin("TRX") is True
    assert needs_manual_pin("USDT") is False


def test_scanner_marks_good_candidate_as_manual_pin_required():
    scanner = CandidateScanner(
        cmc_ids={"TRX": 1958},
        addresses={},
        binance_pairs={"TRXUSDT"},
    )

    candidate = scanner.scan_quotes(
        {"data": {"TRX": _quote("TRX")}}, symbols=("TRX",)
    ).candidates[0]

    assert candidate.market_gate_passed is True
    assert candidate.manual_address_pin_required is True
    assert "manual_address_pin_required" in candidate.rejection_reasons


def test_scanner_rejects_missing_cmc_id():
    candidate = (
        CandidateScanner(binance_pairs={"TRXUSDT"})
        .scan_quotes({"data": {"TRX": _quote("TRX")}}, symbols=("TRX",))
        .candidates[0]
    )

    assert candidate.market_gate_passed is False
    assert "missing_cmc_id" in candidate.rejection_reasons


def test_scanner_rejects_missing_binance_pair():
    candidate = (
        CandidateScanner(cmc_ids={"TRX": 1958})
        .scan_quotes({"data": {"TRX": _quote("TRX")}}, symbols=("TRX",))
        .candidates[0]
    )

    assert candidate.market_gate_passed is False
    assert "missing_binance_pair" in candidate.rejection_reasons


def test_scanner_rejects_low_liquidity_and_momentum():
    candidate = (
        CandidateScanner(cmc_ids={"TRX": 1958}, binance_pairs={"TRXUSDT"})
        .scan_quotes(
            {"data": {"TRX": _quote("TRX", volume=1, market_cap=1, pc24=-1, pc7=5)}},
            symbols=("TRX",),
        )
        .candidates[0]
    )

    assert candidate.market_gate_passed is False
    assert "low_volume" in candidate.rejection_reasons
    assert "low_market_cap" in candidate.rejection_reasons
    assert "low_momentum" in candidate.rejection_reasons


def test_fetch_cmc_candidates_records_purchase():
    class Client:
        def call_tool(self, name, arguments):
            assert name == "get_crypto_quotes_latest"
            assert arguments == {"id": "1958"}
            return {
                "structuredContent": {"data": {"TRX": _quote("TRX")}}
            }, DataPurchase(tool=name, cost_usdc=0.01, ok=True)

    result = CandidateScanner(
        cmc_ids={"TRX": 1958}, binance_pairs={"TRXUSDT"}
    ).fetch_cmc_candidates(Client(), symbols=("TRX",))

    assert result.purchases[0].tool == "get_crypto_quotes_latest"
    assert result.candidates[0].market_gate_passed is True
