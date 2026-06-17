# SOLVENT Submission Packet

Use this packet when the repo is approved for public release and the demo video
is ready. Do not submit the DoraHacks form from automation.

## DoraHacks Fields

**Project name:** SOLVENT

**Track:** Track 1, Autonomous Trading Agents

**One-liner:** SOLVENT is a glass-box BSC trading agent that records every data
purchase, thesis, intent, and transaction in a public hash-chained decision log
anchored daily under an ERC-8004 identity.

**Short description:**

SOLVENT reads live crypto market data through CoinMarketCap Agent Hub/x402,
decides under a deterministic risk constitution, and executes on BSC through
Trust Wallet Agent Kit. Its edge is auditability: every cycle emits a decision
receipt that includes data bought, cost, inferred regime, thesis, intents, and
execution tx hash. Receipts are hash-chained and the daily head is anchored
on-chain under ERC-8004 agent `136384`.

**Repository:** pending public release approval

**Demo:** pending recording/upload

**Live demo:** https://solvent.gudman.xyz

## Proof Links

- Dashboard: https://solvent.gudman.xyz
- Receipts: https://solvent.gudman.xyz/receipts
- Verifier API: https://solvent.gudman.xyz/verify
- State and anchors: https://solvent.gudman.xyz/state
- Standalone verifier: https://solvent.gudman.xyz/verify_receipts.py
- Evidence page: https://solvent.gudman.xyz/proof

## On-chain Proof

- Agent wallet: `0xE4fe23FB57dbb9AC2f685ea29B6b9A1409A0d359`
- ERC-8004 agent ID: `136384`
- ERC-8004 registration tx:
  `0xda8461a78cc715964a8d653a6cf8a1968119516e443475f6ef5ca46c4eaa90b6`
- Track 1 registration tx:
  `0xc4cdba129a1fb12714542ab991255c692240d6eb8bdfa716576199f9d31bda3a`
- First daily anchor tx:
  `0x01a50c38abfc5b577683b80d680b7a9e5e6c81e30cdcd0d81689c69afa1104ba`
- 2026-06-16 anchored head:
  `0x986790cac174dfccbdfae2ffebd0bea37f90b12d35d3431f7f3275dc5a5fddb0`
- TWAK live rehearsal swap tx:
  `0x2254bf01ea6bfa8d610c9bed916dbf19ec6db81d6068f1e0b0802a58cad50ac4`
- Live CMC x402 receipt head:
  `0x282364a912a5090e83b4b522e5470ddba02203b3b3c77755e96bc515f89ba744`

## Strategy Explanation

SOLVENT uses a barbell portfolio designed for a live PnL tournament with a
drawdown gate:

- At least 75% of equity stays in in-scope BSC stables.
- A single momentum sleeve can use about 22% of equity.
- Entries require risk-on regime plus executable token momentum.
- Stops, slippage, token allowlist, per-trade sizing, and daily trade limits are
  deterministic.
- A ratchet shrinks risk after gains.
- A kill switch liquidates the sleeve before the competition drawdown line.
- A deadman path can fire a small stable-to-stable rotation to satisfy the daily
  trade requirement.

The optional advisor can only de-risk. It cannot force a larger trade or bypass
the deterministic kernel.

## Sponsor Alignment

- **CMC Agent Hub:** live market reads and x402 paid data are recorded per
  decision receipt.
- **Trust Wallet Agent Kit:** TWAK is the only live execution path, using local
  self-custody signing.
- **BNB AI Agent SDK / ERC-8004:** agent identity `136384` anchors the receipt
  chain head daily.
- **BNB Chain:** registrations, anchors, rehearsals, and planned scored trades
  are on BSC mainnet.

## Demo Shot List

1. Dashboard with chain-verified status.
2. `/receipts` showing data purchases and receipt hashes.
3. CMC x402 receipt or live proof showing non-zero data cost.
4. TWAK live rehearsal tx on BscScan.
5. `python verify_receipts.py` matching `/verify`.
6. ERC-8004 anchor in `/state` and BscScan.
7. Strategy/risk constitution in `kernel/rules.py`.

## Do Not Claim

- Do not present paper-mode PnL as scored-week live PnL.
- Do not claim public repo availability before the repo is actually made public.
- Do not claim every dashboard receipt is live-money execution while production
  mode is still gated.
