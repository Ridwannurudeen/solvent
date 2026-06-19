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
from collections.abc import Callable
import time
from datetime import datetime, timezone
from pathlib import Path

from .brain.advisor import make_advisor
from .engine import StateStore, run_cycle
from .exec.executor import Journal, PaperExecutor
from .kernel.rules import RiskConfig, risk_config_for_profile
from .kernel.state import MarketSignals, PortfolioState
from .ops.alerts import alert
from .ops.lock import SingleWriterLock
from .receipts.chain import ReceiptChain
from .receipts.pretrade import build_pretrade_publisher
from .signals.sources import BinanceSource

logger = logging.getLogger(__name__)

PAPER_SEED_USDT = 300.0
PAPER_FEE = 0.0025  # one-side DEX fee haircut applied to every fill

_PROFILE_ORDER = ("safety", "conviction_50", "tournament_50", "tournament_60")


def _profile_rank(profile: str) -> int:
    try:
        return _PROFILE_ORDER.index(profile)
    except ValueError as exc:
        raise ValueError(f"unknown risk profile: {profile}") from exc


def _clamp_profile(profile: str, max_profile: str) -> str:
    base_rank = _profile_rank(max_profile)
    return profile if _profile_rank(profile) <= base_rank else max_profile


def _adaptive_profile_selector(
    base_profile: str,
) -> Callable[[RiskConfig, PortfolioState, MarketSignals], RiskConfig]:
    if base_profile not in _PROFILE_ORDER:
        raise ValueError(f"unknown risk profile: {base_profile}")

    def _selector(
        _cfg: RiskConfig, state: PortfolioState, signals: MarketSignals
    ) -> RiskConfig:
        if signals.degraded or signals.fear_greed is None:
            return risk_config_for_profile("safety")
        if (
            signals.fear_greed < 35
            or state.dq_headroom_pct < 0.08
            or state.trailing_drawdown_pct > 0.14
            or state.banked_gain_pct < -0.10
        ):
            return risk_config_for_profile("safety")

        top_momentum = max(signals.momentum.values(), default=0.0)
        funding = signals.btc_funding_rate or 0.0

        if (
            signals.fear_greed >= 68
            and top_momentum >= 8.0
            and funding < 0.001
            and state.equity_usd > state.start_equity_usd * 0.98
        ):
            target = "tournament_60"
        elif signals.fear_greed >= 50 and top_momentum >= 3.0:
            target = "conviction_50"
        else:
            target = "safety"
        return risk_config_for_profile(_clamp_profile(target, base_profile))

    return _selector


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
        if result.applies_state_change and self._signals is not None:
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
      SOLVENT_WALLET_ADDRESS   TWAK trading wallet address to read balances from
    Optional env:
      TWAK_WALLET_PASSWORD     TWAK signing password; otherwise keychain fallback
      SOLVENT_TRADE_NETWORK    bsc-mainnet (default) | bsc-testnet
      SOLVENT_TWAK_CHAIN       TWAK chain name (default: bsc)
    """
    from bnbagent.signing import SigningPolicy
    from bnbagent.wallets import EVMWalletProvider
    from bnbagent.x402 import X402Signer

    from .exec.executor import TwakExecutor
    from .exec.livebook import LiveBook, LiveReceiptVerifier, make_web3
    from .signals.sources import CMCSource, CrossCheckedSource
    from .signals.x402pay import TOKEN_DECIMALS, SpendLedger, X402MCPClient, X402Payer

    def _require(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise SystemExit(f"{name} is required for --mode live")
        return value

    private_key = _require("SOLVENT_PRIVATE_KEY")
    wallet_password = _require("SOLVENT_WALLET_PASSWORD")
    wallet_address = _require("SOLVENT_WALLET_ADDRESS")
    twak_password = os.environ.get("TWAK_WALLET_PASSWORD")
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
    cmc_source = CMCSource(
        X402MCPClient(
            payer=X402Payer(signer),
            spend_ledger=SpendLedger(
                data_dir / "x402-spend.jsonl", daily_budget_usd=budget_usd
            ),
        )
    )
    source = CrossCheckedSource(
        cmc_source,
        BinanceSource(),
        max_deviation_pct=float(os.environ.get("SOLVENT_PRICE_DEVIATION_MAX_PCT", "5")),
    )

    book = LiveBook(make_web3(network), wallet_address)
    receipt_verifier = LiveReceiptVerifier(
        book,
        slippage_pct=cfg.max_slippage_pct,
        timeout_s=int(os.environ.get("SOLVENT_TX_RECEIPT_TIMEOUT_S", "180")),
        confirmations=int(os.environ.get("SOLVENT_TX_CONFIRMATIONS", "1")),
    )

    journal = Journal(data_dir / "journal.jsonl")
    executor = TwakExecutor(
        journal,
        password=twak_password,
        chain=twak_chain,
        slippage_pct=cfg.max_slippage_pct,
        receipt_verifier=receipt_verifier,
        balance_reader=receipt_verifier.before,
    )

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
    try:
        base_profile = os.environ.get("SOLVENT_RISK_PROFILE", "safety")
        cfg = risk_config_for_profile(base_profile)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    adaptive_selector = (
        _adaptive_profile_selector(base_profile)
        if os.environ.get("SOLVENT_ADAPTIVE_PROFILE") == "1"
        else None
    )
    # Opt-in: the regime brain costs API credits, so it's off unless asked.
    advisor = make_advisor() if os.environ.get("SOLVENT_USE_ADVISOR") == "1" else None
    pretrade_publisher = build_pretrade_publisher()

    if args.mode == "live":
        if (data_dir / "paper-holdings.json").exists() and os.environ.get(
            "SOLVENT_ALLOW_LIVE_SHARED_DATA"
        ) != "1":
            raise SystemExit(
                "live mode refuses a data directory containing paper-holdings.json; "
                "set SOLVENT_DATA_DIR to an isolated live directory"
            )
        source, executor, journal, book = build_live(data_dir, cfg)

        def holdings_now() -> dict[str, float]:
            # Re-read every pinned token each cycle, including residual balances.
            return book.snapshot_all()
    else:
        source, executor, journal, book = build_paper(data_dir)

        def holdings_now() -> dict[str, float]:
            return dict(book.holdings)

    def one_cycle() -> bool:
        try:
            with SingleWriterLock(data_dir / "writer.lock"):
                holdings = holdings_now()
                if args.mode == "live":
                    (data_dir / "live-holdings.json").write_text(
                        json.dumps(
                            {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "holdings": holdings,
                            },
                            separators=(",", ":"),
                        )
                    )
                summary = run_cycle(
                    source=source,
                    executor=executor,
                    journal=journal,
                    receipts=receipts,
                    store=store,
                    holdings=holdings,
                    cfg=cfg,
                    cfg_selector=adaptive_selector,
                    advisor=advisor,
                    pretrade_publisher=pretrade_publisher,
                )
                heartbeat = data_dir / "heartbeat"
                heartbeat.write_text(datetime.now(timezone.utc).isoformat())
            if summary["intents"] > 0 or summary["degraded"]:
                alert(f"SOLVENT [{args.mode}] {json.dumps(summary)}")
            return True
        except RuntimeError as exc:
            if "state writer already active" in str(exc):
                logger.warning("%s", exc)
                return True
            logger.exception("cycle failed")
            alert(f"SOLVENT [{args.mode}] CYCLE FAILED — check logs")
            return False
        except Exception:
            logger.exception("cycle failed")
            alert(f"SOLVENT [{args.mode}] CYCLE FAILED — check logs")
            return False

    if args.once:
        return 0 if one_cycle() else 1
    while True:
        one_cycle()
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
