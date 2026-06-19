# Handoff — audit remediation (branch `fix/audit-remediation`, PR #1)

Working handoff for whoever picks this up next. Delete once the three open items
below are closed. Everything in the "Done" section is committed, tested, pushed.

## State
- Branch `fix/audit-remediation` → PR #1, base `codex/solvent-private-prep`.
- 6 commits; `python -m pytest -q` → **244 passed**; `ruff check solvent tests` → clean.
- A two-pass audit found 21 limitation clusters; all are addressed (FIX with a
  regression test each, or MITIGATE + honest docs for inherent design limits).
- House rules: no Claude/Anthropic attribution in commits/PRs; never submit
  (PR merge, uploads, forms) without the owner's explicit approval. Files carry
  CRLF on this Windows checkout — the LF→CRLF git warnings are cosmetic.

## Done (commit → items)
- `e94f791` #11 phantom-drawdown halt + peak skew on a missing price; #12 a
  latched HALT still fires the daily qualification trade.
- `22dfefa` #10 pinned-address format validation + pre-broadcast executability
  gate; #3 buy-side min-units slippage verification.
- `0b8bc4b` #14 lock atomic stale-steal; #15 live readiness probes TWAK
  auth/balance + empty-chain note; #16 ERC-8183 price>0 guard + nginx rate-limit;
  #17 alert() robustness + live readiness alert gate.
- `365225a` #19 thesis attribution + dashboard trust labels; #20 esc()
  single-quote + advisor confidence clamp; #21 systemd sandboxing; #18 watcher
  relabel.
- `ae6972e` #4 paper gas/slippage + report per-trade cost; #1/#2/#5/#6/#7/#9
  README "Honest limitations".
- `d9939c3` #21/#9 `UnsetEnvironment=` strips signing secrets from the three
  read-only units (web, watcher, watchdog).

## Open item 1 — finish #10 (swap-by-address) — CODE, needs the VPS
The contract-address pin is enforced **pre-broadcast as an executability gate**
(`solvent/exec/executor.py:241`, `is_executable` at `solvent/kernel/allowlist.py:274`)
and **post-trade** by the settlement verifier, but the swap itself is issued by
SYMBOL: `solvent/exec/executor.py:297-298` builds `twak swap <FROM> <TO> --usd …`.
TWAK (`@trustwallet/cli`) resolves the symbol to a token itself, so if its
resolver ever picks a wrong/honeypot token, funds move before the post-trade
check runs.

`RUNBOOK.md:27` and `RUNBOOK.md:117` document the surface as symbol-based
(`twak swap USDT USDC --usd 1 --chain bsc --quote-only --json`); no address flag
is documented. I could not verify the full `swap` flag set — there is no `twak`
binary on the dev host.

**To resolve, on the VPS:**
```bash
twak swap --help
twak --version
```
- If `swap` accepts a token-address/contract flag: pass the pinned
  `ADDRESSES[symbol]` (`solvent/kernel/allowlist.py:221`) as `--from`/`--to`
  address so the exact contract is bound at broadcast. Update
  `TwakExecutor.execute` (`executor.py:297-308`) and `TwakExecutor.__init__`,
  add a test in `tests/test_executor.py` asserting the address is in the argv.
- If it only accepts symbols (most likely for the Trust Wallet CLI): the current
  pre-broadcast gate + post-trade address check is the ceiling. Document that in
  the `ADDRESSES` docstring (`allowlist.py:205-220`) and mark #10 final.

## Open item 2 — deploy + verify on the VPS (no code; can't run on Windows)
Repo lives at `/opt/solvent` (a git checkout); units install via
`sudo bash ops/install.sh` (copies `ops/*.service` → `/etc/systemd/system`,
`daemon-reload`, enable). nginx conf is `ops/solvent.gudman.xyz.conf` (path on
the box not in install.sh — locate with `nginx -T | grep solvent`).

```bash
cd /opt/solvent && git fetch origin && git checkout fix/audit-remediation && git pull
sudo bash ops/install.sh
# sandboxing didn't break writes + secrets stripped:
systemd-analyze security solvent.service | tail -5
sudo systemctl restart solvent-web.service
systemctl show solvent-web.service -p UnsetEnvironment            # lists 5 secret vars
sudo cat /proc/"$(pgrep -f receipts.server)"/environ | tr '\0' '\n' | grep -c SOLVENT_PRIVATE_KEY   # expect 0
sudo systemctl start solvent.service && journalctl -u solvent.service -n 20 --no-pager  # no read-only-fs error
# nginx:
sudo nginx -t && sudo systemctl reload nginx
# smoke:
sudo -u solvent /opt/solvent/.venv/bin/python -m solvent.ops.readiness --data-dir "$SOLVENT_DATA_DIR" --profile live --env-file /opt/solvent/solvent.env
sudo -u solvent /opt/solvent/.venv/bin/python /opt/solvent/verify_receipts.py
```
If `solvent.service` logs a read-only-filesystem error, `ReadWritePaths=/opt/solvent`
in the units must point at the real `SOLVENT_DATA_DIR` (it defaults under
`/opt/solvent`, so this only bites if the data dir lives elsewhere).

## Open item 3 — decision (not code): #1 anchoring window
Mitigated + documented only. To tighten the ≤24h un-anchored rewrite window,
enable pre-trade anchoring (`SOLVENT_PRETRADE_ANCHOR=1`, already implemented per
`README.md:98`) and/or add a more-frequent head-anchor timer. Trade-off: one
extra on-chain metadata tx (gas) before every trade during the scored week.
Owner decision — do not enable by default.

## Inherent limits left as MITIGATE (by design, not bugs)
#1 anchoring window, #2 inference = self-consistency (not attestation), #5
synthetic/self-graded evidence, #6 survive-not-profit, #7 thin signals, #9
single-operator/shared-host. All are documented in README "Honest limitations".

## Commands
- Tests: `python -m pytest -q`  · Lint: `python -m ruff check solvent tests`
- One paper cycle: `python -m solvent.run --mode paper --data-dir <dir> --once`
