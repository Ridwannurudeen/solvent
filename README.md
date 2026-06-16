# SOLVENT — the glass-box trading agent

> BNB Hack: AI Trading Agent Edition (CoinMarketCap × Trust Wallet × BNB Chain)

An autonomous BNB Smart Chain trading agent whose distinguishing feature is **honesty you can verify**: every decision the agent makes is written as a signed, hash-chained **decision receipt** — what data it bought, what that cost, the regime it inferred, its thesis, the intents it produced, and the resulting transactions. The chain head is anchored on-chain daily under the agent's ERC-8004 identity, so the whole record is tamper-evident and publicly auditable.

**Live (paper mode):** https://solvent.gudman.xyz — equity vs BNB buy-and-hold, current barbell allocation, liveness heartbeat, a live `CHAIN VERIFIED` badge, and the full receipt stream.

Public API: [`/receipts`](https://solvent.gudman.xyz/receipts) · [`/verify`](https://solvent.gudman.xyz/verify) · [`/state`](https://solvent.gudman.xyz/state)

---

## Why a glass box

A self-custody user can't audit a black-box trader — they just have to trust it. SOLVENT inverts that: the agent emits a receipt every cycle and anchors the log on-chain, so anyone can pull the public log, recompute the hash chain, and check the head against the on-chain anchor. Nobody else in the field is doing decision receipts + an ERC-8004 trading identity; that's the originality lead.

The trading strategy is deliberately **not** pitched as alpha. It is a disciplined **barbell** built to stay alive through a drawdown-DQ tournament (≥1 trade/day, 30% trailing-drawdown disqualification) while keeping convex upside if the momentum sleeve catches a move.

## Architecture

```
solvent/
  kernel/        deterministic decision core — no I/O, no LLM, no randomness
    rules.py       RiskConfig: every money-controlling number (the "constitution")
    allocator.py   (state, signals, cfg) -> intents : sizing, stops, ratchet, kill switch
    allowlist.py   competition token allowlist + pinned BSC contract gate
    state.py       PortfolioState / MarketSignals / SleevePosition
  signals/       market data layer (Binance + Fear&Greed paper source; CMC x402 for live)
  brain/
    advisor.py     opt-in Claude regime advisor — a ONE-WAY de-risk ratchet only
  exec/
    executor.py    TwakExecutor (live, one-tx-per-intent) + PaperExecutor
    livebook.py    live on-chain holdings reader (floor stables + open sleeve)
  receipts/
    chain.py       hash-chained receipt log + verify_chain()
    server.py      read-only HTTP API (/, /receipts, /verify, /state, /summary)
    anchor.py      daily ERC-8004 on-chain anchor of the chain head
  ops/           deadman (independent qualification trade), watchdog, alerts
  engine.py      run_cycle(): one decision heartbeat
  run.py         runner — assembles paper/live mode and fires cycles
```

The cardinal rule: **the kernel is pure and deterministic.** The LLM advisor can feed a reconciled regime into the allocator, but sizing, gating, stops, the ratchet, and the kill switch are decided by `allocator.py` alone — and the advisor is wired so it can only ever *de-risk*, never force or enlarge a trade.

## Strategy — the barbell (all values in `kernel/rules.py`)

- **Floor:** ≥ **75%** of equity stays in floor stables (`floor_frac_min = 0.75`) at all times.
- **Sleeve:** at most **one** concurrent position (`max_positions = 1`), target **22%** of equity (`sleeve_frac_target = 0.22`), rotated into the single highest-momentum *executable* allowlist token above an entry bar.
- **Entry gate:** flat **and** regime is `risk-on` **and** a candidate clears `MIN_ENTRY_MOMO`. Regime is classified conservatively from Fear & Greed + funding; missing data only ever downgrades the regime.
- **Per-position protection:** hard stop at **−12%** from entry (`stop_pct`), plus a momentum-decay exit when the score falls below **50%** of its entry value.
- **Lock-in ratchet** (anti peak-drawdown DQ): after banked gains the sleeve cap tightens — **≥10% → 15%**, **≥20% → 10%**, **≥35% → 5%** — so winnings are de-risked into the floor rather than ridden back down.
- **Kill switch:** if trailing drawdown from peak hits **22%** (`kill_switch_drawdown_pct`, a buffer below the 30% DQ line), liquidate the sleeve and freeze.
- **Daily qualification:** a deadman path fires a $2 stable→stable micro-rotation after 20:00 UTC if no qualifying trade happened, satisfying the ≥1-trade/day rule even with every data feed down.

## Decision receipts — and how to verify them

Each cycle appends one receipt to `data/receipts.jsonl`. Line *N* commits to line *N−1*'s hash (`prev_hash`), so any mutation breaks every subsequent hash.

```jsonc
{"receipt": {
  "seq": 5, "prev_hash": "0x…", "ts": "2026-06-12T04:00:25+00:00",
  "data_purchases": [{"tool": "binance:ticker24h+7d", "cost_usdc": 0.0, "ok": true}],
  "signals": {"fear_greed": 12, "regime_deterministic": "risk-off", "advisor": null, …},
  "regime": "risk-off", "thesis": "hold (risk-off)",
  "intents": [{"kind": "qualify", "from": "USDT", "to": "USDC", "notional_usd": 2.0}],
  "executions": [{"key": "…", "ok": true, "tx_hash": "…"}],
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

## ERC-8004 on-chain anchoring

`receipts/anchor.py` posts the chain head as ERC-8004 metadata under SOLVENT's registered identity, one cheap tx/day. On **bsc-testnet** this is **gasless** via the MegaFuel paymaster (`use_paymaster=True`), so registration and anchoring need no testnet BNB — only a wallet key. Built against `bnbagent==0.3.6`.

```bash
# one-time identity registration (prints SOLVENT_AGENT_ID)
SOLVENT_WALLET_PASSWORD=… SOLVENT_PRIVATE_KEY=0x… \
  python -m solvent.receipts.anchor --data-dir ./data --register
# daily anchor (idempotent per UTC day via data/anchors.json)
SOLVENT_AGENT_ID=… SOLVENT_WALLET_PASSWORD=… SOLVENT_PRIVATE_KEY=0x… \
  python -m solvent.receipts.anchor --data-dir ./data
```

## Data & x402 spend metering

Signals come from the data layer (paper mode: free Binance tickers + alternative.me Fear & Greed; live mode: CoinMarketCap x402 keyless endpoints). Every paid call is recorded in the receipt's `data_purchases` with its USDC cost, and a session budget + per-call cap live in `RiskConfig` (`x402_session_budget_usdc`, `x402_max_per_call_usdc`). The point is honest metering surfaced per decision — not a performance claim.

## Run it

Requires Python ≥ 3.12.

```bash
pip install -e .
python -m pytest -q                                   # 108 tests
python -m solvent.run --mode paper --data-dir ./data --once     # one cycle
python -m solvent.run --mode paper --data-dir ./data --loop 3600  # hourly
python -m solvent.receipts.server --data-dir ./data --port 3078   # serve the glass box
```

Paper mode fills instantly at signal price with a 0.25% fee haircut, seeded with $300 USDT.
The Claude regime advisor is **opt-in** (`SOLVENT_USE_ADVISOR=1`) — off by default so no API credits are spent unless asked.

## Status

- **Built + tested:** deterministic kernel, paper execution loop, receipt hash-chain, read-only API, ERC-8004 anchor, opt-in regime advisor, ops armor (deadman + watchdog + systemd units). **108 tests, ruff-clean.**
- **Live now:** paper agent running hourly on a VPS with the dashboard public at solvent.gudman.xyz; receipts accumulating autonomously.
- **Allowlist gate:** 22 sleeve majors + 5 floor stables have pinned, source-verified BSC contracts; `TRX` and `TON` are deliberately held out (ambiguous / thin-liquidity resolution) until confirmed.
- **Live mode wired (credential-gated):** `--mode live` assembles the real stack — CMC x402 paid signals (`CMCSource`), TWAK execution (`TwakExecutor`), and on-chain balance reads (`LiveBook`) — and fails fast if `SOLVENT_PRIVATE_KEY` / `SOLVENT_WALLET_PASSWORD` / `TWAK_WALLET_PASSWORD` are absent, so it cannot broadcast without explicit credentials. What remains is operational, not code: a funded agent wallet, TWAK API credentials on the host (`TWAK_ACCESS_ID` / `TWAK_HMAC_SECRET`, required for every `twak` call), and the ERC-8004 identity registered. The TWAK CLI (v0.19.0) is installed and its `swap` interface + `TWAK_WALLET_PASSWORD` env contract verified against `executor.py`. Live trading has not been run.

## Rubric map

| Rubric line | SOLVENT evidence |
|---|---|
| TWAK execution depth | sole execution path; rebuilding `exec/` is the only way to replace it |
| Self-custody integrity | keys never leave the host; local signing through the whole loop |
| Autonomous execution + guardrails | hands-off cycles + codified caps / allowlist / ratchet / kill switch, all visible on the dashboard |
| Native x402 | per-call data spend metered into every receipt |
| Originality | glass-box receipts + ERC-8004 trading identity — an agent a self-custody user can actually audit |
| CMC Agent Hub | multi-endpoint signals with per-decision cost metering |
| BNB AI Agent SDK | ERC-8004 identity + daily on-chain receipt anchors |
