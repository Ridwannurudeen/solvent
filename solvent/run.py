"""Runner: assembles a mode (paper/live) and fires decision cycles.

    python -m solvent.run --mode paper --data-dir /opt/solvent/data --once
    python -m solvent.run --mode paper --data-dir ./data --loop 3600

Paper mode: free Binance/alternative.me signals, instant simulated fills
with a 0.25% fee haircut, holdings in a JSON file seeded with $300 USDT.
Live mode: CMC x402 paid signals + TWAK execution (requires the agent
wallet and TWAK credentials on the host).
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .brain.advisor import make_advisor
from .engine import StateStore, run_cycle
from .exec.executor import Journal, PaperExecutor
from .kernel.rules import RiskConfig
from .ops.alerts import alert
from .receipts.chain import ReceiptChain
from .signals.sources import BinanceSource

logger = logging.getLogger(__name__)

PAPER_SEED_USDT = 300.0
PAPER_FEE = 0.0025  # one-side DEX fee haircut applied to every fill


class PaperBook:
    """Simulated holdings, persisted as JSON. Fills at signal price."""

    def __init__(self, path: Path) -> None:
        self.path = path
        if path.exists():
            self.holdings: dict[str, float] = json.loads(path.read_text())
        else:
            self.holdings = {"USDT": PAPER_SEED_USDT}
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.holdings, indent=1))

    def apply(self, intent, signals) -> None:
        stable = {"USDT", "USDC", "FDUSD", "DAI", "USD1"}

        def price_of(sym: str) -> float | None:
            return 1.0 if sym in stable else signals.prices.get(sym)

        p_from, p_to = price_of(intent.from_symbol), price_of(intent.to_symbol)
        if p_from is None or p_to is None or p_from <= 0 or p_to <= 0:
            logger.warning("paper fill skipped, missing price: %s", intent)
            return
        usd = min(
            intent.notional_usd, self.holdings.get(intent.from_symbol, 0.0) * p_from
        )
        if usd <= 0:
            return
        self.holdings[intent.from_symbol] = (
            self.holdings.get(intent.from_symbol, 0.0) - usd / p_from
        )
        self.holdings[intent.to_symbol] = (
            self.holdings.get(intent.to_symbol, 0.0) + (usd * (1 - PAPER_FEE)) / p_to
        )
        self.holdings = {k: v for k, v in self.holdings.items() if v > 1e-12}
        self.save()


class PaperCycleExecutor(PaperExecutor):
    """PaperExecutor that also moves the simulated book."""

    def __init__(self, journal: Journal, book: PaperBook) -> None:
        super().__init__(journal)
        self.book = book
        self._signals = None  # injected per cycle

    def execute(self, intent, cycle_id):
        result = super().execute(intent, cycle_id)
        if result.ok and self._signals is not None and "skipped" not in result.detail:
            self.book.apply(intent, self._signals)
        return result


class _TappedSource:
    """Wraps a source so the executor sees the same signal snapshot."""

    def __init__(self, inner, executor) -> None:
        self.inner = inner
        self.executor = executor

    def fetch(self):
        signals, purchases = self.inner.fetch()
        self.executor._signals = signals
        return signals, purchases


def build_paper(data_dir: Path):
    journal = Journal(data_dir / "journal.jsonl")
    book = PaperBook(data_dir / "paper-holdings.json")
    executor = PaperCycleExecutor(journal, book)
    source = _TappedSource(BinanceSource(), executor)
    return source, executor, journal, book


def build_live(data_dir: Path, cfg: RiskConfig):
    """Assemble the live stack from env credentials — fail-fast, no broadcast
    until a cycle actually executes.

    Required env:
      SOLVENT_PRIVATE_KEY      agent wallet key (signs x402 data payments)
      SOLVENT_WALLET_PASSWORD  keystore password for the above
      TWAK_WALLET_PASSWORD     TWAK signing password (trade execution)
    Optional env:
      SOLVENT_WALLET_ADDRESS   address to read balances from (default: wallet's own)
      SOLVENT_TRADE_NETWORK    bsc-mainnet (default) | bsc-testnet
      SOLVENT_TWAK_CHAIN       TWAK chain name (default: bsc)
    """
    from bnbagent.signing import SigningPolicy
    from bnbagent.wallets import EVMWalletProvider
    from bnbagent.x402 import X402Signer

    from .exec.executor import TwakExecutor
    from .exec.livebook import LiveBook, make_web3
    from .signals.sources import CMCSource
    from .signals.x402pay import TOKEN_DECIMALS, X402MCPClient, X402Payer

    def _require(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise SystemExit(f"{name} is required for --mode live")
        return value

    private_key = _require("SOLVENT_PRIVATE_KEY")
    wallet_password = _require("SOLVENT_WALLET_PASSWORD")
    twak_password = _require("TWAK_WALLET_PASSWORD")
    network = os.environ.get("SOLVENT_TRADE_NETWORK", "bsc-mainnet")
    twak_chain = os.environ.get("SOLVENT_TWAK_CHAIN", "bsc")

    # x402 signer: only the known CMC payment-token domains may be signed, with
    # the per-call cap and session budget from RiskConfig, scaled to each
    # asset's own decimals (Base USDC 6, BSC stables 18).
    policy = SigningPolicy(
        domain_allowlist=frozenset(
            (int(net.split(":")[1]), asset) for (net, asset) in TOKEN_DECIMALS
        ),
        primary_type_allowlist=frozenset({"TransferWithAuthorization"}),
    )
    wallet = EVMWalletProvider(
        password=wallet_password,
        private_key=private_key,
        persist=False,
        signing_policy=policy,
    )
    max_call_usd = cfg.x402_max_per_call_usdc / 1e6
    budget_usd = cfg.x402_session_budget_usdc / 1e6
    signer = X402Signer(
        wallet,
        max_value_per_call={
            asset: int(max_call_usd * 10**dec)
            for (_n, asset), dec in TOKEN_DECIMALS.items()
        },
        session_budget={
            asset: int(budget_usd * 10**dec)
            for (_n, asset), dec in TOKEN_DECIMALS.items()
        },
    )
    source = CMCSource(X402MCPClient(payer=X402Payer(signer)))

    journal = Journal(data_dir / "journal.jsonl")
    executor = TwakExecutor(
        journal,
        password=twak_password,
        chain=twak_chain,
        slippage_pct=cfg.max_slippage_pct,
    )

    wallet_address = os.environ.get("SOLVENT_WALLET_ADDRESS") or wallet.address
    book = LiveBook(make_web3(network), wallet_address)
    return source, executor, journal, book


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["paper", "live"], required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true")
    group.add_argument("--loop", type=int, metavar="SECONDS")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    data_dir = args.data_dir
    receipts = ReceiptChain(data_dir / "receipts.jsonl")
    store = StateStore.load(data_dir / "state.json")
    cfg = RiskConfig()
    # Opt-in: the regime brain costs API credits, so it's off unless asked.
    advisor = make_advisor() if os.environ.get("SOLVENT_USE_ADVISOR") == "1" else None

    if args.mode == "live":
        source, executor, journal, book = build_live(data_dir, cfg)

        def holdings_now() -> dict[str, float]:
            # Re-read on-chain balances each cycle; floor stables + open sleeve.
            pos = store.position["symbol"] if store.position else None
            return book.snapshot(pos)
    else:
        source, executor, journal, book = build_paper(data_dir)

        def holdings_now() -> dict[str, float]:
            return dict(book.holdings)

    def one_cycle() -> None:
        try:
            summary = run_cycle(
                source=source,
                executor=executor,
                journal=journal,
                receipts=receipts,
                store=store,
                holdings=holdings_now(),
                cfg=cfg,
                advisor=advisor,
            )
            if summary["intents"] > 0 or summary["degraded"]:
                alert(f"SOLVENT [{args.mode}] {json.dumps(summary)}")
            heartbeat = data_dir / "heartbeat"
            heartbeat.write_text(datetime.now(timezone.utc).isoformat())
        except Exception:
            logger.exception("cycle failed")
            alert(f"SOLVENT [{args.mode}] CYCLE FAILED — check logs")

    if args.once:
        one_cycle()
        return 0
    while True:
        one_cycle()
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
