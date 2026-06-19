from solvent.research.report import strategy_report


def test_strategy_report_contains_stress_results_and_limits():
    report = strategy_report(profiles=("safety", "tournament_50"))

    assert report["schema"] == "solvent.strategy-evidence.v1"
    assert set(report["scenarios"]) == {"uptrend", "crash", "chop"}
    assert len(report["scenarios"]["uptrend"]) == 2
    assert report["limits"]
