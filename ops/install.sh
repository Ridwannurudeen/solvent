#!/usr/bin/env bash
# Install SOLVENT systemd units on the VPS. Run as root from /opt/solvent:
#   sudo bash ops/install.sh
# Idempotent — safe to re-run after a code pull.
set -euo pipefail

APP=/opt/solvent
UNITS=(
  solvent.service solvent.timer
  solvent-deadman.service solvent-deadman.timer
  solvent-watchdog.service solvent-watchdog.timer
  solvent-anchor.service solvent-anchor.timer
)

[[ -d "$APP" ]] || { echo "expected app at $APP"; exit 1; }
[[ -x "$APP/.venv/bin/python" ]] || { echo "create the venv first: python -m venv $APP/.venv && $APP/.venv/bin/pip install -e $APP"; exit 1; }

if [[ "$(date -u +%Z)" != "UTC" ]]; then
  echo "WARNING: host clock is not UTC (timers assume UTC). Set it: timedatectl set-timezone UTC"
fi

if [[ ! -f "$APP/solvent.env" ]]; then
  echo "create $APP/solvent.env from ops/solvent-env.example first"; exit 1
fi

id -u solvent &>/dev/null || useradd -r -s /usr/sbin/nologin -d "$APP" solvent
install -d -o solvent -g solvent "$APP/data"
chown -R solvent:solvent "$APP"
chmod 600 "$APP/solvent.env"

for u in "${UNITS[@]}"; do
  install -m 0644 "$APP/ops/$u" "/etc/systemd/system/$u"
done

systemctl daemon-reload
systemctl enable --now solvent.timer solvent-deadman.timer solvent-watchdog.timer solvent-anchor.timer
systemctl list-timers 'solvent*' --no-pager
