"""Generate a strategy-evidence report from the local backtester."""

import argparse
import json
from dataclasses import asdict

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
) -> dict:
    configs = profile_configs()
    scenarios = {}
    for scenario, candles in stress_suite().items():
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
                    "dq_breached": result["dq_breached"],
                }
                for result in results
            ],
        }
    return {
        "schema": "solvent.strategy-evidence.v1",
        "start_usd": start_usd,
        "fee_pct": fee_pct,
        "profiles": list(profiles),
        "scenarios": scenarios,
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
    args = parser.parse_args(argv)
    profiles = tuple(name.strip() for name in args.profiles.split(",") if name.strip())
    print(
        json.dumps(
            strategy_report(
                start_usd=args.start_usd, fee_pct=args.fee_pct, profiles=profiles
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
