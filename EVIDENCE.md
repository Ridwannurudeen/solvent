# SOLVENT Evidence Packet

## Public Surfaces

- Dashboard: https://solvent.gudman.xyz
- Receipts: https://solvent.gudman.xyz/receipts
- Verifier API: https://solvent.gudman.xyz/verify
- State and anchors: https://solvent.gudman.xyz/state
- Standalone verifier: https://solvent.gudman.xyz/verify_receipts.py
- Evidence page: https://solvent.gudman.xyz/proof

## On-chain Proof

- Agent wallet: `0xE4fe23FB57dbb9AC2f685ea29B6b9A1409A0d359`
- ERC-8004 agent ID: `136384`
- ERC-8004 registration tx: `0xda8461a78cc715964a8d653a6cf8a1968119516e443475f6ef5ca46c4eaa90b6`
- Track 1 registration tx: `0xc4cdba129a1fb12714542ab991255c692240d6eb8bdfa716576199f9d31bda3a`
- First daily anchor tx: `0x01a50c38abfc5b577683b80d680b7a9e5e6c81e30cdcd0d81689c69afa1104ba`
- 2026-06-16 anchored head: `0x986790cac174dfccbdfae2ffebd0bea37f90b12d35d3431f7f3275dc5a5fddb0`

## Live Proof Runs

- TWAK live rehearsal swap tx: `0x2254bf01ea6bfa8d610c9bed916dbf19ec6db81d6068f1e0b0802a58cad50ac4`
- CMC x402 isolated receipt head: `0x282364a912a5090e83b4b522e5470ddba02203b3b3c77755e96bc515f89ba744`
- CMC x402 isolated receipt contents: 3 paid CMC MCP calls, `$0.03` total data cost, `degraded=false`, 22 parsed token prices, 22 momentum scores, no trade because regime was `risk-off`.

## Current Boundary

- The public dashboard is intentionally still paper mode plus mainnet proof.
- Isolated live proof runs are stored outside `/opt/solvent/data` so public paper accounting is not mixed with real wallet equity.
- Production live mode remains approval-gated and must use an isolated `SOLVENT_DATA_DIR`.
- Unresolved TWAK attempts are resolved only through append-only journal
  recovery (`solvent.ops.exec_recovery`) after manual chain review.

## Submission-gated Items

- Make the GitHub repository public.
- Record and upload the demo video.
- Submit the DoraHacks BUIDL.
- Flip production `SOLVENT_MODE=live`.
- Top up to the final scored-week stake.
