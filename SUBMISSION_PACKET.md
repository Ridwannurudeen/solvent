# SOLVENT Submission Packet

Use this packet when the demo video is ready. The repo is public; do not submit
the DoraHacks form from automation.

## DoraHacks Fields

**Project name:** SOLVENT

**Track:** Track 1, Autonomous Trading Agents

**One-liner:** SOLVENT is a glass-box BSC trading agent that records every data
purchase, thesis, intent, and transaction in a public hash-chained decision log
anchored daily under an ERC-8004 identity.

**Short description:**

SOLVENT reads live crypto market data through CoinMarketCap Agent Hub/x402,
cross-checks prices against Binance public REST, decides under a deterministic
risk constitution, and executes on BSC through Trust Wallet Agent Kit. Its edge
is auditability: every cycle emits a decision receipt that includes data bought,
cost, raw response commitment, inferred regime, thesis, intents, execution tx
hash, and settlement verification. Receipts are hash-chained and the daily head
is anchored on-chain under ERC-8004 agent `136384`.

**Repository:** https://github.com/Ridwannurudeen/solvent

**Demo:** pending recording/upload

**Live demo:** https://solvent.gudman.xyz

## Proof Links

- Dashboard: https://solvent.gudman.xyz
- Receipts: https://solvent.gudman.xyz/receipts
- Verifier API: https://solvent.gudman.xyz/verify
- State and anchors: https://solvent.gudman.xyz/state
- Policy manifest: https://solvent.gudman.xyz/policy
- Policy compliance / risk passport: https://solvent.gudman.xyz/policy-compliance
- Passport alias: https://solvent.gudman.xyz/passport
- ERC-8183 signal payload: https://solvent.gudman.xyz/signal
- Inference commitments: https://solvent.gudman.xyz/inference-commitments
- Standalone verifier: https://solvent.gudman.xyz/verify_receipts.py
- Evidence page: https://solvent.gudman.xyz/proof

## Readiness Command

```bash
sudo -u solvent -H /opt/solvent/.venv/bin/python -m solvent.ops.readiness \
  --env-file /opt/solvent/solvent.env --profile submission
```

The submission profile checks public `/verify`, `/state`, `/proof`, local
receipt-chain integrity, `/policy-compliance`, unresolved execution attempts,
and non-secret env presence. Approval-gated items are reported separately and do
not trigger automation.

## Local Proof Commands

```bash
python -m solvent.policy.verify --data-dir ./data
python -m solvent.ops.watcher --public-base https://solvent.gudman.xyz --out ./data/watcher-attestations.jsonl
python -m solvent.research.report
```

The policy verifier is the repo-side Proof-of-Policy check: it recomputes the
manifest hash, verifies the declared manifest signature and ERC-8004 wallet
anchor, validates scoped receipt and journal evidence against the mandate, and
emits a risk passport. The watcher is read-only public monitoring. The strategy
report is stress evidence, not a claim of statistically proven alpha.

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
- 2026-06-18 production anchor tx:
  `0xec2fc445697704bd3dccb00d403a92e89f3ca6516a85751e135d594b11659319`
- 2026-06-18 production anchored head:
  `0x03c442d20351f5970e67894e4b0c45a2eab09543fc8b67b6b09eb1c3fe1aa617`
- TWAK live rehearsal swap tx:
  `0x2254bf01ea6bfa8d610c9bed916dbf19ec6db81d6068f1e0b0802a58cad50ac4`
- Live CMC x402 receipt head:
  `0x282364a912a5090e83b4b522e5470ddba02203b3b3c77755e96bc515f89ba744`

## Strategy Explanation

SOLVENT uses a barbell portfolio designed for a live PnL tournament with a
drawdown gate:

- At least 75% of equity stays in in-scope BSC stables, conservatively marked
  with a stable haircut instead of assuming every unit is exactly $1.
- A single momentum sleeve can use about 22% of equity.
- Entries require risk-on regime plus executable token momentum.
- Stops, slippage, token allowlist, per-trade sizing, and daily trade limits are
  deterministic.
- A ratchet shrinks risk after gains.
- A kill switch liquidates the sleeve before the competition drawdown line and
  latches `HALTED` until explicit operator resume.
- A deadman path can fire a small stable-to-stable rotation to satisfy the daily
  trade requirement when the runtime is not persistently halted.
- CMC prices are checked against Binance; divergence or secondary-source failure
  degrades the cycle and prevents new risk.
- A researched `conviction_50` profile is available for the live competition:
  50% stable floor, 48% maximum sleeve, 8% hard stop, 10.0 momentum entry bar,
  and 48h minimum hold before momentum-decay exits. It is activated only by
  explicit runtime profile selection.
- Money-moving intents produce a pre-trade commit receipt before execution and
  an execution seal after the result. Production proof mode publishes the
  pre-trade commit hash to ERC-8004 before the TWAK swap. A swap is not marked
  `CONFIRMED` until the receipt, wallet sender, ERC-20 transfer logs, and
  post-trade balance deltas match the intent.
- New receipts include inference commitment packets that hash-bind the signal
  input, effective regime output, and model/kernel ID into the receipt chain.
  They are commitments, not TEE or zk proofs that a model executed.

The optional advisor can only de-risk. It cannot force a larger trade or bypass
the deterministic kernel.

## Sponsor Alignment

- **CMC Agent Hub:** live market reads and x402 paid data are recorded per
  decision receipt with response hashes and byte counts.
- **Trust Wallet Agent Kit:** TWAK is the only live execution path, using local
  self-custody signing and intent-aware settlement verification.
- **BNB AI Agent SDK / ERC-8004 / ERC-8183:** agent identity `136384` anchors
  the receipt chain head daily, pre-trade anchors can seal commit hashes before
  swaps, and the latest regime signal can be sold as an ERC-8183 deliverable.
- **BNB Chain:** registrations, anchors, rehearsals, and planned scored trades
  are on BSC mainnet.

## Demo Shot List

1. Dashboard with local-chain verification and anchor-coverage status.
2. `/receipts` showing data purchases and receipt hashes.
3. A `pre_trade_commit` followed by an `execution_seal`.
4. CMC x402 receipt or live proof showing non-zero data cost.
5. TWAK live rehearsal tx on BscScan.
6. `/inference-commitments` showing input/output/commitment hashes.
7. `/signal` showing the ERC-8183-ready paid signal payload.
8. `/policy-compliance` showing the risk passport and green policy checks.
9. `python verify_receipts.py` matching `/verify`.
10. ERC-8004 anchor in `/state` and BscScan.
11. Strategy/risk constitution in `kernel/rules.py` and signed, anchored `/policy` manifest.

## Do Not Claim

- Do not present paper-mode PnL as scored-week live PnL.
- Do not claim every individual receipt is wallet-signed; the receipt log is hash-chained and covered by periodic wallet-signed on-chain checkpoints.
- Do not call inference commitments TEE, zk, or runtime proofs.
- Do not claim pre-June-22 live rehearsal PnL is scored-week leaderboard PnL.
- Do not claim TEE/zk proof-of-inference; the proof layer is hash commitments,
  wallet-signed checkpoints, and settlement verification.
