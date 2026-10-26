#!/usr/bin/env bash
# macOS: remove the daily sync launchd agent.
set -euo pipefail
LABEL="com.financesync.sync"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Removed $LABEL."
