#!/usr/bin/env bash
# macOS: schedule the daily sync as a launchd agent (com.financesync.sync).
#
# - Runs daily at the given time (default 07:15) and whenever you log in.
# - If the Mac was asleep at that time, launchd runs it when the Mac wakes.
#   (A Mac that was shut down skips it; the login run covers that.)
# - The program skips runs within 20h of the last success, so extra
#   triggers never burn extra SimpleFIN requests.
# - Runs as YOU, inside your login session, so it can read the vault key
#   from your login Keychain. No sudo, no stored password.
#
# Usage:
#   bash scripts/register-launchd.sh            # 07:15
#   bash scripts/register-launchd.sh 06:40
#   bash scripts/register-launchd.sh --print    # show the plist, change nothing
set -euo pipefail

LABEL="com.financesync.sync"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPY="$ROOT/.venv/bin/python"
PRINT_ONLY=0
TIME="07:15"
for arg in "$@"; do
  case "$arg" in
    --print) PRINT_ONLY=1 ;;
    *) TIME="$arg" ;;
  esac
done
if ! [[ "$TIME" =~ ^([01]?[0-9]|2[0-3]):[0-5][0-9]$ ]]; then
  echo "Time must be HH:MM (24-hour), e.g. 07:15" >&2
  exit 1
fi
HOUR=$((10#${TIME%%:*}))
MINUTE=$((10#${TIME##*:}))

xml_escape() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
ROOT_X="$(printf '%s' "$ROOT" | xml_escape)"
VPY_X="$(printf '%s' "$VPY" | xml_escape)"

PLIST_CONTENT="<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VPY_X</string>
    <string>-m</string>
    <string>finance_sync</string>
    <string>sync</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT_X</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$ROOT_X/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$ROOT_X/logs/launchd.log</string>
</dict>
</plist>"

if [ "$PRINT_ONLY" = "1" ]; then
  printf '%s\n' "$PLIST_CONTENT"
  exit 0
fi

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This script is for macOS. On Linux use scripts/register-systemd.sh" >&2
  exit 1
fi
if [ ! -x "$VPY" ]; then
  echo "Run scripts/install.sh first ($VPY not found)." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
printf '%s\n' "$PLIST_CONTENT" > "$PLIST"
plutil -lint "$PLIST" >/dev/null

DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"

echo "Registered $LABEL (daily $TIME + at login)."
echo "Test it now:   launchctl kickstart -k $DOMAIN/$LABEL"
echo "Check status:  launchctl print $DOMAIN/$LABEL | grep -E 'state|last exit'"
echo "Details are in logs/finance-sync.log and the 'Sync Log' tab of your sheet."
echo
echo "First run only: macOS may ask whether python can use your Keychain."
echo "Choose 'Always Allow', or run ./scripts/finance-sync.sh sync --force once by hand first."
