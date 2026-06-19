import pytest
from eth_account import Account

from solvent import run as run_mod
from solvent.exec.executor import TwakExecutor
from solvent.exec.livebook import LiveBook
from solvent.run import build_live
from solvent.signals.sources import CMCSource, CrossCheckedSource


def _set_live_env(monkeypatch):
    """Throwaway creds so build_live assembles offline — never broadcasts."""
    monkeypatch.setenv("SOLVENT_PRIVATE_KEY", Account.create().key.hex())
    monkeypatch.setenv("SOLVENT_WALLET_PASSWORD", "pw")
    monkeypatch.setenv("SOLVENT_WALLET_ADDRESS", Account.create().address)
    monkeypatch.setenv("SOLVENT_TRADE_NETWORK", "bsc-testnet")


def test_build_live_assembles_real_components(tmp_path, monkeypatch):
    from solvent.kernel.rules import RiskConfig

    _set_live_env(monkeypatch)
    source, executor, journal, book = build_live(tmp_path, RiskConfig())
    assert isinstance(source, CrossCheckedSource)
    assert isinstance(source.primary, CMCSource)
    assert isinstance(executor, TwakExecutor)
    assert isinstance(book, LiveBook)
    assert executor.chain == "bsc"  # default TWAK chain


@pytest.mark.parametrize(
    "missing",
    ["SOLVENT_PRIVATE_KEY", "SOLVENT_WALLET_PASSWORD", "SOLVENT_WALLET_ADDRESS"],
)
def test_build_live_fails_fast_without_creds(tmp_path, monkeypatch, missing):
    from solvent.kernel.rules import RiskConfig

    _set_live_env(monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(SystemExit) as exc:
        build_live(tmp_path, RiskConfig())
    assert missing in str(exc.value)


def test_main_live_refuses_paper_data_dir(tmp_path, monkeypatch):
    (tmp_path / "paper-holdings.json").write_text("{}")
    monkeypatch.setattr(
        run_mod,
        "build_live",
        lambda *a, **k: pytest.fail("build_live should not be called"),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "solvent.run",
            "--mode",
            "live",
            "--data-dir",
            str(tmp_path),
            "--once",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        run_mod.main()

    assert "paper-holdings.json" in str(exc.value)


def test_main_once_returns_nonzero_when_cycle_fails(tmp_path, monkeypatch):
    def fail_cycle(**_kwargs):
        raise RuntimeError("cycle failed")

    monkeypatch.setattr(run_mod, "run_cycle", fail_cycle)
    monkeypatch.setattr(run_mod, "alert", lambda _message: None)
    monkeypatch.setattr(
        "sys.argv",
        [
            "solvent.run",
            "--mode",
            "paper",
            "--data-dir",
            str(tmp_path),
            "--once",
        ],
    )

    assert run_mod.main() == 1
