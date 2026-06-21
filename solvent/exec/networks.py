"""Execution network helpers shared by live trading and TWAK-backed ops."""

TWAK_CHAIN_BY_NETWORK = {
    "bsc": "bsc",
    "bsc-mainnet": "bsc",
    "bsctestnet": "bsctestnet",
    "bsc-testnet": "bsctestnet",
}


def twak_chain_for_network(network: str) -> str:
    return TWAK_CHAIN_BY_NETWORK.get(network, network)


def resolve_twak_chain(network: str, configured: str | None) -> str:
    expected = twak_chain_for_network(network)
    if not configured:
        return expected
    if configured != expected:
        raise ValueError(
            f"SOLVENT_TWAK_CHAIN={configured!r} does not match "
            f"SOLVENT_TRADE_NETWORK={network!r}; expected {expected!r}"
        )
    return configured
