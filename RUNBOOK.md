# SOLVENT — go-live runbook

The build is done and running in **paper** mode on the VPS. Going live is four
operational steps — all of which move real money or create on-chain state, so
each is gated on an explicit decision. Nothing here has been executed yet.

Host: `root@75.119.153.252`, agent runs as the **`solvent`** user, env file
`/opt/solvent/solvent.env` (loaded by every systemd unit). The cycle unit runs
`python -m solvent.run --mode ${SOLVENT_MODE} …`, so **flipping `SOLVENT_MODE`
in that file is the live switch** — no unit edits.

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
TWAK_WALLET_PASSWORD=...
```

> ⚠️ VERIFY AT RUN TIME: that `twak` invoked by the **`solvent`** user (not root)
> finds this wallet + creds — the executor runs as `solvent`. If `twak setup`
> stored config under root's home, re-run it as `solvent` or rely on the env
> vars above. Confirm with `sudo -u solvent twak compete status`.

## Step 2 — Fund the agent wallet (real money, BSC mainnet)

Send the rehearsal stake to the Step-1 address on **BSC mainnet**:
- ~$50 in a floor stable (USDT) for the rehearsal; top up to ~$300 for the scored week.
- ~$5 of BNB for gas (swaps cost ~$0.006 each; the binding cost is the ~0.25% DEX fee).

```bash
sudo -u solvent -H twak wallet balance --chain bsc    # confirm funds landed
```

## Step 3 — Register the ERC-8004 identity + first anchor (BSC mainnet)

Lights up the dashboard's on-chain anchor panel. This uses the `solvent` user's
TWAK wallet/keychain on BSC mainnet; the wallet needs a small BNB balance for
gas before registration or anchoring can mine.

```bash
cd /opt/solvent
sudo -u solvent -H env SOLVENT_ANCHOR_BACKEND=twak SOLVENT_BSC_NETWORK=bsc-mainnet \
  .venv/bin/python -m solvent.receipts.anchor --data-dir /opt/solvent/data --register
# -> prints SOLVENT_AGENT_ID=<n> ; add the three vars below to solvent.env:
# SOLVENT_ANCHOR_BACKEND=twak
# SOLVENT_BSC_NETWORK=bsc-mainnet
# SOLVENT_AGENT_ID=<n>
# Then fire the first anchor:
sudo -u solvent -H env SOLVENT_ANCHOR_BACKEND=twak SOLVENT_BSC_NETWORK=bsc-mainnet SOLVENT_AGENT_ID=<n> \
  .venv/bin/python -m solvent.receipts.anchor --data-dir /opt/solvent/data
sudo systemctl enable --now solvent-anchor.timer     # daily 23:00 UTC
```

## Step 4 — Pre-flight, then flip to live

**Pre-flight (no broadcast)** — confirm TWAK resolves bare symbols on BSC; this
was the one thing untestable without creds:

```bash
sudo -u solvent -H twak swap USDT USDC --usd 1 --chain bsc --quote-only --json
```

If the quote returns sensible token addresses/amounts, add the live block to
`/opt/solvent/solvent.env`:

```
SOLVENT_MODE=live
SOLVENT_PRIVATE_KEY=0x...
SOLVENT_WALLET_PASSWORD=...
SOLVENT_TRADE_NETWORK=bsc-mainnet
SOLVENT_TWAK_CHAIN=bsc
# (TWAK_ACCESS_ID / TWAK_HMAC_SECRET / TWAK_WALLET_PASSWORD already from Step 1)
```

Fire one live cycle immediately instead of waiting for the hourly timer:

```bash
sudo systemctl start solvent.service
journalctl -u solvent.service -n 40 --no-pager
curl -s https://solvent.gudman.xyz/state    # holdings now read from chain
```

> Rehearsal safeguard (optional): the barbell sleeve target is 22%
> (`RiskConfig.sleeve_frac_target`). The plan's rehearsal calls for a 10% cap;
> lowering it is a code/config change and must happen **before** the Jun-20
> strategy freeze, not during the scored week.

## Verify go-live succeeded

- `https://solvent.gudman.xyz` — equity flat→moving, holdings from chain, anchor panel populated.
- A new receipt with real `executions[].tx_hash` (0x…) linking to BscScan.
- `python verify_receipts.py` still `OK`; chain head matches `/verify` and the anchor tx.
- Heartbeat fresh; `journalctl -u solvent.service` clean.

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
