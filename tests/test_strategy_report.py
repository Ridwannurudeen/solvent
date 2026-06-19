from solvent.research.report import strategy_report


def test_strategy_report_contains_stress_results_and_limits():
    report = strategy_report(
        profiles=("safety", "tournament_50"), policy_profile="tournament_50"
    )

    assert report["schema"] == "solvent.strategy-evidence.v1"
    assert set(report["scenarios"]) == {"uptrend", "crash", "chop"}
    assert report["scenarios"]["uptrend"]["metadata"]["active_days_after_lookback"] == 3
    assert len(report["scenarios"]["uptrend"]["profiles"]) == 2
    assert report["scenarios"]["uptrend"]["benchmarks"]["best_buy_hold"]
    assert report["scenarios"]["uptrend"]["relative_to_benchmarks"]
    assert report["profile_scorecard"]
    assert report["profile_scorecard"][0]["evidence_points"] > 0
    assert report["fee_sensitivity"]
    assert report["selection"]["anchored_policy_profile"] == "tournament_50"
    assert report["selection"]["anchored_policy_rank"] in {1, 2}
    assert report["edge_claim"]["guaranteed"] is False
    assert report["limits"]
