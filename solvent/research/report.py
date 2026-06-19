"""Generate a strategy-evidence report from the local backtester."""

import argparse
import math
import json
from dataclasses import asdict
from statistics import mean

from .backtest import DEFAULT_FEE_PCT, Candle, profile_configs, simulate, stress_suite


def strategy_report(
    *,
    start_usd: float = 300.0,
    fee_pct: float = DEFAULT_FEE_PCT,
    profiles: tuple[str, ...] = (
        "safety",
        "conviction_50",
        "tournament_50",
        "tournament_60",
    ),
    policy_profile: str | None = None,
) -> dict:
    configs = profile_configs()
    scenarios = {}
    for scenario, candles in stress_suite().items():
        metadata = scenario_metadata(candles)
        results = [
            asdict(
                simulate(
                    candles,
                    cfg=configs[name],
                    profile=name,
                    start_usd=start_usd,
                    fee_pct=fee_pct,
                )
            )
            for name in profiles
        ]
        benchmarks = benchmark_report(candles, start_usd=start_usd)
        scenarios[scenario] = {
            "metadata": metadata,
            "profiles": results,
            "benchmarks": benchmarks,
            "relative_to_benchmarks": [
                {
                    "profile": result["profile"],
                    "excess_return_vs_stables_pct": round(
                        result["total_return_pct"]
                        - benchmarks["hold_stables"]["total_return_pct"],
                        6,
                    ),
                    "excess_return_vs_best_buy_hold_pct": round(
                        result["total_return_pct"]
                        - benchmarks["best_buy_hold"]["total_return_pct"],
                        6,
                    ),
                    "beats_stables": result["total_return_pct"]
                    > benchmarks["hold_stables"]["total_return_pct"],
                    "beats_best_buy_hold": result["total_return_pct"]
                    > benchmarks["best_buy_hold"]["total_return_pct"],
                    "active_window_trade_rule_met": result["trades"]
                    >= metadata["required_trades_to_cover_active_days"],
                    "dq_breached": result["dq_breached"],
                }
                for result in results
            ],
        }
    scorecard = profile_scorecard(scenarios, profiles)
    return {
        "schema": "solvent.strategy-evidence.v1",
        "start_usd": start_usd,
        "fee_pct": fee_pct,
        "profiles": list(profiles),
        "scenarios": scenarios,
        "profile_scorecard": scorecard,
        "fee_sensitivity": fee_sensitivity(
            start_usd=start_usd,
            fee_pcts=(fee_pct, fee_pct * 2),
            profiles=profiles,
        ),
        "selection": {
            "rubric": (
                "Rank profiles by declared evidence points first, then average "
                "scenario return, then lower worst drawdown. This is a research "
                "scorecard, not an optimizer or alpha guarantee."
            ),
            "best_supported_profile": scorecard[0]["profile"] if scorecard else None,
            "anchored_policy_profile": policy_profile,
            "anchored_policy_rank": _rank_for_profile(scorecard, policy_profile),
        },
        "edge_claim": {
            "guaranteed": False,
            "standard": (
                "A profile earns an evidence point only when it beats stables "
                "and the best buy-and-hold benchmark without breaching DQ in a "
                "declared scenario."
            ),
        },
        "limits": [
            "Synthetic stress scenarios are execution-safety evidence, not proof of alpha.",
            "Historical mode uses close-to-close candles and does not model MEV or route liquidity.",
            "Scored-window policy must be frozen separately in the signed policy manifest.",
        ],
    }


def _rank_for_profile(scorecard: list[dict], profile: str | None) -> int | None:
    if profile is None:
        return None
    for index, row in enumerate(scorecard, start=1):
        if row["profile"] == profile:
            return index
    return None


def scenario_metadata(
    candles_by_symbol: dict[str, list[Candle]], *, interval_hours: float = 1.0
) -> dict:
    times = sorted({c.ts for candles in candles_by_symbol.values() for c in candles})
    if not times:
        return {
            "symbols": [],
            "candle_count": 0,
            "first_ts": None,
            "last_ts": None,
            "active_days_after_lookback": 0,
            "required_trades_to_cover_active_days": 0,
        }
    lookback_7d = max(1, round(168 / interval_hours))
    active_start = times[min(lookback_7d, len(times) - 1)]
    active_hours = max(0.0, (times[-1] - active_start).total_seconds() / 3600)
    active_days = max(1, math.ceil(active_hours / 24))
    return {
        "symbols": sorted(candles_by_symbol),
        "candle_count": sum(len(candles) for candles in candles_by_symbol.values()),
        "first_ts": times[0].isoformat(),
        "last_ts": times[-1].isoformat(),
        "active_days_after_lookback": active_days,
        "required_trades_to_cover_active_days": active_days,
    }


