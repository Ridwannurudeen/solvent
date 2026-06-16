"""Portfolio and position state passed into the allocator each cycle.

State is assembled by the engine from on-chain balances and the trade
journal — the allocator never mutates it, only reads it and returns
intents.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class SleevePosition:
    symbol: str
    entry_price_usd: float
    entry_momo_score: float
    notional_usd: float
    opened_at: datetime


@dataclass(frozen=True)
class PortfolioState:
    equity_usd: float
    # Equity at competition start (set once when the scored week opens).
    start_equity_usd: float
    # Highest equity observed since competition start.
    peak_equity_usd: float
    # USD value currently held in floor stables.
    floor_usd: float
    # Open sleeve position, if any.
    position: SleevePosition | None
    # Swaps already executed this UTC day (from the journal).
    trades_today: int
    # True once any qualifying trade confirmed this UTC day.
    qualified_today: bool
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def banked_gain_pct(self) -> float:
        if self.start_equity_usd <= 0:
            return 0.0
        return self.equity_usd / self.start_equity_usd - 1.0

    @property
    def trailing_drawdown_pct(self) -> float:
        if self.peak_equity_usd <= 0:
            return 0.0
        return 1.0 - self.equity_usd / self.peak_equity_usd

    @property
    def dq_headroom_pct(self) -> float:
        """Distance between current trailing drawdown and the DQ line.

        Computed against the conservative (peak-based) reading of the
        rule until the organizers answer G1.
        """
        return max(0.0, 0.30 - self.trailing_drawdown_pct)


@dataclass(frozen=True)
class MarketSignals:
    """Signal snapshot the engine assembles from the data layer."""

    # Regime classification inputs.
    fear_greed: int | None  # 0-100
    btc_funding_rate: float | None  # latest aggregate funding, e.g. 0.0001
    # symbol -> momentum score (higher = stronger confirmed uptrend);
    # only sleeve-universe symbols appear here.
    momentum: dict[str, float] = field(default_factory=dict)
    # symbol -> latest USD price for everything we may touch.
    prices: dict[str, float] = field(default_factory=dict)
    # True when any feed is stale/failed — forces fail-frozen behavior.
    degraded: bool = False
