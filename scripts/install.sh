#!/usr/bin/env bash
# One-time install for Finance Sync on macOS or Linux: creates .venv, installs
# the package, creates config/config.toml, then runs `doctor`.
#
# Run from the project folder:
#   bash scripts/install.sh
# Safe to re-run (it upgrades in place).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

find_python() {
  for c in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        command -v "$c"
        return 0
      fi
    fi
  done
  return 1
}

echo "== Finance Sync install =="
echo "Project folder: $ROOT"

if ! PY="$(find_python)"; then
  echo
  echo "Python 3.11 or newer was not found."
  if [ "$(uname -s)" = "Darwin" ]; then
    echo "Install it from https://www.python.org/downloads/macos/ or with Homebrew: brew install python@3.12"
  else
    echo "Install it with your package manager, e.g. sudo apt install python3.12 python3.12-venv"
  fi
  echo "Then open a new terminal and run this script again."
  exit 1
fi
echo "Using Python: $PY"

if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment (.venv)..."
  "$PY" -m venv .venv
fi
VPY="$ROOT/.venv/bin/python"

echo "Installing dependencies (one minute)..."
"$VPY" -m pip install --upgrade pip --quiet --disable-pip-version-check
"$VPY" -m pip install -e . --quiet --disable-pip-version-check

mkdir -p data logs
chmod 700 data
if [ ! -f config/config.toml ]; then
  cp config/config.example.toml config/config.toml
  echo "Created config/config.toml"
fi

echo
echo "== Health check (FAIL items are expected until setup is done) =="
"$VPY" -m finance_sync doctor || true

echo
echo "Installed. Next, from this folder run, in order:"
echo "  ./scripts/finance-sync.sh setup-google \"\$HOME/Downloads/<downloaded-key>.json\""
echo "  ./scripts/finance-sync.sh set-sheet \"<your Google Sheet URL>\""
echo "  ./scripts/finance-sync.sh setup-simplefin"
echo "  ./scripts/finance-sync.sh init-sheet"
echo "  ./scripts/finance-sync.sh doctor --online"
echo "  ./scripts/finance-sync.sh sync --dry-run"
echo "  ./scripts/finance-sync.sh sync --force"
if [ "$(uname -s)" = "Darwin" ]; then
  echo "  bash scripts/register-launchd.sh"
else
  echo "  bash scripts/register-systemd.sh"
fi
