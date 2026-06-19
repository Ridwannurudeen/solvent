# SOLVENT — the glass-box trading agent

> BNB Hack: AI Trading Agent Edition (CoinMarketCap × Trust Wallet × BNB Chain)

An autonomous BNB Smart Chain trading agent whose distinguishing feature is **honesty you can verify**: every decision the agent makes is written as a hash-chained **decision receipt** — what data it bought, what that cost, the regime it inferred, its thesis, the intents it produced, and the resulting transactions. The chain head is anchored on-chain daily under the agent's ERC-8004 identity, so the whole record is tamper-evident and publicly auditable through wallet-signed checkpoints.

**Live BSC mainnet agent:** https://solvent.gudman.xyz — live wallet holdings, equity vs BNB buy-and-hold, current barbell allocation, liveness heartbeat, local chain verification, anchor coverage, mainnet ERC-8004 anchors, and the full receipt stream.

Public API: [`/receipts`](https://solvent.gudman.xyz/receipts) · [`/verify`](https://solvent.gudman.xyz/verify) · [`/state`](https://solvent.gudman.xyz/state) · [`/policy`](https://solvent.gudman.xyz/policy) · [`/policy-compliance`](https://solvent.gudman.xyz/policy-compliance) · [`/passport`](https://solvent.gudman.xyz/passport) · [`/signal`](https://solvent.gudman.xyz/signal) · [`/inference-commitments`](https://solvent.gudman.xyz/inference-commitments) · [`/inference-verification`](https://solvent.gudman.xyz/inference-verification) · [`/strategy-evidence`](https://solvent.gudman.xyz/strategy-evidence)

Submission evidence packet: [`EVIDENCE.md`](EVIDENCE.md)
Live cutover checklist: [`LIVE_CUTOVER.md`](LIVE_CUTOVER.md)
Submission packet: [`SUBMISSION_PACKET.md`](SUBMISSION_PACKET.md)

---

## Why a glass box

A self-custody user can't audit a black-box trader — they just have to trust it. SOLVENT inverts that: the agent emits a receipt every cycle and anchors the log on-chain, so anyone can pull the public log, recompute the hash chain, and check the head against the on-chain anchor. Nobody else in the field is doing decision receipts + an ERC-8004 trading identity; that's the originality lead.

The default trading strategy is a disciplined **barbell** built to stay alive through a drawdown-DQ tournament (≥1 trade/day, 30% trailing-drawdown disqualification) while keeping convex upside if the momentum sleeve catches a move. The repo also includes a research-tested `conviction_50` profile for the scored window; it is not activated unless `SOLVENT_RISK_PROFILE=conviction_50` is explicitly set.

## Architecture

```
solvent/
  kernel/        deterministic decision core — no I/O, no LLM, no randomness
    rules.py       RiskConfig: every money-controlling number (the "constitution")
    allocator.py   (state, signals, cfg) -> intents : sizing, stops, ratchet, kill switch
    allowlist.py   competition token allowlist + pinned BSC contract gate
    state.py       PortfolioState / MarketSignals / SleevePosition
  signals/       market data layer (CMC x402 primary + Binance cross-check in live)
  brain/
    advisor.py     opt-in Claude regime advisor — a ONE-WAY de-risk ratchet only
  exec/
    executor.py    TwakExecutor (live, one-tx-per-intent) + PaperExecutor
    livebook.py    live holdings reader + tx receipt/log/balance settlement verifier
  receipts/
    chain.py       hash-chained receipt log + verify_chain()
    pretrade.py    optional ERC-8004 pre-trade commit publisher
    server.py      read-only HTTP API (/, /receipts, /verify, /state, /signal, /passport)
    anchor.py      daily ERC-8004 on-chain anchor of the chain head
  policy/
    manifest.py    signed, anchorable scored-week mandate generator
    verify.py      Proof-of-Policy compliance verifier + risk passport
  commerce/
    signal.py      ERC-8183-ready paid regime signal deliverable
    server.py      FastAPI ERC-8183 provider for funded buyer jobs
  ops/           deadman, watchdog, alerts, preflight, readiness, execution recovery
  research/      historical/stress backtests for comparing risk profiles
  engine.py      run_cycle(): one decision heartbeat
  run.py         runner — assembles paper/live mode and fires cycles
```

The cardinal rule: **the kernel is pure and deterministic.** The LLM advisor can feed a reconciled regime into the allocator, but sizing, gating, stops, the ratchet, and the kill switch are decided by `allocator.py` alone — and the advisor is wired so it can only ever *de-risk*, never force or enlarge a trade.

## Strategy — the barbell (all values in `kernel/rules.py`)

- **Floor:** ≥ **75%** of equity stays in floor stables (`floor_frac_min = 0.75`) at all times; floor assets are conservatively marked at `min(reference price, $1)` minus the configured stable haircut.
- **Sleeve:** at most **one** concurrent position (`max_positions = 1`), target **22%** of equity (`sleeve_frac_target = 0.22`), rotated into the single highest-momentum *executable* allowlist token above an entry bar.
- **Entry gate:** flat **and** regime is `risk-on` **and** a candidate clears `MIN_ENTRY_MOMO`. Regime is classified conservatively from Fear & Greed + funding; missing or cross-source-divergent data downgrades the regime.
- **Per-position protection:** hard stop at **−12%** from entry (`stop_pct`), plus a momentum-decay exit when the score falls below **50%** of its entry value.
- **Lock-in ratchet** (anti peak-drawdown DQ): after banked gains the sleeve cap tightens — **≥10% → 15%**, **≥20% → 10%**, **≥35% → 5%** — so winnings are de-risked into the floor rather than ridden back down.
- **Degraded-data unwind:** if a sleeve is open and data verification degrades, the allocator exits to floor instead of passively holding volatile risk.
- **Kill switch:** if trailing drawdown from peak hits **22%** (`kill_switch_drawdown_pct`, a buffer below the 30% DQ line), liquidate the sleeve and latch `HALTED` until an explicit operator resume.
- **Daily qualification:** a deadman path fires a $2 stable→stable micro-rotation after 20:00 UTC if no qualifying trade happened and the runtime is not persistently halted.
- **Profit protection:** breakeven, trailing-stop, and partial take-profit rules exist in the kernel, but default live profiles leave them disabled unless a frozen policy manifest explicitly activates non-`999` thresholds.
- **Competitive profile:** `conviction_50` keeps a 50% stable floor, can deploy a 48% sleeve, tightens the stop to 8%, requires a 10.0 momentum score, and suppresses momentum-decay exits for 48h. It is designed to reduce fee churn while keeping more upside than the default safety profile.

## Decision receipts — and how to verify them

Each receipt line in `data/receipts.jsonl` commits to line *N-1*'s hash (`prev_hash`), so any mutation breaks every subsequent hash. A normal hold cycle appends a `cycle_summary`; a money-moving intent appends a local `pre_trade_commit` before execution, an `execution_seal` after the result, and then the `cycle_summary`.

```jsonc
{"receipt": {
  "seq": 5, "prev_hash": "0x…", "ts": "2026-06-12T04:00:25+00:00",
  "phase": "cycle_summary", "cycle_id": "20260612T04",
  "data_purchases": [{"tool": "get_crypto_quotes_latest", "cost_usdc": 0.01, "ok": true, "response_hash": "0x…"}],
  "signals": {"fear_greed": 12, "regime_deterministic": "risk-off", "advisor": null, …},
  "regime": "risk-off", "thesis": "hold (risk-off)",
  "intents": [{"kind": "qualify", "from": "USDT", "to": "USDC", "notional_usd": 2.0}],
  "executions": [{"key": "…", "ok": true, "outcome": "executed_now", "tx_hash": "…", "verification": {"from_transfer_out": 2.0}}],
  "equity_usd": 300.0, "dq_headroom_pct": 0.3
}, "hash": "0x…"}
```

Verify the published log yourself — zero trust, zero dependencies (`verify_receipts.py` is stdlib-only and never imports the agent's code):

```bash
curl -s https://solvent.gudman.xyz/verify        # the agent's own claim: head hash + count
python verify_receipts.py                         # recompute the chain from the public log yourself
# OK - 15 receipts, chain intact
# head: 0x6fed532a808fef46...dff012eec
```

The standalone verifier recomputes every receipt hash from genesis and checks each `prev_hash` link; the head it prints must match `/verify` (which runs the same check server-side) and, once the ERC-8004 anchor is registered, the head posted on-chain daily — closing the loop from public log → independently recomputed chain → on-chain commitment.

For the strongest anti-hindsight mode, `SOLVENT_PRETRADE_ANCHOR=1` publishes each pre-trade commit hash to ERC-8004 metadata before the TWAK swap. It is enabled in production proof mode via systemd override because it adds one cheap BSC metadata transaction before every trade.

## Inference commitments and ERC-8183 signal sales

Every new decision receipt includes a hash-bound inference commitment packet:

- `input_hash`: stable hash of the exact signal snapshot, deterministic regime, active risk profile, and open-position context.
- `output_hash`: stable hash of the effective regime and optional advisor output.
- `commitment_hash` / `proof_hash`: the verifier-facing commitment tying mode, model/kernel ID, input hash, and output hash together.

This is not a TEE attestation and does not prove that a declared model ran. It lets reviewers verify that the declared inputs and outputs were hash-bound into the receipt chain rather than written after the fact. Public commitments are exposed at `/inference-commitments`; `/inference-proofs` remains as a compatibility alias. `/inference-verification` goes one step further for deterministic cycles: it recomputes the input/output hashes, re-runs the committed deterministic regime classifier from the committed signal bytes, and verifies that the effective regime reconciliation matches the kernel's rules. It still does not claim TEE, zk, or external model runtime attestation.

## Strategy evidence, not guaranteed alpha

No repository can honestly guarantee a trading edge. SOLVENT treats edge as an evidence question: `python -m solvent.research.report` and `/strategy-evidence` compare each risk profile against hold-stables, best buy-and-hold, and equal-weight buy-and-hold benchmarks in declared stress scenarios. A profile only earns an evidence point when it beats the relevant benchmark without breaching the drawdown gate. The scored-week policy remains the signed manifest; the strategy report is supporting evidence, not a promise of future PnL.

SOLVENT also exposes its latest daily regime read as an ERC-8183-ready paid signal. `/signal` returns a verifiable `solvent.daily-regime-signal` payload whose `signal_hash` binds to the latest receipt hash, current chain head, latest anchor, and inference commitment hash. For paid buyers, `solvent.commerce.server` runs the BNB Agent SDK ERC-8183 provider: funded jobs assigned to the agent wallet receive the same signal as a `DeliverableManifest`, and `ERC8183JobOps.submit_result()` submits the manifest hash on-chain.

## Policy manifest

The scored-week mandate can be frozen as canonical JSON:

```bash
python -m solvent.policy.manifest --profile safety --out ./data/policy-manifest.json
# optionally sign the manifest hash without printing the key:
SOLVENT_PRIVATE_KEY=... python -m solvent.policy.manifest --profile safety --sign-env --out ./data/policy-manifest.json
SOLVENT_ANCHOR_BACKEND=twak SOLVENT_AGENT_ID=... python -m solvent.policy.manifest --profile safety --sign-env --anchor --out ./data/policy-manifest.json
```

The published `/policy` endpoint serves only that file; it returns 404 until a manifest is explicitly generated. Each manifest includes the git commit, risk profile, numerical limits, pinned token addresses, data/execution requirements, settlement-verification requirements, result outcome states, `manifest_hash`, optional wallet signature, and optional ERC-8004 policy anchor tx.

`python -m solvent.policy.verify --data-dir ./data` recomputes the manifest hash, verifies the declared manifest signature plus the ERC-8004 wallet anchor, checks receipt-chain integrity, enforces policy-scoped intent/result/settlement rules, checks x402 response commitments and spend caps, and emits a compact agent risk passport. The same report is public at `/policy-compliance`; `/passport` is an alias for judges who want the high-level metrics.

## ERC-8004 on-chain anchoring

`receipts/anchor.py` posts the chain head as ERC-8004 metadata under SOLVENT's registered identity, one cheap tx/day. The BNB Hack path uses the TWAK CLI/keychain on **BSC mainnet**, so anchoring reuses the same self-custody wallet as execution and does not require a raw private key in the anchor environment.

```bash
# one-time identity registration (prints SOLVENT_AGENT_ID)
SOLVENT_ANCHOR_BACKEND=twak SOLVENT_BSC_NETWORK=bsc-mainnet \
  python -m solvent.receipts.anchor --data-dir ./data --register
# daily anchor (idempotent per UTC day via data/anchors.json)
SOLVENT_ANCHOR_BACKEND=twak SOLVENT_BSC_NETWORK=bsc-mainnet SOLVENT_AGENT_ID=... \
  python -m solvent.receipts.anchor --data-dir ./data
```

## Data & x402 spend metering

Signals come from the data layer (paper mode: free Binance tickers + alternative.me Fear & Greed; live mode: CoinMarketCap x402 primary data with Binance public REST as an independent price cross-check). Every paid call is recorded in the receipt's `data_purchases` with its USD cost, response hash, and response byte count; a daily durable spend ledger plus per-call cap live in `RiskConfig` (`x402_session_budget_usdc`, `x402_max_per_call_usdc`). The point is honest metering surfaced per decision — not a performance claim. On BSC, SOLVENT prefers CMC's USD1 EIP-3009 offer because BSC USDC is currently permit2-only.

## Run it

Requires Python ≥ 3.12.

```bash
pip install -e .
python -m pytest -q                                   # full test suite
python -m solvent.run --mode paper --data-dir ./data --once     # one cycle
python -m solvent.run --mode paper --data-dir ./data --loop 3600  # hourly
python -m solvent.receipts.server --data-dir ./data --port 3078   # serve the glass box
python -m solvent.ops.readiness --data-dir ./data --profile submission
python -m solvent.policy.verify --data-dir ./data                  # policy compliance + risk passport
python -m solvent.ops.watcher --public-base https://solvent.gudman.xyz
python -m solvent.research.backtest --days 30 --interval 1h       # compare risk profiles
python -m solvent.research.report                                  # stress + benchmark evidence
python -m solvent.research.scan_universe --json                   # review-only candidate scanner
python -m solvent.commerce.signal --data-dir ./data               # latest signal payload
```

Paper mode fills instantly at signal price with a 0.25% fee haircut, seeded with $300 USDT.
`SOLVENT_RISK_PROFILE` selects a named profile (`safety` default, `tournament_50`, `conviction_50`, `tournament_60`) for paper or live runs.
Set `SOLVENT_ADAPTIVE_PROFILE=1` to let SOLVENT auto-switch between
`safety` / `conviction_50` / `tournament_60` per cycle based on live
Fear & Greed, momentum strength, and drawdown headroom; this stays inside the
existing kill-switch, 1-trade/day, and daily fallback constraints.
The Claude regime advisor is **opt-in** (`SOLVENT_USE_ADVISOR=1`) — off by default so no API credits are spent unless asked.

## Status

- **Built + tested:** deterministic kernel, paper execution loop, live TWAK/CMC stack, Binance live price cross-check, receipt hash-chain, raw data response commitments, inference commitments, deterministic inference re-execution verification, signed/anchorable policy manifest generator, Proof-of-Policy verifier/risk passport, independent public watcher attestations with a systemd archive timer, ERC-8183 signal provider, read-only API, ERC-8004 anchors, pre-trade anchors, intent-aware settlement verification, persistent halt latch, atomic local state writes, opt-in regime advisor, adaptive profile mode, ops armor (deadman + watchdog + systemd units), benchmarked strategy-evidence reports, and risk-profile backtests.
- **Live now:** production live-mode rehearsal is running hourly on BSC mainnet from `/opt/solvent/data-prod`; public holdings are read from the funded TWAK wallet; ERC-8004 identity `136384` and receipt-chain anchors are live on BSC mainnet.
- **Allowlist gate:** 22 sleeve majors + 5 floor stables have pinned, source-verified BSC contracts; `TRX` and `TON` are deliberately held out (ambiguous / thin-liquidity resolution) until confirmed.
- **Scored-window gate:** the live stack is active before the June 22 trading window; do not present pre-window rehearsal PnL as scored-week PnL. Remaining gates are operational: keep the wallet funded, keep x402 USD1 available, keep watchdog/deadman timers healthy, and publish the repo/demo only after approval.

## BNB Hack alignment

| Requirement | SOLVENT status |
|---|---|
| Track 1 autonomous agent | Built as an executable hourly agent with deterministic rules, liveness heartbeat, watchdog, deadman daily qualification path, and systemd timers. |
| Reads markets via CMC | Live mode uses `CMCSource` through the x402 MCP client, with `get_global_metrics_latest`, `get_crypto_quotes_latest`, and conditional derivatives metrics recorded per receipt. |
| Signs/executes via TWAK | `TwakExecutor` is the only live execution path; mined swaps must pass receipt status, sender, transfer-log, and balance-delta checks before state changes apply. |
| User-defined rules | Risk constitution enforces allowlist, conservative stable marks, one-position cap, floor reserve, per-trade sizing, slippage, stop, drawdown kill switch, persistent halt, and lock-in ratchet. |
| Live BSC trading week | Registered and already running live-mode rehearsal on BSC mainnet; scored-window results begin June 22. |
| On-chain Track 1 registration | Registered in the BNB Hack competition contract: `0xc4cdba129a1fb12714542ab991255c692240d6eb8bdfa716576199f9d31bda3a`. |
| On-chain proof | ERC-8004 agent `136384` on BSC mainnet with daily receipt-chain anchors plus optional pre-trade anchors. |
| Submission package | Public repo is live at `https://github.com/Ridwannurudeen/solvent`; demo video and DoraHacks submission remain approval-gated. |
| Reproducible proof | `/policy-compliance`, `/inference-verification`, `/strategy-evidence`, and local verifier CLIs expose policy checks, deterministic re-execution checks, benchmark evidence, and the agent risk passport from public/local logs. |

## Rubric map

| Rubric line | SOLVENT evidence |
|---|---|
| TWAK execution depth | sole execution path; rebuilding `exec/` is the only way to replace it |
| Self-custody integrity | keys never leave the host; local signing through the whole loop |
| Autonomous execution + guardrails | hands-off cycles + codified caps / allowlist / ratchet / kill switch, all visible on the dashboard |
| Native x402 | per-call data spend metered into every receipt |
| Originality | glass-box receipts + ERC-8004 trading identity — an agent a self-custody user can actually audit |
| CMC Agent Hub | multi-endpoint signals with per-decision cost metering |
| BNB AI Agent SDK | ERC-8004 identity + daily/on-demand receipt anchors + ERC-8183 signal sales |
