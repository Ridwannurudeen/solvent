"""FastAPI ERC-8183 provider for SOLVENT's paid regime signal."""

import os
from pathlib import Path

from bnbagent.erc8183.config import ERC8183Config
from bnbagent.erc8183.server import create_erc8183_app
from bnbagent.storage import LocalStorageProvider
from bnbagent.wallets import EVMWalletProvider

from .signal import SERVICE_ID, build_job_response, service_price_raw_units


def _data_dir() -> Path:
    return Path(os.environ.get("SOLVENT_DATA_DIR", "/opt/solvent/data-prod"))


def _wallet() -> EVMWalletProvider:
    password = os.environ.get("SOLVENT_WALLET_PASSWORD") or os.environ.get(
        "WALLET_PASSWORD"
    )
    if not password:
        raise RuntimeError("SOLVENT_WALLET_PASSWORD or WALLET_PASSWORD is required")
    return EVMWalletProvider(
        password=password,
        private_key=os.environ.get("SOLVENT_PRIVATE_KEY")
        or os.environ.get("PRIVATE_KEY"),
        address=os.environ.get("SOLVENT_WALLET_ADDRESS")
        or os.environ.get("WALLET_ADDRESS")
        or None,
    )


def _service_price() -> str:
    """Validated job price. A 0/unset price disables the bnbagent budget floor,
    so any caller could fund a near-zero job and drain the agent's gas — refuse
    to start the paid service without an explicit positive raw-unit price."""
    return service_price_raw_units()


def _config() -> ERC8183Config:
    data_dir = _data_dir()
    return ERC8183Config(
        network=os.environ.get("SOLVENT_ERC8183_NETWORK")
        or os.environ.get("SOLVENT_TRADE_NETWORK", "bsc-mainnet"),
        wallet_provider=_wallet(),
        storage=LocalStorageProvider(
            os.environ.get("SOLVENT_ERC8183_STORAGE_DIR") or str(data_dir / "erc8183")
        ),
        service_price=_service_price(),
        agent_url=os.environ.get(
            "SOLVENT_ERC8183_AGENT_URL", "https://solvent.gudman.xyz/erc8183"
        ),
    )


def _execute_job(_job: dict):
    return build_job_response(_data_dir())


def build_app():
    return create_erc8183_app(
        config=_config(),
        on_job=_execute_job,
        task_metadata={"service": SERVICE_ID, "provider": "SOLVENT"},
    )


def __getattr__(name: str):
    # Lazy ASGI target: `uvicorn solvent.commerce.server:app` builds the app on
    # first access (after env is set), so importing this module stays free of
    # wallet/price side effects (and unit-testable).
    if name == "app":
        return build_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
