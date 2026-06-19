"""Risk configuration — the constitution of the agent.

Every number that controls money lives here, is frozen at the Phase 3
strategy freeze, and is enforced by pure functions in allocator.py.
Nothing downstream (LLM, signals, executor) can override these values.
"""

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class RatchetTier:
    """Above `gain_pct` banked gain, sleeve fraction caps at `sleeve_cap`."""

    gain_pct: float
    sleeve_cap: float


@dataclass(frozen=True)
class RiskConfig:
    # ── Barbell ──────────────────────────────────────────────────────
    # Fraction of equity that must remain in floor stables at all times.
    floor_frac_min: float = 0.75
    # Sleeve sizing: target fraction of equity in the momentum sleeve
    # when a confirmed entry exists (≤ 1 - floor_frac_min).
    sleeve_frac_target: float = 0.22
    # At most one concurrent sleeve position (concentration by design).
    max_positions: int = 1

    # ── Per-position protection ──────────────────────────────────────
    # Hard stop: exit sleeve position at this loss from entry.
    stop_pct: float = 0.12
    # Move the protective stop to breakeven once a position has this profit.
    breakeven_activation_pct: float = 999.0
    # Trail from the observed high once a position has this profit.
    trailing_activation_pct: float = 999.0
    # Trailing stop distance from the observed high.
    trailing_stop_pct: float = 0.05
    # Take partial profits once per position after this profit threshold.
    take_profit_pct: float = 999.0
    # Fraction of the current sleeve value sold by the take-profit rule.
    take_profit_fraction: float = 0.35
    # Entry bar for confirmed 24h+7d momentum.
    min_entry_momo: float = 1.0
    # Ignore momentum-decay exits until a position has had time to work.
    min_hold_hours: float = 0.0
    # Take-profit decay exit: exit when momentum score drops below this
    # fraction of its entry value.
    momo_decay_exit: float = 0.5

    # ── Lock-in ratchet (anti peak-drawdown DQ) ──────────────────────
    ratchet: tuple[RatchetTier, ...] = (
        RatchetTier(gain_pct=0.10, sleeve_cap=0.15),
        RatchetTier(gain_pct=0.20, sleeve_cap=0.10),
        RatchetTier(gain_pct=0.35, sleeve_cap=0.05),
    )

    # ── Disqualification guards ─────────────────────────────────────
    # Competition DQ threshold (organizer rule).
    dq_drawdown_pct: float = 0.30
    # Our action threshold: if trailing drawdown from peak reaches this,
    # force-liquidate sleeve to floor and stay there (manual review to
    # resume). Half-way buffer to the DQ line.
    kill_switch_drawdown_pct: float = 0.22
    # Never let total portfolio value approach the $1 dust rule.
    min_portfolio_usd: float = 25.0
    # Conservative floor valuation: stables are marked at min(price, $1)
    # minus this haircut instead of exactly $1.
    stable_haircut_pct: float = 0.0025

    # ── Per-trade limits ─────────────────────────────────────────────
    # Max notional of any single swap as a fraction of equity.
    max_trade_frac: float = 0.25
    # Slippage tolerance passed to the executor (percent).
    max_slippage_pct: float = 1.0
    # Max swaps per UTC day (incl. qualification trade).
    max_trades_per_day: int = 6

    # ── Qualification (≥1 trade/day rule) ────────────────────────────
    # Hour (UTC) by which the daily qualification trade must be done;
    # the deadman fires the fallback micro-trade after this.
    qual_deadline_hour_utc: int = 20
    # Notional of the fallback qualification micro-trade, in USD.
    qual_trade_usd: float = 2.0

    # ── Data spend (x402 metering) ───────────────────────────────────
    # Session budget for paid data calls, USDC base units (6 decimals).
    x402_session_budget_usdc: int = 15_000_000  # $15.00
    # Per-call cap (a malicious 402 challenge cannot exceed this).
    x402_max_per_call_usdc: int = 50_000  # $0.05

    floor_symbols: tuple[str, ...] = field(
        default=("USDT", "USDC", "FDUSD", "DAI", "USD1")
    )

    def sleeve_cap_for_gain(self, banked_gain_pct: float) -> float:
        """Sleeve fraction cap given banked gain since competition start.

        Walks the ratchet tiers from highest gain down; returns the
        matching cap, or sleeve_frac_target when no tier is reached.
        """
        cap = self.sleeve_frac_target
        for tier in sorted(self.ratchet, key=lambda t: t.gain_pct):
            if banked_gain_pct >= tier.gain_pct:
                cap = min(cap, tier.sleeve_cap)
        return cap


RISK_PROFILE_NAMES = ("safety", "tournament_50", "conviction_50", "tournament_60")


def risk_config_for_profile(profile: str) -> RiskConfig:
    """Named risk profiles; `safety` is the unchanged default."""
    name = profile.strip().lower()
    base = RiskConfig()
    if name == "safety":
        return base
    if name == "tournament_50":
        return replace(
            base,
            floor_frac_min=0.50,
            sleeve_frac_target=0.48,
            max_trade_frac=0.50,
            stop_pct=0.08,
            ratchet=(
                RatchetTier(gain_pct=0.10, sleeve_cap=0.35),
                RatchetTier(gain_pct=0.20, sleeve_cap=0.25),
                RatchetTier(gain_pct=0.35, sleeve_cap=0.15),
            ),
        )
    if name == "conviction_50":
        return replace(
            base,
            floor_frac_min=0.50,
            sleeve_frac_target=0.48,
            max_trade_frac=0.50,
            stop_pct=0.08,
            min_entry_momo=10.0,
            min_hold_hours=48.0,
            ratchet=(
                RatchetTier(gain_pct=0.10, sleeve_cap=0.35),
                RatchetTier(gain_pct=0.20, sleeve_cap=0.25),
                RatchetTier(gain_pct=0.35, sleeve_cap=0.15),
            ),
        )
    if name == "tournament_60":
        return replace(
            base,
            floor_frac_min=0.40,
            sleeve_frac_target=0.58,
            max_trade_frac=0.60,
            stop_pct=0.07,
            ratchet=(
                RatchetTier(gain_pct=0.10, sleeve_cap=0.40),
                RatchetTier(gain_pct=0.20, sleeve_cap=0.25),
                RatchetTier(gain_pct=0.35, sleeve_cap=0.15),
            ),
        )
    raise ValueError(f"unknown risk profile: {profile}")
