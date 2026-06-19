"""Competition token allowlist.

Source: official BNB Hack brief ("Eligible tokens: a fixed list of BEP-20
tokens listed on CoinMarketCap (149 tokens)"), transcribed verbatim in
order on 2026-06-10. Trades outside this list do not count.

The brief publishes SYMBOLS only. Symbols alone are ambiguous on BSC
(M, U, B, H, Q, NFT, ...), so live trading additionally requires the
BSC contract address to be pinned in ADDRESSES before a symbol becomes
tradable. A symbol with no pinned (or malformed) address is allowlist-visible
but NOT executable — the kernel refuses to size it.
"""

import re

# Verbatim from the brief (includes the brief's own duplicates: SLX twice,
# USDf and USDF as distinct casings). Order preserved.
RAW_BRIEF_SYMBOLS = [
    "ETH",
    "USDT",
    "USDC",
    "XRP",
    "TRX",
    "DOGE",
    "ZEC",
    "ADA",
    "LINK",
    "BCH",
    "DAI",
    "TON",
    "USD1",
    "USDe",
    "M",
    "LTC",
    "AVAX",
    "SHIB",
    "XAUt",
    "WLFI",
    "H",
    "DOT",
    "UNI",
    "ASTER",
    "DEXE",
    "USDD",
    "ETC",
    "AAVE",
    "ATOM",
    "U",
    "STABLE",
    "FIL",
    "INJ",
    "币安人生",
    "NIGHT",
    "FET",
    "TUSD",
    "BONK",
    "PENGU",
    "CAKE",
    "SIREN",
    "LUNC",
    "ZRO",
    "KITE",
    "FDUSD",
    "BEAT",
    "PIEVERSE",
    "BTT",
    "NFT",
    "EDGE",
    "FLOKI",
    "LDO",
    "B",
    "FF",
    "PENDLE",
    "NEX",
    "STG",
    "AXS",
    "TWT",
    "HOME",
    "RAY",
    "COMP",
    "GWEI",
    "XCN",
    "GENIUS",
    "XPL",
    "BAT",
    "SKYAI",
    "APE",
    "IP",
    "SFP",
    "TAG",
    "NXPC",
    "AB",
    "SAHARA",
    "1INCH",
    "CHEEMS",
    "BANANAS31",
    "RIVER",
    "MYX",
    "RAVE",
    "SNX",
    "FORM",
    "LAB",
    "HTX",
    "USDf",
    "CTM",
    "BDX",
    "SLX",
    "UB",
    "DUCKY",
    "FRAX",
    "BILL",
    "WFI",
    "KOGE",
    "ALE",
    "FRXUSD",
    "USDF",
    "GOMINING",
    "VCNT",
    "GUA",
    "DUSD",
    "SMILEK",
    "0G",
    "BEAM",
    "MY",
    "SLX",
    "SOON",
    "REAL",
    "Q",
    "AIOZ",
    "ZIG",
    "YFI",
    "TAC",
    "lisUSD",
    "CYS",
    "ZAMA",
    "TRIA",
    "HUMA",
    "PLUME",
    "ZIL",
    "XPR",
    "ZETA",
    "BabyDoge",
    "NILA",
    "ROSE",
    "VELO",
    "UAI",
    "BRETT",
    "OPEN",
    "BSB",
    "TOSHI",
    "BAS",
    "ACH",
    "AXL",
    "LUR",
    "ELF",
    "KAVA",
    "APR",
    "IRYS",
    "EURI",
    "XUSD",
    "BARD",
    "DUSK",
    "SUSHI",
    "PEAQ",
    "COAI",
    "BDCA",
    "XAUM",
]

ALLOWED_SYMBOLS = frozenset(RAW_BRIEF_SYMBOLS)
DISCOVERY_SYMBOLS = tuple(dict.fromkeys(RAW_BRIEF_SYMBOLS))

# Floor assets: deep-liquidity stables used for the barbell floor and the
# daily qualification trade. All must be in ALLOWED_SYMBOLS.
FLOOR_SYMBOLS = ("USDT", "USDC", "FDUSD", "DAI", "USD1")

# Sleeve universe: liquid majors eligible for the concentrated momentum
# sleeve. Conservative initial cut — only names with deep BSC DEX liquidity;
# revisited (never widened mid-competition) at the Phase 3 freeze.
SLEEVE_SYMBOLS = (
    "ETH",
    "XRP",
    "DOGE",
    "ADA",
    "LINK",
    "LTC",
    "AVAX",
    "DOT",
    "UNI",
    "BCH",
    "CAKE",
    "TWT",
    "FLOKI",
    "SHIB",
    "FET",
    "INJ",
    "PENDLE",
    "ASTER",
    "AAVE",
    "ETC",
    "FIL",
    "ATOM",
    "TRX",
    "TON",
)

