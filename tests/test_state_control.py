import json

import pytest

from solvent.engine import StateStore
from solvent.exec.executor import Journal
from solvent.kernel.allocator import IntentKind, TradeIntent
from solvent.ops.state_control import main


def test_halt_and_resume_cycle(tmp_path, capsys):
    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "halt",
                "--reason",
                "operator test",
            ]
        )
        == 0
    )
    halted = json.loads(capsys.readouterr().out)
    assert halted["runtime_status"] == "HALTED"

    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "resume",
                "--reason",
                "review complete",
            ]
        )
        == 0
    )
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["runtime_status"] == "ACTIVE"
    assert "review complete" in resumed["halt_reason"]
    assert StateStore.load(tmp_path / "state.json").runtime_status == "ACTIVE"


def test_resume_refuses_unresolved_journal(tmp_path):
    intent = TradeIntent(
        kind=IntentKind.QUALIFY,
        from_symbol="USDT",
        to_symbol="USDC",
        notional_usd=2.0,
        reason="test",
    )
    Journal(tmp_path / "journal.jsonl").mark_attempted("pending", intent)

    with pytest.raises(SystemExit, match="unresolved"):
        main(
            [
                "--data-dir",
                str(tmp_path),
                "resume",
                "--reason",
                "review complete",
            ]
        )
