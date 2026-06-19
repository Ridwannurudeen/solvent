# SOLVENT — demo video script (≤4 min)

Target flow (from the plan): **data purchase → decision → local signing → BSC tx
→ receipt → anchor → dashboard.** The thesis to land: *every other trading agent
asks you to trust it; SOLVENT lets you verify it.*

Two cuts are marked per scene:
- **[NOW]** — filmable right now against the live BSC rehearsal agent, mainnet ERC-8004 identity, and on-chain anchor at `solvent.gudman.xyz`.
- **[SCORED]** — film during the June 22-28 scored window if showing leaderboard PnL.

Keep it screen-recording + voiceover. No talking head. Total ~3:30.

---

## Scene 0 — Cold open (0:00–0:20)

**Screen:** the live dashboard `https://solvent.gudman.xyz`, scrolling slowly
past the six panels.

**VO:** "This is an autonomous trading agent running live on BNB Smart Chain.
Every decision it has ever made is on this page — what data it bought, what it
cost, why it traded, and a hash you can recompute yourself. It's a glass box."

> On-screen callout: the green **CHAIN VERIFIED** badge.

## Scene 1 — The data purchase (0:20–0:50) · [NOW]

**Screen:** the **Information budget** panel, then open one receipt's
`data_purchases` in `/receipts`.

**VO:** "Each cycle it buys exactly the data it needs and records the cost in
the decision receipt. Cheap base signals every hour; expensive derivatives data
only when the market's at an extreme — an information budget, capped at five
cents a call. Nothing is bought off the books."

> Show a non-zero CMC x402 proof receipt when available; otherwise show the
> current live receipt and the x402 budget caps in `/state`.

## Scene 2 — The decision (0:50–1:25) · [NOW]

**Screen:** the **Latest decision** panel + the barbell allocation bar.

**VO:** "The strategy is a barbell, not a gamble. At least 75% always sits in
stables; a single momentum position takes at most a 22% sleeve. Hard 12% stop,
a kill switch well inside the competition's drawdown limit, and a ratchet that
banks gains by shrinking the position as it wins. The rules are pure,
deterministic code — the AI can only *de-risk*, never force a trade."

> On-screen: the floor/sleeve bar; cut to `kernel/rules.py` for one beat to show
> the numbers are literal config, not vibes.

## Scene 3 — Local signing → on-chain (1:25–2:05) · [NOW]

**Screen:** terminal on the VPS; a live cycle firing; a `twak swap` producing a
real tx hash; click through to BscScan.

**VO:** "When it trades, signing happens locally through Trust Wallet's agent
kit — the keys never leave the host. The data payment is an EIP-3009 signature,
also local. Here's the resulting swap, confirmed on BSC."

> Use the live rehearsal tx if the current hour has no new sleeve trade.

## Scene 4 — The receipt and inference proof (2:05–2:35) · [NOW]

**Screen:** the **Decision receipts** table; expand one row — data bought, intent
(`from → to`, notional), execution + tx, and the receipt hash.

**VO:** "All of it lands in one signed receipt: the data, the thesis, the trade,
the transaction. New receipts also include an inference proof: input hash,
output hash, and proof hash. Each receipt commits to the previous one's hash,
so the whole history is a chain — change any past decision and every hash after
it breaks."

> Open `/inference-proofs` for one beat and show the proof hash matching the
> latest receipt.

## Scene 5 — Anyone can verify (2:35–3:05) · [NOW]

**Screen:** terminal, run the standalone verifier.

```bash
curl -s https://solvent.gudman.xyz/verify
python verify_receipts.py
# OK - N receipts, chain intact
# head: 0x...
```

**VO:** "You don't have to take its word for it. This verifier is 51 lines of
standard-library Python — it never touches the agent's code. It pulls the public
log, recomputes every hash from scratch, and prints the head. It matches."

## Scene 6 — Anchor and paid signal (3:05–3:25) · [NOW]

**Screen:** the **On-chain anchors** panel; click a day's tx to BscScan.

**VO:** "And once a day the latest daily head is written on-chain under the
agent's ERC-8004 identity. The current head keeps moving each hour; the anchor
shows the last committed UTC day. So the proof is end-to-end: public log,
recomputed locally, committed on-chain. A self-custody user could actually audit
this agent — and then trust it."

> Show ERC-8004 agent `136384`, the BSC mainnet anchor transaction, and the
> matching anchored-day head from `/state`. Then open `/signal` and show the
> ERC-8183-ready signal hash bound to the same receipt head and inference proof.

## Close (3:25–3:30)

**Screen:** dashboard URL + repo.

**VO:** "SOLVENT. The glass-box trader."

---

## Production notes

- Record the terminal at a large font; the BscScan tab in a clean profile.
- Pre-run `python verify_receipts.py` once so the head hash is warm on screen.
- The receipt-anchor path and live wallet accounting are live on BSC mainnet.
- Do not describe pre-June-22 rehearsal PnL as scored-week PnL.
- **Do not publish** (video, thread, or submission) without explicit approval —
  standing rule.
