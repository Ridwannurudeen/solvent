# PROJECT SOLVENT — Master Plan

**BNB Hack: AI Trading Agent Edition** (BNB Chain × CoinMarketCap × Trust Wallet) · Track 1: Autonomous Trading Agents
**Plan version:** 2.0 (post adversarial red-team + competitor scan) · **Drafted:** 2026-06-10

> **SOLVENT — the glass-box trader.** A fully autonomous, self-custody trading agent on BSC
> that buys its own market data with x402 micropayments, signs its own transactions through
> Trust Wallet Agent Kit inside hard-coded guardrails, and proves *why* it traded — every trade
> ships an on-chain decision receipt (data bought → cost → thesis → tx hash) anchored under its
> permanent ERC-8004 identity. Other agents show you their PnL. SOLVENT shows you its mind.

---

## 1. Objectives & prize targets

| Prize | Pool | Our angle | Priority |
|---|---|---|---|
| Best Use of TWAK (Track 1 only) | $2,000 | TWAK as *sole* execution layer across 3 surfaces (Agent Wallet autonomous mode, CLI, MCP), clean local signing end-to-end, native Binance-x402 in the loop, codified guardrails | **P0 — primary** |
| Best Use of CMC Agent Hub | $2,000 | Multi-endpoint usage (funding, OI, Fear & Greed, TA, narratives) via both MCP and x402 keyless, with per-call cost metering surfaced in receipts | **P0 — primary** |
| Best Use of BNB AI Agent SDK | $2,000 | ERC-8004 identity + on-chain receipt anchoring + ERC-8183 signal-selling. SDK has zero trading code, so few teams will use it non-cosmetically | **P0 — primary** |
| Track 1 placement (5 winners) | $2k–$10k | Barbell engine: structurally DQ-proof floor + concentrated momentum sleeve for right-tail return | **P1 — bounded lottery** |

Realistic outcome band: $2k–$16k. The specials are rubric-judged craft (we control them); Track 1 rank is partly the market's mood that week (we don't).

**Strategic posture (from red-team):** build to *win the specials*, stay *alive and competitive* in the PnL race. Do not pretend a risk-disciplined agent reliably beats a convex tournament; do ensure that if the momentum sleeve catches a move, the ratchet banks it.

---

## 2. Verified ground truth (all checked 2026-06-10 — do not re-litigate)

- **Field size:** 61 DoraHacks registrations, 0 submissions; ~20 `Register` calls on CompetitionRegistry `0x212c61b9b72c95d95bf29cf032f5e5635629aed5` (BSC, verified contract).
- **bnbagent-sdk** (`github.com/bnb-chain/bnbagent-sdk`, Python, v0.3.6, PyPI `bnbagent`): ERC-8004 identity (gas-free testnet reg via MegaFuel paymaster), ERC-8183 commerce/escrow, `X402Signer`. **No trading/DEX code.** Self-declared breaking changes.
- **TWAK** (`portal.trustwallet.com`): MCP + CLI + SDK + REST; Agent Wallet autonomous mode ("rules set upfront, no per-tx approval"); local self-custody signing; Binance x402 on BNB Chain. Install: `curl -fsSL https://agent-kit.trustwallet.com/install.sh | bash` (Linux — runs on the VPS, not this Windows box).
- **CMC Agent Hub:** MCP at `mcp.coinmarketcap.com/mcp` (~12 tools incl. `get_global_crypto_derivatives_metrics` [funding/OI/liquidations], `get_global_metrics_latest` [Fear & Greed], TA, narratives); x402 keyless endpoints at **$0.01 USDC/call settled on Base** (e.g. `pro.coinmarketcap.com/x402/v3/cryptocurrency/quotes/latest`).
- **Costs:** BSC gas 0.05 gwei (RPC-verified) → ~$0.006/swap at BNB $582.70. DEX swap fee ~0.25% — **the binding cost**, not gas.
- **Scoring:** ranked by total return (%, hourly marks), DQ >30% drawdown, ≥1 trade/day (7+/week), 149-token BEP-20 allowlist, portfolio must stay >$1.
- **Local environment:** Python 3.12.10, Node 24.15.0, git 2.54 on Windows; **WSL2 unavailable** (virtualization off) → agent runtime = Contabo VPS (Linux). VPS hosts live projects (bequest/kickoff/reef/verdikt/PINL) — new systemd unit + port only, touch nothing else.
- **Competitor intel (GitHub, June 2026):** strongest rival = **NEXUS Arbiter** (7-stage pipeline, 12 CMC MCP tools, TWAK, 155 live BSC trades, verifiable wallet — but no x402/receipts/ERC-8004). 4+ repos crowd the "regime monitor" lane. **Nobody public does decision receipts, info-budget metering, or ERC-8004 trading identity.** That lane is ours.
- **What wins these events (precedent):** deployed + verifiable + vertical beats frameworks; demo video and public narrative materially matter; in live-PnL arenas the winning profile was low-frequency momentum with tight stops (Alpha Arena winner: ~43 trades/17 days, 1–2 positions, ~+22%) while 4/6 over-leveraged entrants lost >50%.

