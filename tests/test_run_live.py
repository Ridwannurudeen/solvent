import pytest
from eth_account import Account

from solvent.exec.executor import TwakExecutor
from solvent.exec.livebook import LiveBook
from solvent.run import build_live
from solvent.signals.sources import CMCSource


def _set_live_env(monkeypatch):
    """Throwaway creds so build_live assembles offline — never broadcasts."""
    monkeypatch.setenv("SOLVENT_PRIVATE_KEY", Account.create().key.hex())
    monkeypatch.setenv("SOLVENT_WALLET_PASSWORD", "pw")
    monkeypatch.setenv("SOLVENT_TRADE_NETWORK", "bsc-testnet")


def test_build_live_assembles_real_components(tmp_path, monkeypatch):
    from solvent.kernel.rules import RiskConfig

    _set_live_env(monkeypatch)
    source, executor, journal, book = build_live(tmp_path, RiskConfig())
    assert isinstance(source, CMCSource)
    assert isinstance(executor, TwakExecutor)
    assert isinstance(book, LiveBook)
    assert executor.chain == "bsc"  # default TWAK chain


@pytest.mark.parametrize(
    "missing",
    ["SOLVENT_PRIVATE_KEY", "SOLVENT_WALLET_PASSWORD"],
)
def test_build_live_fails_fast_without_creds(tmp_path, monkeypatch, missing):
    from solvent.kernel.rules import RiskConfig

    _set_live_env(monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(SystemExit):
        build_live(tmp_path, RiskConfig())
