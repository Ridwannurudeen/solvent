from dataclasses import dataclass

import anthropic
import httpx

from solvent.brain.advisor import RegimeAdvice, advise
from solvent.kernel.allocator import Regime
from solvent.kernel.state import MarketSignals

SIGNALS = MarketSignals(
    fear_greed=60,
    btc_funding_rate=0.0001,
    momentum={"CAKE": 2.0, "ETH": 1.5},
    prices={"CAKE": 2.5},
    degraded=False,
)


@dataclass
class _Block:
    type: str
    text: str


@dataclass
class _Resp:
    content: list
    stop_reason: str = "end_turn"


class _Client:
    """Minimal stand-in for anthropic.Anthropic — returns a canned response."""

    def __init__(self, resp=None, raise_exc=None):
        self._resp = resp
        self._raise = raise_exc
        self.messages = self

    def create(self, **kwargs):
        if self._raise is not None:
            raise self._raise
        return self._resp


def _json_resp(body: str, stop_reason: str = "end_turn") -> _Resp:
    return _Resp(content=[_Block("text", body)], stop_reason=stop_reason)


def test_parses_valid_advice():
    client = _Client(
        _json_resp(
            '{"regime": "neutral", "confidence": 0.6, '
            '"thesis": "funding crowded", "ranked_symbols": ["CAKE", "ETH"]}'
        )
    )
    advice = advise(SIGNALS, Regime.RISK_ON, client=client)
    assert advice == RegimeAdvice(
        Regime.NEUTRAL, 0.6, "funding crowded", ["CAKE", "ETH"]
    )


def test_confidence_is_clamped_to_unit_range():
    client = _Client(
        _json_resp(
            '{"regime": "neutral", "confidence": 5.0, '
            '"thesis": "x", "ranked_symbols": []}'
        )
    )
    advice = advise(SIGNALS, Regime.RISK_ON, client=client)
    assert advice is not None and advice.confidence == 1.0


def test_api_error_returns_none():
    client = _Client(
        raise_exc=anthropic.APIConnectionError(
            request=httpx.Request("POST", "http://x")
        )
    )
    assert advise(SIGNALS, Regime.RISK_ON, client=client) is None


def test_refusal_returns_none():
    client = _Client(_json_resp("{}", stop_reason="refusal"))
    assert advise(SIGNALS, Regime.RISK_ON, client=client) is None


def test_malformed_json_returns_none():
    client = _Client(_json_resp("not json at all"))
    assert advise(SIGNALS, Regime.RISK_ON, client=client) is None


def test_missing_field_returns_none():
    client = _Client(_json_resp('{"regime": "neutral", "confidence": 0.5}'))
    assert advise(SIGNALS, Regime.RISK_ON, client=client) is None


def test_unknown_regime_returns_none():
    client = _Client(
        _json_resp(
            '{"regime": "moon", "confidence": 1.0, "thesis": "x", "ranked_symbols": []}'
        )
    )
    assert advise(SIGNALS, Regime.RISK_ON, client=client) is None
