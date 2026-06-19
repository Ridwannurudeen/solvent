# SOLVENT — go-live runbook

The build is running in **live rehearsal** mode on the public VPS, with BSC
mainnet identity, anchoring, Track 1 registration, live wallet accounting, and
TWAK execution wired. The scored leaderboard window starts June 22; do not
present pre-window rehearsal PnL as scored-week PnL.

Host: `root@75.119.153.252`, agent runs as the **`solvent`** user, and every
systemd unit loads `/opt/solvent/solvent.env`. Current production state uses
`SOLVENT_MODE=live` and `SOLVENT_DATA_DIR=/opt/solvent/data-prod`; do not point
live mode at an old paper directory.

The cutover checklist in `LIVE_CUTOVER.md` is retained as an operator recovery
checklist; this runbook keeps the full setup and recovery context.

For no-broadcast readiness checks, use:

```bash
sudo -u solvent -H /opt/solvent/.venv/bin/python -m solvent.ops.readiness \
  --env-file /opt/solvent/solvent.env --profile submission
```

Use `--profile live` only for the strict scored-week cutover gate; it requires
an isolated live data directory and all live env presence checks.

Verified this session: TWAK CLI `v0.19.0` at `/usr/bin/twak` (on the `solvent`
user's PATH); `twak swap FROM TO --usd N --chain bsc …` matches `executor.py`;
`twak auth status`, `twak wallet status`, `twak wallet keychain check`, and
`twak compete status` all pass for the `solvent` user.

---

## Step 1 — TWAK credentials + wallet (on the VPS, as the `solvent` user)

TWAK needs API credentials (`TWAK_ACCESS_ID` + `TWAK_HMAC_SECRET`) for **every**
call, plus an agent wallet. Run setup as the `solvent` user so the wallet/config
land where the service's `twak` subprocess will read them:

```bash
ssh root@75.119.153.252
sudo -u solvent -H twak auth setup       # interactive: API creds
sudo -u solvent -H twak setup --wallet   # interactive: create/import wallet
sudo -u solvent -H twak wallet address --chain bsc   # the address to fund
sudo -u solvent -H twak compete status   # confirm creds work
```

If you choose env-backed credentials instead of TWAK's user config, put them in
`/opt/solvent/solvent.env` (so the systemd-run agent inherits them) and
`chmod 600` it:

```
TWAK_ACCESS_ID=...
TWAK_HMAC_SECRET=...
# Optional if the `solvent` user's TWAK keychain works:
# TWAK_WALLET_PASSWORD=...
```

> ⚠️ VERIFY AT RUN TIME: that `twak` invoked by the **`solvent`** user (not root)
> finds this wallet + creds — the executor runs as `solvent`. If `twak setup`
> stored config under root's home, re-run it as `solvent` or rely on the env
> vars above. Confirm with `sudo -u solvent twak compete status`.

## Step 2 — Fund the agent wallet (real money, BSC mainnet)

Send the rehearsal stake to the Step-1 address on **BSC mainnet**:
- ~$50 in a floor stable (USDT) for the rehearsal; top up to ~$300 for the scored week.
- ~$5 of BNB for gas (swaps cost ~$0.006 each; the binding cost is the ~0.25% DEX fee).
- A small BSC USD1 balance on the x402 signing wallet for paid CMC calls. The
  CMC MCP challenge offers BSC EIP-3009 settlement in USD1; Binance-Peg USDC on
  BSC is currently permit2-only, which SOLVENT does not sign.

```bash
sudo -u solvent -H twak wallet balance --chain bsc    # confirm funds landed
# non-secret readiness report
sudo -u solvent -H /opt/solvent/.venv/bin/python -m solvent.ops.preflight --env-file /opt/solvent/solvent.env
```

## Step 3 — Register the ERC-8004 identity + first anchor (BSC mainnet)

Lights up the dashboard's on-chain anchor panel. This uses the `solvent` user's
TWAK wallet/keychain on BSC mainnet; the wallet needs a small BNB balance for
gas before registration or anchoring can mine.

```bash
cd /opt/solvent
sudo -u solvent -H env SOLVENT_ANCHOR_BACKEND=twak SOLVENT_BSC_NETWORK=bsc-mainnet \
  .venv/bin/python -m solvent.receipts.anchor --data-dir /opt/solvent/data-prod --register
# -> prints SOLVENT_AGENT_ID=<n> ; add the three vars below to solvent.env:
# SOLVENT_ANCHOR_BACKEND=twak
# SOLVENT_BSC_NETWORK=bsc-mainnet
# SOLVENT_AGENT_ID=<n>
# Then fire the first anchor:
sudo -u solvent -H env SOLVENT_ANCHOR_BACKEND=twak SOLVENT_BSC_NETWORK=bsc-mainnet SOLVENT_AGENT_ID=<n> \
  .venv/bin/python -m solvent.receipts.anchor --data-dir /opt/solvent/data-prod --update-existing
sudo systemctl enable --now solvent-anchor.timer     # daily 23:55 UTC
```

## Step 4 — Track 1 competition registration

DoraHacks Track 1 requires on-chain competition registration of the agent wallet
before the live trading window. This is separate from the ERC-8004 identity.
SOLVENT is registered; tx:
`0xc4cdba129a1fb12714542ab991255c692240d6eb8bdfa716576199f9d31bda3a`.

```bash
sudo -u solvent -H twak compete status
```

Expected result: `registered: true` for
`0xE4fe23FB57dbb9AC2f685ea29B6b9A1409A0d359`.

## Step 5 — Pre-flight, then flip to live

**Pre-flight (no broadcast)** — confirm TWAK resolves bare symbols on BSC:

```bash
sudo -u solvent -H twak swap USDT USDC --usd 1 --chain bsc --quote-only --json
```

If the quote returns sensible token addresses/amounts, add the live block to
`/opt/solvent/solvent.env`:

```
SOLVENT_MODE=live
SOLVENT_DATA_DIR=/opt/solvent/data-prod
SOLVENT_RISK_PROFILE=safety
SOLVENT_ADAPTIVE_PROFILE=1
SOLVENT_PRETRADE_ANCHOR=1
SOLVENT_PRIVATE_KEY=0x...
SOLVENT_WALLET_PASSWORD=...
SOLVENT_TRADE_NETWORK=bsc-mainnet
SOLVENT_TWAK_CHAIN=bsc
SOLVENT_WALLET_ADDRESS=0xE4fe23FB57dbb9AC2f685ea29B6b9A1409A0d359
# (TWAK auth/wallet already configured from Step 1; TWAK_WALLET_PASSWORD is
# optional when the `solvent` user's keychain is available)
```

Live mode refuses to start against a data directory containing
`paper-holdings.json`, unless `SOLVENT_ALLOW_LIVE_SHARED_DATA=1` is explicitly
set. Do not set that override for the competition.

`SOLVENT_RISK_PROFILE=safety` is the unchanged default. The researched
competition profile is `conviction_50`, but switching to it changes real-money
sizing and should be done only as an explicit cutover decision before the
scored window.

`SOLVENT_PRETRADE_ANCHOR=1` adds an ERC-8004 metadata tx before each TWAK swap.
Production proof mode enables it so the pre-trade commit is on-chain before
execution.

`SOLVENT_ADAPTIVE_PROFILE=1` keeps sleeve risk dynamic within the same max
drawdown, kill-switch, and daily requirements. It selects conservative profile
`safety` during degraded or weak signal regimes and moves toward `conviction_50` /
`tournament_60` only when momentum and broad risk appetite improve.

Fire one live cycle immediately instead of waiting for the hourly timer:

```bash
sudo systemctl start solvent.service
journalctl -u solvent.service -n 40 --no-pager
curl -s https://solvent.gudman.xyz/state    # holdings now read from chain
curl -s https://solvent.gudman.xyz/signal   # latest ERC-8183-ready signal
```

> Rehearsal safeguard (optional): the default `safety` profile sleeve target is
> 22% (`RiskConfig.sleeve_frac_target`). More aggressive profiles are available
> through `SOLVENT_RISK_PROFILE`, but the selected profile should be frozen
> before the scored week, not changed mid-run.

## Optional five-minute scanner cadence

`ops/solvent-scan.timer` fires the existing `solvent.service` every five
minutes. It is installed but not enabled by `ops/install.sh`. If using it for
the scored week, disable the hourly timer first:

```bash
sudo systemctl disable --now solvent.timer
sudo systemctl enable --now solvent-scan.timer
sudo systemctl list-timers 'solvent*' --no-pager
```

## ERC-8183 signal provider

The signal payload is public at `/signal`. To sell the same payload through the
BNB Agent SDK ERC-8183 flow, enable the provider service after wallet env is
present:

```bash
sudo systemctl enable --now solvent-erc8183.service
curl -s https://solvent.gudman.xyz/erc8183/status
```

Funded ERC-8183 jobs assigned to the agent wallet are processed by
`solvent.commerce.server`; the job response is the latest
`solvent.daily-regime-signal` payload, and the SDK submits a
`DeliverableManifest` hash on-chain.

## Verify go-live succeeded

- `https://solvent.gudman.xyz` — equity flat→moving, holdings from chain, anchor panel populated.
- A new receipt with real `executions[].tx_hash` (0x…) linking to BscScan.
- `python verify_receipts.py` still `OK`; chain head matches `/verify` and the anchor tx.
- Heartbeat fresh; `journalctl -u solvent.service` clean.

## Recover an unresolved TWAK attempt

The executor never blind-retries a value-moving call. If TWAK times out, exits
without a parseable tx hash, or otherwise leaves a swap outcome unknown, the
journal remains in `ATTEMPTED` and future sends halt until an operator reviews
BSC wallet history.

```bash
sudo -u solvent -H /opt/solvent/.venv/bin/python -m solvent.ops.exec_recovery \
  --data-dir /opt/solvent/data-prod list-unresolved
```

If the transaction mined:

```bash
sudo -u solvent -H /opt/solvent/.venv/bin/python -m solvent.ops.exec_recovery \
  --data-dir /opt/solvent/data-prod mark-confirmed \
  --key '<journal-key>' --tx-hash 0x... --mined-at 2026-06-22T20:05:00Z
```

If no matching successful swap exists:

```bash
sudo -u solvent -H /opt/solvent/.venv/bin/python -m solvent.ops.exec_recovery \
  --data-dir /opt/solvent/data-prod mark-failed \
  --key '<journal-key>' --reason 'Checked BscScan and wallet history; no matching successful swap.'
```

This appends a terminal journal row only. It does not edit receipts and does not
call TWAK.

## Rollback

```bash
# back to paper: set SOLVENT_MODE=paper in solvent.env, then
sudo systemctl start solvent.service
# stop trading entirely:
sudo systemctl stop solvent.timer
# remove TWAK CLI if desired:
npm uninstall -g @trustwallet/cli
```

## Still gated on you (standing rules)

- **External gates:** Telegram G1 (30% DD = start-capital vs peak-equity?) + G6 (mark methodology); CMC G5 (tier covers derivatives/funding).
- **Submission:** DoraHacks BUIDL + on-chain `Register`, demo video, X thread — **only with explicit approval.**
