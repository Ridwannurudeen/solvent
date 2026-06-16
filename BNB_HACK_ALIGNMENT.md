# BNB Hack Alignment

Source: BNB Hack: AI Trading Agent Edition, Track 1 Autonomous Trading Agents.

## Track Choice

SOLVENT is a Track 1 submission: Autonomous Trading Agents.

## Requirement Checklist

| Requirement | Status | Evidence |
|---|---:|---|
| GitHub/GitLab/Bitbucket link | Pending | Private GitHub repo exists; make public only when approved for submission. |
| On-chain proof: BSC agent address | Done | TWAK wallet `0xE4fe23FB57dbb9AC2f685ea29B6b9A1409A0d359`; ERC-8004 agent `136384`. |
| Track 1 on-chain competition registration | Done | `twak compete status` returns `registered:true`; registration tx `0xc4cdba129a1fb12714542ab991255c692240d6eb8bdfa716576199f9d31bda3a`. |
| Reads markets via CMC | Implemented | Live mode uses `CMCSource` via x402 MCP calls for global metrics, quotes, and conditional derivatives data. |
| Signs/processes transactions via TWAK | Implemented | `TwakExecutor` is the only live execution path; BSC quote-only swap verified on VPS. |
| User-defined rules | Implemented | RiskConfig plus allocator enforce allowlist, floor reserve, sleeve cap, stop, drawdown kill switch, ratchet, and slippage. |
| Live BSC trading during competition week | Operationally pending | Needs floor stable funding, TWAK key rotation, and explicit live flip. |
| Minimum 1 trade/day | Implemented | Deadman service triggers a stable-to-stable qualification trade path if no trade has occurred. |
| Fixed eligible token list | Implemented conservatively | Allowlist uses pinned BSC contracts for floor stables and selected in-scope sleeve tokens. |
| Portfolio worth above $1 | Pending funding | Wallet currently has gas but no SOLVENT-recognized floor stable balance. |
| Public repo plus demo/setup | Pending | README/RUNBOOK provide setup; repo publication and demo video require approval. |
| No token launch | Done | SOLVENT has no token launch, fundraising, LP opening, or airdrop component. |

## Special Prize Alignment

| Prize | SOLVENT angle |
|---|---|
| Best Use of Trust Wallet Agent Kit | TWAK wallet/keychain, self-custody local signing, sole live execution path, autonomous timers, and quote/trade CLI integration. TWAK-native x402 is still a stretch item for this special prize. |
| Best Use of Agent Hub | CMC x402 MCP data source with per-call data purchase receipts and budget caps. |
| Best Use of BNB AI Agent SDK | ERC-8004 identity and daily on-chain metadata anchors for the public receipt chain. |

## Before Submission

1. Rotate TWAK API credentials because credentials were pasted in chat.
2. Fund the TWAK wallet with floor stables on BSC mainnet.
3. Run one live rehearsal cycle after explicit approval.
4. Add a real CMC x402 receipt and, if feasible, a TWAK-native x402 action for the TWAK special prize.
5. Make the repo public only when approved.
6. Record the 2-4 minute demo with live dashboard, BscScan anchor, verifier, and setup proof.
7. Submit DoraHacks BUIDL only after explicit approval.
