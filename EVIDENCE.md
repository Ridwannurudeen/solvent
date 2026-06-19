# SOLVENT Evidence Packet

## Public Surfaces

- Dashboard: https://solvent.gudman.xyz
- Receipts: https://solvent.gudman.xyz/receipts
- Verifier API: https://solvent.gudman.xyz/verify
- State and anchors: https://solvent.gudman.xyz/state
- Policy manifest: https://solvent.gudman.xyz/policy
- ERC-8183 signal payload: https://solvent.gudman.xyz/signal
- Inference commitments: https://solvent.gudman.xyz/inference-commitments
- Standalone verifier: https://solvent.gudman.xyz/verify_receipts.py
- Evidence page: https://solvent.gudman.xyz/proof
- Public repo: https://github.com/Ridwannurudeen/solvent

## On-chain Proof

- Agent wallet: `0xE4fe23FB57dbb9AC2f685ea29B6b9A1409A0d359`
- ERC-8004 agent ID: `136384`
- ERC-8004 registration tx: `0xda8461a78cc715964a8d653a6cf8a1968119516e443475f6ef5ca46c4eaa90b6`
- Track 1 registration tx: `0xc4cdba129a1fb12714542ab991255c692240d6eb8bdfa716576199f9d31bda3a`
- First daily anchor tx: `0x01a50c38abfc5b577683b80d680b7a9e5e6c81e30cdcd0d81689c69afa1104ba`
- 2026-06-16 anchored head: `0x986790cac174dfccbdfae2ffebd0bea37f90b12d35d3431f7f3275dc5a5fddb0`
- 2026-06-18 production anchor tx: `0xec2fc445697704bd3dccb00d403a92e89f3ca6516a85751e135d594b11659319`
- 2026-06-18 production anchored head: `0x03c442d20351f5970e67894e4b0c45a2eab09543fc8b67b6b09eb1c3fe1aa617`

## Live Production Evidence

- TWAK live rehearsal swap tx: `0x2254bf01ea6bfa8d610c9bed916dbf19ec6db81d6068f1e0b0802a58cad50ac4`
- CMC x402 isolated receipt head: `0x282364a912a5090e83b4b522e5470ddba02203b3b3c77755e96bc515f89ba744`
- CMC x402 isolated receipt contents: 3 paid CMC MCP calls, `$0.03` total data cost, `degraded=false`, 22 parsed token prices, 22 momentum scores, no trade because regime was `risk-off`.
- Production live-mode rehearsal now reads all pinned wallet token balances directly from BSC mainnet through `LiveBook` and writes to `/opt/solvent/data-prod`.
- New live confirmations require a successful receipt, wallet sender match,
  ERC-20 transfer logs in the expected direction, and post-trade balance deltas
  before local position state can change.
- CMC x402 response bodies are hash-committed in `data_purchases`, and live
  CMC prices are cross-checked against Binance public REST.
- New receipts include hash-bound inference commitment packets. The public `/signal` payload binds the latest regime signal to the receipt hash, chain head, latest anchor, and inference commitment hash.

## Current Boundary

- The public dashboard is no longer a paper-mode dashboard; it reads production live-mode rehearsal state from the funded BSC wallet.
- Do not present pre-June-22 rehearsal PnL as scored-week leaderboard PnL.
- The production directory is isolated at `/opt/solvent/data-prod`; do not mix it with old paper data.
- Unresolved TWAK attempts are resolved only through append-only journal
  recovery (`solvent.ops.exec_recovery`) after manual chain review.

## Submission-gated Items

- Keep the signed and ERC-8004-anchored policy manifest live for the scored week.
- Record and upload the demo video.
- Submit the DoraHacks BUIDL.
- Increase or rebalance the scored-week stake only by explicit operator decision.