# symbol -> BSC (BEP-20) contract address. Pinned before live trading;
# resolved from authoritative sources (BscScan verified contracts / official
# project channels), never guessed, and cross-checked against the canonical
# Binance-Peg addresses. This dict is the executable GATE (is_executable):
# TWAK swaps by SYMBOL and resolves the token itself, so a pinned address is
# not an execution parameter — it is our human-verified certification that the
# symbol maps to a real, liquid, canonical BSC token before the kernel will
# size it. Each verified 2026-06-11.
#
# DELIBERATELY HELD OUT (allowlist-visible, NOT executable):
#   TRX  — ambiguous on BSC: legacy Binance-Peg contracts vs the newer TRON DAO
#          contract (6 decimals) carry different liquidity; symbol resolution is
#          not certain, so we do not enable it until TWAK's resolver is confirmed.
#   TON  — only a bridged "Wrapped TON Coin" exists on BSC (thin, bridge-dependent
#          liquidity). Excluded on liquidity grounds, consistent with the barbell's
#          deep-liquidity-only sleeve rule.
ADDRESSES: dict[str, str] = {
    # --- floor stables ---
    "USDT": "0x55d398326f99059fF775485246999027B3197955",  # Binance-Peg BSC-USD (18 dec)
    "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",  # Binance-Peg USDC (18 dec)
    "FDUSD": "0xc5f0f7b66764F6ec8C8Dff7BA683102295E16409",  # First Digital USD
    "DAI": "0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3",  # Binance-Peg DAI
    "USD1": "0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d",  # World Liberty Financial USD
    # --- sleeve majors (Binance-Peg unless noted) ---
    "ETH": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
    "XRP": "0x1D2F0da169ceB9fC7B3144628dB156f3F6c60dBE",
    "DOGE": "0xbA2aE424d960c26247Dd6c32edC70B295c744C43",  # 8 dec
    "ADA": "0x3EE2200Efb3400fAbB9AacF31297cBdD1d435D47",
    "LINK": "0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD",
    "LTC": "0x4338665CBB7B2485A8855A139b75D5e34AB0DB94",
    "AVAX": "0x1CE0c2827e2eF14D5C4f29a091d735A204794041",
    "DOT": "0x7083609fCE4d1d8Dc0C979AAb8c869Ea2C873402",
    "UNI": "0xBf5140A22578168FD562DCcF235E5D43A02ce9B1",
    "BCH": "0x8fF795a6F4D97E7887C79beA79aba5cc76444aDf",
    "CAKE": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",  # native PancakeSwap
    "TWT": "0x4B0F1812e5Df2A09796481Ff14017e6005508003",  # native Trust Wallet
    "FLOKI": "0xfb5B838b6cfEEdC2873aB27866079AC55363D37E",  # FLOKI V2 (9 dec)
    "SHIB": "0x2859e4544C4bB03966803b044A93563Bd2D0DD4D",
    "FET": "0x031b41e504677879370e9DBcF937283A8691Fa7f",  # native Fetch.ai (ASI)
    "INJ": "0xa2B726B1145A4773F68593CF171187d8EBe4d495",  # native Injective
    "PENDLE": "0xb3Ed0A426155B79B898849803E3B36552f7ED507",
    "ASTER": "0x000aE314E2A2172a039b26378814C252734f556A",  # native Aster (BNB Chain)
    "AAVE": "0xfb6115445Bff7b52FeB98650C87f44907E58f802",
    "ETC": "0x3d6545b08693daE087E957cb1180ee38B9e3c25E",
    "FIL": "0x0D8Ce2A99Bb6e3B7Db580eD848240e4a0F9aE153",
    "ATOM": "0x0Eb3a705fc54725037CC9e008bDede697f62F335",
}


_VALID_BSC_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _validate_addresses() -> None:
    """A malformed pinned address must fail loudly at import, not silently
    pass `is_executable` and let TWAK resolve/broadcast against a bad pin."""
    for sym, addr in ADDRESSES.items():
        if not _VALID_BSC_ADDR.fullmatch(addr):
            raise ValueError(f"allowlist: malformed BSC address for {sym!r}: {addr!r}")


_validate_addresses()


def is_allowed(symbol: str) -> bool:
    return symbol in ALLOWED_SYMBOLS


def is_executable(symbol: str) -> bool:
    """Allowed AND has a pinned, format-valid BSC contract address."""
    addr = ADDRESSES.get(symbol)
    return (
        symbol in ALLOWED_SYMBOLS
        and bool(addr)
        and bool(_VALID_BSC_ADDR.fullmatch(addr))
    )


def needs_manual_pin(symbol: str) -> bool:
    """Allowlist-visible, but not executable until a BSC address is pinned."""
    return is_allowed(symbol) and not is_executable(symbol)
