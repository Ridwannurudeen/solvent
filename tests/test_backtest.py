import pytest
import httpx

from solvent.kernel.allowlist import ADDRESSES
from solvent.research.backtest import (
    _fetch_klines,
    profile_configs,
    simulate,
    stress_suite,
    synthetic_candles,
)


@pytest.fixture(autouse=True)
def pin_cake(monkeypatch):
    monkeypatch.setitem(ADDRESSES, "CAKE", "0x" + "1" * 40)


def test_re_risked_profiles_outperform_safety_in_clean_uptrend():
    candles = {
        "CAKE": synthetic_candles(
            "CAKE", start=2.0, hourly_returns=[0.0] * 168 + [0.004] * 72
        )
    }
    profiles = profile_configs()

    safety = simulate(candles, cfg=profiles["safety"], profile="safety")
    tournament = simulate(
        candles, cfg=profiles["tournament_50"], profile="tournament_50"
    )

    assert tournament.total_return_pct > safety.total_return_pct
    assert tournament.entries >= 1


def test_crash_stress_exits_before_dq_line():
    profiles = profile_configs()
    result = simulate(
        stress_suite()["crash"],
        cfg=profiles["tournament_50"],
        profile="tournament_50",
    )

    assert result.exits >= 1
    assert result.max_drawdown_pct < profiles["tournament_50"].dq_drawdown_pct
    assert result.dq_breached is False


def test_no_momentum_path_only_pays_qualification_fees():
    candles = {"CAKE": synthetic_candles("CAKE", start=2.0, hourly_returns=[0.0] * 240)}
    result = simulate(candles, cfg=profile_configs()["safety"], profile="safety")

    assert result.entries == 0
    assert result.qualifications >= 2
    assert result.end_equity_usd < result.start_equity_usd


def test_fetch_klines_tries_fallback_after_network_error(monkeypatch):
    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [["open_time"]]

    class Client:
        def __init__(self):
            self.urls = []

        def get(self, url, params):
            self.urls.append(url)
            if len(self.urls) == 1:
                raise httpx.ConnectError("dns")
            return Response()

    monkeypatch.setattr(
        "solvent.research.backtest.BINANCE_RESEARCH_APIS",
        ("https://primary", "https://fallback"),
    )
    client = Client()

    assert _fetch_klines(client, pair="CAKEUSDT", interval="1h", limit=10) == [
        ["open_time"]
    ]
    assert client.urls == ["https://primary/klines", "https://fallback/klines"]