## 3. Open questions (Phase 0 gates — every one has a fallback)

| # | Question | How to resolve | Fallback if bad answer |
|---|---|---|---|
| G1 | Is the 30% DD measured from **starting capital or peak equity**? (flips strategy) | Ask in hackathon Telegram, day 1 | Assume peak-based (stricter) → ratchet protects peak |
| G2 | TWAK guardrail schema: allowlists / per-trade caps / drawdown — what's real? | Install CLI on VPS, dump config | Enforce ALL rules in our risk kernel; TWAK = signing+execution only |
| G3 | Does `twak compete register` exist? | CLI `--help` after install + Telegram | Register via MCP action `competition_register` or direct contract call |
| G4 | Does TWAK execute BSC perps or spot only? | Test with dust | Spot-only build (default assumption anyway) |
| G5 | CMC Pro key tier: are derivatives/funding endpoints included? | Test key day 1 | x402 keyless path for everything (it's our feature) |
| G6 | Are hourly equity marks venue-based or oracle-based? (wick-DQ risk) | Telegram | Concentrate only in deep-liquidity allowlist names |

**No real-money code is written until G1–G3 are closed.**

---

## 4. Architecture

```
                       ┌─ CMC MCP (keyed) ──────────────┐
  market data ─────────┤                                 ├──> SIGNAL ENGINE
                       └─ CMC x402 keyless ($0.01/call) ─┘    (funding, OI, F&G, TA,
                              │  every call metered            narratives; cached+costed)
                              ▼
                      X402 SPEND METER  ── cost-per-decision into receipts
                              │
                              ▼
                        REGIME BRAIN          LLM (Claude API) advises: regime read,
                     (risk-on/neutral/off) ←─ candidate ranking, thesis text.
                              │               NEVER touches keys, sizes, or sends.
                              ▼
                         RISK KERNEL          deterministic Python, 100% unit-tested:
                     ┌────────────────────┐   • barbell allocator (§5)
                     │  the only path to   │  • lock-in ratchet
                     │  money. no AI here. │  • per-trade/daily caps, slippage bounds
                     └────────────────────┘   • 149-token allowlist (exact list, frozen)
                              │               • qualification scheduler (1 trade/day)
                              ▼               • $1-floor + DQ-headroom monitors
                       TWAK EXECUTOR          autonomous Agent Wallet, local signing,
                     (sole execution layer)   idempotent: ONE tx per intended trade, ever
                              │
                              ▼
                      DECISION RECEIPTS       JSON: {data bought, x402 cost, signals,
                     (hash-chained log)        regime, thesis, action, tx hash, equity}
                              │                each receipt hashes the previous one
                              ▼
                   DAILY ON-CHAIN ANCHOR      1 cheap tx/day: chain-head hash posted to
                   under ERC-8004 IDENTITY    BSC under SOLVENT's registered agent ID
                              │
                              ▼
                    GLASS-BOX DASHBOARD       solvent.gudman.xyz (read-only, public):
                                              live equity vs BNB benchmark, every receipt,
                                              data bill, regime state, guardrail status,
                                              DQ headroom gauge, anchor-tx links
```

**Design laws (non-negotiable):**
1. **LLM proposes, code disposes.** Money moves only through the deterministic kernel.
2. **One transaction per intended trade.** Idempotency keys on every execution; never probe or retry a value-send blind (standing rule).
3. **Fail frozen, not creative.** Any component failure → hold current (or rotate to stables via pre-validated path) + alert. The agent never guesses under degraded data.
4. **Everything public.** If a judge can't click it, it doesn't count.

## 5. Strategy spec — the barbell engine

**Why barbell (red-team conclusion reconciled with arena data):** a convex top-5 tournament punishes both mid-pack caution *and* blow-ups (30% DD = DQ; 65–70% of aggressive arena entrants blew up). The dominant shape: a floor that mathematically cannot DQ + a concentrated sleeve with real right-tail.

- **Floor (75–80% of capital):** stables (USDT/USDC/FDUSD from the allowlist). Worst case across the whole week ≈ −20–25% even if the sleeve goes to zero → DQ structurally impossible from position losses.
- **Sleeve (20–25%):** rotated into the **single highest-momentum, deep-liquidity** allowlist token. Entry requires confirmation (momentum + funding not extreme against us + F&G filter). Hard stop ≈ −12% per position. Exit on momentum decay or stop. Expected 1–2 real positions at a time, low frequency — the verified winning arena profile.
- **Lock-in ratchet (anti-peak-DD, G1-proofed):** banked-gain thresholds shrink the sleeve. E.g. equity > +10% → sleeve caps at 15%; > +20% → 10%. We never give back enough from peak to approach DQ. Exact thresholds tuned in Phase 3 rehearsal, frozen June 20.
- **Qualification trade:** one minimal-size stable↔stable or micro rebalance daily at a scheduled hour (cost ≈ 0.25% of a tiny notional ≈ cents) — satisfies the 7-trade rule with ~zero drag. Deadman: if no trade confirmed by 21:00 UTC, watchdog fires the fallback trade through an independent code path.
- **Sizing reality check:** fees (0.25%/swap) are budgeted in the P&L model explicitly; gas is noise ($0.006/swap, verified).
- **Down-week posture:** floor holds, sleeve sits out unless a counter-trend confirmation appears. In a red week we are playing for the specials — by design, not by accident.

## 6. The showpieces (what wins the rubrics)

### 6.1 Decision receipts + ERC-8004 identity (originality lead)
Every cycle emits a receipt:
```json
{
  "seq": 142, "prev_hash": "0x…", "ts": "2026-06-24T13:00:04Z",
  "data_purchases": [{"endpoint": "x402/v3/…/quotes", "cost_usdc": 0.01, "tx": "0x…"}],
  "signals": {"regime": "risk-on", "fng": 71, "funding_btc": 0.012, "momo_rank": ["ASTER", "CAKE"]},
  "thesis": "ASTER 4h momentum + funding neutral; sleeve entry 22% w/ stop -12%",
  "action": {"type": "swap", "from": "USDT", "to": "ASTER", "notional_usd": 66.0, "tx_hash": "0x…"},
  "equity_usd": 318.42, "dq_headroom_pct": 24.1, "receipt_hash": "0x…"
}
```
Hash-chained (each receipt commits to the previous); the chain head is anchored **once daily in one cheap BSC tx** under SOLVENT's ERC-8004 agent ID (port Reef registration code; Merkle trees deliberately cut — hash chain gives ~90% of the credibility for 10% of the effort). Anyone can recompute the chain from the public log and check it against the anchors: **a tamper-evident, on-chain-verifiable record of the agent's reasoning.** NEXUS proves *that* it traded; SOLVENT proves *why*.

### 6.2 x402 spend metering (honest scope — NOT pitched as alpha)
All CMC data flows through paid x402 calls; the meter attributes research cost to each decision ("this trade consumed $0.04 of data"). Additionally ≥1 recurring payment routes through **TWAK's Binance-x402 on BNB Chain** so the TWAK rubric's "native x402" box is checked on their rails, not just CMC's Base rails. Pitch line: *"SOLVENT natively meters its information supply chain"* — never "the budget is its edge" (red-team kill-line avoided).

### 6.3 Glass-box dashboard — `solvent.gudman.xyz`
Read-only static-ish page fed by the receipt log (same nginx/TLS/systemd pattern as reef/bequest; new port; zero risk to live apps). Panels: equity curve vs BNB benchmark · live regime + sleeve state · receipt stream with x402 costs · DQ-headroom gauge · daily anchor txs (BscScan links) · uptime heartbeat. This is simultaneously the demo, the judge artifact, and the X-thread content.

## 7. Phases & schedule (build: Jun 10–21 · trading: Jun 22–28)

### Phase 0 — Gates & registration (Jun 10–11)
- [ ] VPS: create `/opt/solvent`, dedicated user, install TWAK CLI; close G2/G3/G4 (dump guardrail schema, test dust swap on BSC, check `compete register`)
- [ ] Telegram: join, ask G1 (DD definition) + G6 (mark methodology)
- [ ] CMC Pro key; close G5 (tier check); fire one live x402 call end-to-end (USDC on Base)
- [ ] `pip install bnbagent`; register throwaway ERC-8004 ID on BSC testnet (gas-free paymaster)
- [ ] Scaffold repo `solvent` (public from day 1 — building in public is scored, precedent-verified)
- **Exit criteria:** all six gates have answers or activated fallbacks; dust swap executed via TWAK on BSC mainnet.

### Phase 1 — Deterministic core + uptime armor (Jun 12–14)  ← *uptime promoted here per red-team*
- [ ] `kernel/`: barbell allocator, ratchet, caps, allowlist (149 tokens transcribed exactly from rules), slippage bounds, scheduler, $1-floor + DQ-headroom monitors — **unit tests for every rule including the ugly edges** (no trade by 23:50? all venues down? token delisted mid-week?)
- [ ] `exec/`: TWAK wrapper — idempotency keys, confirmation polling, one-tx-per-trade invariant
- [ ] `signals/`: CMC MCP + x402 clients with caching, cost logging, staleness flags
- [ ] **Ops armor now:** systemd unit + watchdog + auto-restart + heartbeat + "did-I-trade-today" deadman with independent fallback-trade path + Telegram alert bot (trade/error/guardrail/heartbeat-loss)
- **Exit criteria:** full loop runs hourly, unattended, in live rehearsal mode on the VPS; kernel test suite green; kill the process manually → it restarts and reports.

### Phase 2 — Showpieces (Jun 15–17) · *live small-stake trading starts now = public track record (NEXUS bar)*
- [ ] Receipt engine: schema, hash chain, public log endpoint
- [ ] ERC-8004 mainnet identity + daily anchor tx (port Reef code)
- [ ] Regime brain: Claude API advisor with structured output → kernel-validated proposals only
- [ ] Dashboard live at solvent.gudman.xyz
- [ ] Fund wallet (~$50 rehearsal stake) and switch to LIVE with sleeve capped at 10%
- **Exit criteria:** a stranger can: watch the dashboard → click a receipt → click its anchor tx on BscScan → recompute the hash chain.

### Phase 3 — Hardening & dress rehearsal (Jun 18–20)
- [ ] 48h+ continuous live run under full competition rules ($50 stake)
- [ ] Chaos drills (now they're cheap — armor exists): kill RPC / CMC / TWAK mid-cycle; power-cycle; clock skew → verified fail-frozen behavior each time
- [ ] Tune ratchet/momentum thresholds on rehearsal data; **strategy freeze June 20, no edits during scored week**
- [ ] Top up wallet to full stake (~$300 BSC + $5 BNB gas + $10 USDC-on-Base)
- **Exit criteria:** ≥48h unattended uptime, every drill passed, config frozen and tagged.

### Phase 4 — Ship (Jun 21)
- [ ] README: architecture, strategy explanation (DoraHacks requires it), receipt-verification how-to, rubric map (§8)
- [ ] Demo video (≤4 min): data purchase → decision → local signing → BSC tx → receipt → anchor → dashboard
- [ ] X thread (building-in-public recap) — *publish only with user approval*
- [ ] **DoraHacks BUIDL submission + on-chain `Register` — ONLY with explicit user approval (standing rule, no exceptions)**

### Scored week — ops only (Jun 22–28)
Daily 10-minute runbook (UTC morning): qualification trade confirmed for yesterday + scheduled today · DQ headroom > 20% · heartbeat fresh · anchor tx mined · dashboard up · wallet > $1 · alert log clean. **No strategy edits. No manual trades. Hands off — that's the demo.**

## 8. Special-prize rubric map (feature → points)

| Rubric line | Points | SOLVENT evidence |
|---|---|---|
| TWAK integration depth | 30 | Sole execution layer; 3 surfaces (Agent Wallet, CLI, MCP); not replaceable without rebuilding `exec/` |
| Self-custody integrity | 25 | Keys never leave VPS; local signing through entire loop; zero custodial hops → top band (20–25) |
| Autonomous execution + guardrails | 20 | 7-day hands-off scored week + codified caps/allowlist/ratchet, all visible on dashboard |
| Native x402 (TWAK) | 10 | Recurring Binance-x402 payment on BNB Chain inside the loop |
| Originality + relevance | 10 | Glass-box receipts: the agent a self-custody user could actually audit, then trust |
| Demo | 5 | Live dashboard + end-to-end video with on-chain proof |
| CMC Agent Hub special | — | 5+ endpoint families via MCP **and** x402, costs metered per decision |
| BNB SDK special | — | ERC-8004 mainnet identity + daily/pre-trade receipt anchors + ERC-8183 |

## 9. Budget

| Item | USD |
|---|---|
| Trading capital (BSC, allowlist tokens) | 300 |
| Rehearsal stake (subset of above, deployed early) | (50) |
| BNB gas (verified ~$0.006/swap; ~60 txs incl. anchors) | 5 |
| USDC on Base (x402: ~1,000 calls) | 10 |
| CMC Pro key | 0 (hackathon credits) |
| VPS / domain / TLS | 0 (existing) |
| **Total at risk** | **~$315** · worst-case loss ≈ $60–80 (floor math + fees + data) |

## 10. Risk register

| Risk | L×I | Mitigation |
|---|---|---|
| TWAK immature/broken on BSC | M×H | G2/G4 day-1; fallback: kernel signs via web3.py, TWAK kept for wallet+x402 surfaces (sacrifices special points, saves entry) |
| Process dies during scored week | M×F | Phase-1 armor: watchdog, deadman, independent fallback-trade path, Telegram alerts |
| Peak-DD rule (if G1 = peak-based) | M×H | Ratchet keeps trailing DD < ~25% by construction |
| Flat/chop week → sleeve churn | H×M | Confirmation-gated entries, low frequency; floor preserves capital |
| bnbagent-sdk breaking changes | M×M | Pin v0.3.6; anchor path is ~2 simple calls; raw-contract fallback (ABI known from Reef work) |
| Sole-builder crunch | M×H | Cut order (pre-decided): ERC-8183 stretch → dashboard polish → narratives module. Never cut: kernel tests, armor, receipts |
| Wick-DQ on thin token (G6) | L×H | Sleeve restricted to deep-liquidity names; headroom monitor de-risks at 22% |
| Copycats after repo goes public | M×L | First-mover + live receipt history is unforgeable; velocity is the moat |

## 11. Stretch goals (only after Phase 3 exit criteria pass)
1. **ERC-8183 signal sale** — SOLVENT sells its daily regime read to other agents via SDK escrow (near-locks the BNB SDK special).
2. **Personality layer** — daily self-narration from receipts ("Spent $0.34 on data, learned nothing worth a trade, stayed home").
3. **Receipt verifier CLI** — one-command public auditor: recompute chain, check anchors (`python -m solvent.verify`).

## 12. Submission checklist (Jun 21)
- [ ] Public repo, reproducible setup, pinned deps
- [ ] README: strategy explanation (required), architecture, verification how-to
- [ ] Demo video ≤4 min with on-chain proof
- [ ] Dashboard live · ERC-8004 ID + anchors on BscScan
- [ ] DoraHacks BUIDL (**user approval first**)
- [ ] On-chain registration before Jun 22 window (**user approval first**)
- [ ] No token launch / airdrop / fundraising (rule)
- [ ] No AI attribution anywhere in code/commits (standing rule)

---
*Every factual claim in this plan was verified 2026-06-10 (sources in §2). Anything not verifiable from outside is a numbered gate in §3 with a fallback. Plan supersedes v1; deltas driven by adversarial red-team + competitor scan of same date.*