def profile_scorecard(scenarios: dict, profiles: tuple[str, ...]) -> list[dict]:
    rows = []
    for profile in profiles:
        results = []
        relatives = []
        for scenario_payload in scenarios.values():
            results.extend(
                result
                for result in scenario_payload["profiles"]
                if result["profile"] == profile
            )
            relatives.extend(
                rel
                for rel in scenario_payload["relative_to_benchmarks"]
                if rel["profile"] == profile
            )
        if not results:
            continue
        dq_breaches = sum(1 for result in results if result["dq_breached"])
        beats_stables = sum(1 for rel in relatives if rel["beats_stables"])
        beats_best = sum(1 for rel in relatives if rel["beats_best_buy_hold"])
        trade_rule = sum(1 for rel in relatives if rel["active_window_trade_rule_met"])
        evidence_points = (
            len(results) - dq_breaches + beats_stables + beats_best + trade_rule
        )
        worst_drawdown = max(result["max_drawdown_pct"] for result in results)
        average_return = mean(result["total_return_pct"] for result in results)
        rows.append(
            {
                "profile": profile,
                "scenarios_evaluated": len(results),
                "evidence_points": evidence_points,
                "dq_breaches": dq_breaches,
                "beats_stables_count": beats_stables,
                "beats_best_buy_hold_count": beats_best,
                "active_window_trade_rule_met_count": trade_rule,
                "average_return_pct": round(average_return, 6),
                "worst_return_pct": round(
                    min(result["total_return_pct"] for result in results), 6
                ),
                "worst_drawdown_pct": round(worst_drawdown, 6),
                "return_to_drawdown": round(
                    average_return / max(worst_drawdown, 1e-9), 6
                ),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row["evidence_points"],
            row["average_return_pct"],
            -row["worst_drawdown_pct"],
        ),
        reverse=True,
    )


def fee_sensitivity(
    *,
    start_usd: float,
    fee_pcts: tuple[float, ...],
    profiles: tuple[str, ...],
) -> list[dict]:
    configs = profile_configs()
    rows = []
    for fee_pct in fee_pcts:
        by_profile: dict[str, list[dict]] = {profile: [] for profile in profiles}
        for candles in stress_suite().values():
            for profile in profiles:
                by_profile[profile].append(
                    asdict(
                        simulate(
                            candles,
                            cfg=configs[profile],
                            profile=profile,
                            start_usd=start_usd,
                            fee_pct=fee_pct,
                        )
                    )
                )
        rows.append(
            {
                "fee_pct": fee_pct,
                "profiles": [
                    {
                        "profile": profile,
                        "average_return_pct": round(
                            mean(result["total_return_pct"] for result in results), 6
                        ),
                        "worst_return_pct": round(
                            min(result["total_return_pct"] for result in results), 6
                        ),
                        "dq_breaches": sum(
                            1 for result in results if result["dq_breached"]
                        ),
                    }
                    for profile, results in by_profile.items()
                ],
            }
        )
    return rows


def benchmark_report(
    candles_by_symbol: dict[str, list[Candle]], *, start_usd: float
) -> dict:
    buy_hold = []
    for symbol, candles in sorted(candles_by_symbol.items()):
        ordered = sorted(candles, key=lambda c: c.ts)
        if not ordered or ordered[0].close <= 0:
            continue
        end_equity = start_usd * ordered[-1].close / ordered[0].close
        buy_hold.append(
            {
                "symbol": symbol,
                "start_equity_usd": round(start_usd, 4),
                "end_equity_usd": round(end_equity, 4),
                "total_return_pct": round(end_equity / start_usd - 1.0, 6),
            }
        )
    if buy_hold:
        best = max(buy_hold, key=lambda item: item["total_return_pct"])
        equal_weight_end = sum(item["end_equity_usd"] for item in buy_hold) / len(
            buy_hold
        )
    else:
        best = {
            "symbol": None,
            "start_equity_usd": round(start_usd, 4),
            "end_equity_usd": round(start_usd, 4),
            "total_return_pct": 0.0,
        }
        equal_weight_end = start_usd
    return {
        "hold_stables": {
            "start_equity_usd": round(start_usd, 4),
            "end_equity_usd": round(start_usd, 4),
            "total_return_pct": 0.0,
        },
        "buy_hold": buy_hold,
        "best_buy_hold": best,
        "equal_weight_buy_hold": {
            "start_equity_usd": round(start_usd, 4),
            "end_equity_usd": round(equal_weight_end, 4),
            "total_return_pct": round(equal_weight_end / start_usd - 1.0, 6),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-usd", type=float, default=300.0)
    parser.add_argument("--fee-pct", type=float, default=DEFAULT_FEE_PCT)
    parser.add_argument(
        "--profiles",
        default="safety,conviction_50,tournament_50,tournament_60",
    )
    parser.add_argument("--policy-profile")
    args = parser.parse_args(argv)
    profiles = tuple(name.strip() for name in args.profiles.split(",") if name.strip())
    print(
        json.dumps(
            strategy_report(
                start_usd=args.start_usd,
                fee_pct=args.fee_pct,
                profiles=profiles,
                policy_profile=args.policy_profile,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
