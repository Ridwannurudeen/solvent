# BNB Hack Alignment

Source: BNB Hack: AI Trading Agent Edition, Track 1 Autonomous Trading Agents.

## Track Choice

SOLVENT is a Track 1 submission: Autonomous Trading Agents.

## Requirement Checklist

| Requirement | Status | Evidence |
|---|---:|---|
| GitHub/GitLab/Bitbucket link | Done | Public GitHub repo: `https://github.com/Ridwannurudeen/solvent`. |
| On-chain proof: BSC agent address | Done | TWAK wallet `0xE4fe23FB57dbb9AC2f685ea29B6b9A1409A0d359`; ERC-8004 agent `136384`. |
| Track 1 on-chain competition registration | Done | `twak compete status` returns `registered:true`; registration tx `0xc4cdba129a1fb12714542ab991255c692240d6eb8bdfa716576199f9d31bda3a`. |
| Reads markets via CMC | Implemented | Live mode uses `CMCSource` via x402 MCP calls for global metrics, quotes, and conditional derivatives data. |
| Signs/processes transactions via TWAK | Implemented | `TwakExecutor` is the only live execution path; BSC quote-only swap verified on VPS. |
| User-defined rules | Implemented | RiskConfig plus allocator enforce allowlist, floor reserve, sleeve cap, stop, drawdown kill switch, ratchet, and slippage. |
| Live BSC trading during competition week | Live rehearsal active | Wallet has funded in-scope stables and gas; production runs live mode on BSC mainnet before the June 22 scored window. |
| Minimum 1 trade/day | Implemented | Deadman service triggers a stable-to-stable qualification trade path if no trade has occurred. |
| Fixed eligible token list | Implemented conservatively | Allowlist uses pinned BSC contracts for floor stables and selected in-scope sleeve tokens. |
| Portfolio worth above $1 | Done | Wallet holds in-scope BSC floor stables above the $1 ranking floor. |
| Public repo plus demo/setup | Partial | Public repo and setup docs are live; demo video still requires recording and approval. |
| No token launch | Done | SOLVENT has no token launch, fundraising, LP opening, or airdrop component. |

## Special Prize Alignment

| Prize | SOLVENT angle |
|---|---|
| Best Use of Trust Wallet Agent Kit | TWAK wallet/keychain, self-custody local signing, sole live execution path, autonomous timers, live deadman fallback, and quote/trade CLI integration. |
| Best Use of Agent Hub | CMC x402 MCP data source with per-call data purchase receipts and budget caps. |
| Best Use of BNB AI Agent SDK | ERC-8004 identity, daily/updateable receipt-chain anchors, optional pre-trade anchors, and ERC-8183 paid signal delivery. |

## Before Submission

1. Rotate TWAK API credentials because credentials were pasted in chat.
2. Keep the TWAK wallet funded with the final scored-week stake and gas.
3. Keep production live mode isolated at `/opt/solvent/data-prod`.
4. Publish a frozen `/policy` manifest before the scored window.
5. Record the real CMC x402 receipt, live rehearsal tx, `/signal`, and `/inference-commitments` in the demo.
6. Record the 2-4 minute demo with live dashboard, BscScan anchor, verifier, and setup proof.
7. Submit DoraHacks BUIDL only after explicit approval.
