#!/usr/bin/env bash
# Run any Finance Sync command with the project's own Python (macOS / Linux).
# Examples:
#   ./scripts/finance-sync.sh doctor
#   ./scripts/finance-sync.sh sync --dry-run
#   ./scripts/finance-sync.sh recategorize
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPY="$ROOT/.venv/bin/python"
if [ ! -x "$VPY" ]; then
  echo "Not installed yet. Run: bash scripts/install.sh" >&2
  exit 1
fi
cd "$ROOT"
exec "$VPY" -m finance_sync "$@"
