#!/usr/bin/env bash
# Linux: schedule the daily sync as a systemd *user* timer (finance-sync.timer).
#
# - Runs daily at the given time (default 07:15). Persistent=true runs a missed
#   time as soon as the machine is up again.
# - Runs as YOU. The vault key lives in your desktop keyring (GNOME Keyring /
#   KWallet via Secret Service), which is only unlocked while you're logged in,
#   so this suits a desktop or laptop, not a headless server.
#
# Usage:
#   bash scripts/register-systemd.sh          # 07:15
#   bash scripts/register-systemd.sh 06:40
set -euo pipefail
TIME="${1:-07:15}"
if ! [[ "$TIME" =~ ^([01]?[0-9]|2[0-3]):[0-5][0-9]$ ]]; then
  echo "Time must be HH:MM (24-hour), e.g. 07:15" >&2
  exit 1
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPY="$ROOT/.venv/bin/python"
[ -x "$VPY" ] || { echo "Run scripts/install.sh first ($VPY not found)." >&2; exit 1; }

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR" "$ROOT/logs"
cat > "$UNIT_DIR/finance-sync.service" <<EOF
[Unit]
Description=Read-only SimpleFIN -> Google Sheets transaction sync
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart="$VPY" -m finance_sync sync
TimeoutStartSec=30min
EOF
cat > "$UNIT_DIR/finance-sync.timer" <<EOF
[Unit]
Description=Daily Finance Sync

[Timer]
OnCalendar=*-*-* $TIME:00
Persistent=true
RandomizedDelaySec=2min

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now finance-sync.timer
echo "Registered finance-sync.timer (daily $TIME)."
echo "Test it now:   systemctl --user start finance-sync.service"
echo "Check result:  systemctl --user status finance-sync.service"
echo "Remove it:     systemctl --user disable --now finance-sync.timer"
