"""Generate a strategy-evidence report from the local backtester."""

import argparse
import json
from dataclasses import asdict

from .backtest import DEFAULT_FEE_PCT, profile_configs, simulate, stress_suite


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
        scenarios[scenario] = results
    return {
        "schema": "solvent.strategy-evidence.v1",
        "start_usd": start_usd,
        "fee_pct": fee_pct,
        "profiles": list(profiles),
        "scenarios": scenarios,
        "limits": [
            "Synthetic stress scenarios are execution-safety evidence, not proof of alpha.",
            "Historical mode uses close-to-close candles and does not model MEV or route liquidity.",
            "Scored-window policy must be frozen separately in the signed policy manifest.",
        ],
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
