# SOLVENT Live Cutover Checklist

This is the operator checklist for verifying or resuming the BSC mainnet scored
week setup. It is written to avoid two failure modes: mixing paper and live
accounting, and blindly retrying a value-moving TWAK call.

Production live rehearsal currently uses `/opt/solvent/data-prod`. Do not reset
that state or change stake sizing without explicit approval.

## 1. Pre-cutover checks

Run these from the VPS as the `solvent` user:

```bash
cd /opt/solvent
sudo -u solvent -H .venv/bin/python -m solvent.ops.preflight --env-file /opt/solvent/solvent.env
sudo -u solvent -H .venv/bin/python -m solvent.ops.readiness --env-file /opt/solvent/solvent.env --data-dir /opt/solvent/data-prod --profile live
sudo -u solvent -H .venv/bin/python -m solvent.ops.exec_recovery --data-dir /opt/solvent/data-prod list-unresolved
sudo -u solvent -H twak compete status
sudo -u solvent -H twak wallet balance --chain bsc --json
sudo -u solvent -H twak swap USDT USDC --usd 1 --chain bsc --quote-only --json
sudo -u solvent -H .venv/bin/python -m solvent.policy.manifest --profile "${SOLVENT_RISK_PROFILE:-safety}" --out /opt/solvent/data-prod/policy-manifest.json
```

Required outcomes:

- `SOLVENT_WALLET_ADDRESS`, `SOLVENT_TRADE_NETWORK`, and `SOLVENT_TWAK_CHAIN`
  are present in preflight.
- `paper_data_in_dir` is `false` for `/opt/solvent/data-prod`.
- `journal_has_unresolved` is `false`.
- Track 1 status is registered for
  `0xE4fe23FB57dbb9AC2f685ea29B6b9A1409A0d359`.
- BNB gas and in-scope stable balances are non-zero.
- Quote-only TWAK swap succeeds on `bsc`.
- `/opt/solvent/data-prod/policy-manifest.json` exists and its `manifest_hash`
  is the policy hash referenced during the scored week.

## 2. Live data directory

Use a dedicated directory:

```bash
sudo install -d -o solvent -g solvent -m 0750 /opt/solvent/data-prod
sudo -u solvent -H test ! -e /opt/solvent/data-prod/paper-holdings.json
```

The runner refuses live mode if `paper-holdings.json` is present, unless
`SOLVENT_ALLOW_LIVE_SHARED_DATA=1` is set. Do not set that override for the
competition.

## 3. Cutover command sequence

The live env block in `/opt/solvent/solvent.env` should resolve to:

```bash
SOLVENT_MODE=live
SOLVENT_DATA_DIR=/opt/solvent/data-prod
SOLVENT_TRADE_NETWORK=bsc-mainnet
SOLVENT_TWAK_CHAIN=bsc
SOLVENT_WALLET_ADDRESS=0xE4fe23FB57dbb9AC2f685ea29B6b9A1409A0d359
SOLVENT_PRETRADE_ANCHOR=1
```

Then run one cycle manually:

```bash
sudo systemctl stop solvent.timer
sudo systemctl start solvent.service
journalctl -u solvent.service -n 80 --no-pager
sudo -u solvent -H .venv/bin/python -m solvent.ops.preflight --env-file /opt/solvent/solvent.env
sudo -u solvent -H .venv/bin/python -m solvent.ops.readiness --env-file /opt/solvent/solvent.env --profile live
curl -s https://solvent.gudman.xyz/state
curl -s https://solvent.gudman.xyz/verify
```

If the one-shot cycle exits cleanly and the heartbeat is fresh, re-enable the
hourly timer:

```bash
sudo systemctl enable --now solvent.timer
systemctl list-timers 'solvent*'
```

## 4. Unresolved TWAK attempt recovery

When `TwakExecutor` cannot prove whether a swap landed, it leaves the journal
entry in `ATTEMPTED` and halts future sends. Recovery is manual and append-only.
Never retry the same intent key.

List halted attempts:

```bash
sudo -u solvent -H .venv/bin/python -m solvent.ops.exec_recovery --data-dir /opt/solvent/data-prod list-unresolved
```

If BscScan/wallet history shows the transaction mined:

```bash
sudo -u solvent -H .venv/bin/python -m solvent.ops.exec_recovery --data-dir /opt/solvent/data-prod \
  mark-confirmed --key '<journal-key>' --tx-hash 0x... --mined-at 2026-06-22T20:05:00Z
```

If chain history shows no matching swap, or only a failed/reverted tx:

```bash
sudo -u solvent -H .venv/bin/python -m solvent.ops.exec_recovery --data-dir /opt/solvent/data-prod \
  mark-failed --key '<journal-key>' --reason 'Checked BscScan and wallet history; no matching successful swap.'
```

After either action, rerun preflight. If another `ATTEMPTED` row remains, repeat
the review for that key before resuming trading.

## 5. Rollback

Stop trading first:

```bash
sudo systemctl stop solvent.timer
sudo systemctl stop solvent.service
```

Then set `SOLVENT_MODE=paper` and `SOLVENT_DATA_DIR=/opt/solvent/data` in the
service environment before restarting paper mode.
