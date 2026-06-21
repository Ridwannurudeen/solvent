# SOLVENT Submission Draft

## Track

Track 1: Autonomous Trading Agents

## One-liner

SOLVENT is a glass-box BSC trading agent: it reads CMC market data, decides under a deterministic risk constitution, executes through Trust Wallet Agent Kit, and publishes hash-chained decision receipts anchored daily under an ERC-8004 identity.

## Project Links

- Live dashboard: https://solvent.gudman.xyz
- Public receipt API: https://solvent.gudman.xyz/receipts
- Public verifier: https://solvent.gudman.xyz/verify
- State and anchors: https://solvent.gudman.xyz/state
- ERC-8183 signal payload: https://solvent.gudman.xyz/signal
- Policy manifest: https://solvent.gudman.xyz/policy
- Inference commitments: https://solvent.gudman.xyz/inference-commitments
- Evidence page: https://solvent.gudman.xyz/proof
- GitHub: https://github.com/Ridwannurudeen/solvent
- Demo video: pending recording

## On-chain Proof

- Agent wallet: `0xE4fe23FB57dbb9AC2f685ea29B6b9A1409A0d359`
- ERC-8004 agent ID: `136384`
- ERC-8004 registration tx: `0xda8461a78cc715964a8d653a6cf8a1968119516e443475f6ef5ca46c4eaa90b6`
- First receipt-chain anchor tx: `0x01a50c38abfc5b577683b80d680b7a9e5e6c81e30cdcd0d81689c69afa1104ba`
- Anchored head for `2026-06-16`: `0x986790cac174dfccbdfae2ffebd0bea37f90b12d35d3431f7f3275dc5a5fddb0`
- Production anchor tx for `2026-06-18`: `0xec2fc445697704bd3dccb00d403a92e89f3ca6516a85751e135d594b11659319`
- Production anchored head for `2026-06-18`: `0x03c442d20351f5970e67894e4b0c45a2eab09543fc8b67b6b09eb1c3fe1aa617`
- Track 1 competition registration tx: `0xc4cdba129a1fb12714542ab991255c692240d6eb8bdfa716576199f9d31bda3a`
- Isolated TWAK live rehearsal tx: `0x2254bf01ea6bfa8d610c9bed916dbf19ec6db81d6068f1e0b0802a58cad50ac4`
- Isolated CMC x402 receipt head: `0x282364a912a5090e83b4b522e5470ddba02203b3b3c77755e96bc515f89ba744`

## Strategy

SOLVENT uses a policy-selected barbell structure built for the live PnL week:

- Code-default `safety`: 75%+ floor reserve in in-scope BSC stables and one momentum sleeve capped around 22% of equity.
- Explicit `conviction_50` posture: 50% floor, 48% sleeve target, 8% hard stop, 10.0 momentum entry bar, and 48h minimum hold.
- Deterministic entry, exit, stop, slippage, token allowlist, and drawdown rules.
- Lock-in ratchet that shrinks the sleeve after gains.
- Kill switch before the competition drawdown disqualification threshold, latched until explicit operator resume.
- Deadman qualification path to satisfy the daily trade requirement when the runtime is not persistently halted.
- CMC x402 prices and positive momentum are cross-checked against Binance public REST; divergence or unconfirmed positive momentum marks the cycle degraded.

The advisor layer is optional and can only de-risk; it cannot force or enlarge a trade.

## Sponsor Stack

- CoinMarketCap: live mode uses CMC Agent Hub/x402 MCP calls for global metrics, quotes, and conditional derivatives data; paid responses are hash-committed in receipts.
- Trust Wallet Agent Kit: TWAK wallet/keychain is used for local self-custody signing, and `TwakExecutor` is the sole live execution path with receipt, sender, transfer-log, and balance-delta verification.
- BNB AI Agent SDK: ERC-8004 identity, daily receipt-chain anchors, pre-trade anchors, and the signed/anchored policy manifest provide persistent on-chain proof.
- BNB Agent SDK ERC-8183: the latest regime read can be sold as a paid signal deliverable whose manifest hash is submitted on-chain.
- BNB Chain: all registration, anchors, and planned live trades are on BSC mainnet.

## Verification

Anyone can verify SOLVENT without trusting the server:

```bash
curl -s https://solvent.gudman.xyz/verify
python verify_receipts.py
```

The verifier recomputes the public receipt hash chain and prints the head hash. `/verify` also reports how many local receipts are covered by the latest on-chain anchor versus still unanchored. The latest daily head is committed on BSC as ERC-8004 metadata.

## Current Gates

- Keep the wallet funded with the final scored-week stake and gas.
- Keep live mode isolated at `/opt/solvent/data-prod` so paper and live accounting never mix.
- Keep the signed and ERC-8004-anchored policy manifest live at `/policy`.
- Record and attach the demo video.

## Compliance Notes

SOLVENT does not launch a token, raise funds, open liquidity, run an airdrop, or rely on a custodial execution path.
