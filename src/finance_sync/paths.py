"""Filesystem locations. Everything lives inside the project folder."""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    env = os.environ.get("FINANCE_SYNC_HOME")
    if env:
        return Path(env).resolve()
    # src/finance_sync/paths.py -> project root is two levels above src/
    return Path(__file__).resolve().parents[2]


ROOT = project_root()
CONFIG_DIR = ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "config.toml"
CONFIG_EXAMPLE = CONFIG_DIR / "config.example.toml"
SEED_CATEGORIES = CONFIG_DIR / "seed_categories.csv"
SEED_RULES = CONFIG_DIR / "seed_rules.csv"
DATA_DIR = ROOT / "data"
VAULT_FILE = DATA_DIR / "secrets.vault"
STATE_FILE = DATA_DIR / "state.json"
LOCK_FILE = DATA_DIR / "sync.lock"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "finance-sync.log"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
