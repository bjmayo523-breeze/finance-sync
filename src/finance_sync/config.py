"""Load config.toml with defaults. Nothing here is secret."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from finance_sync import paths


@dataclass
class SimpleFINConfig:
    backfill_days: int = 90
    overlap_days: int = 7
    chunk_days: int = 45
    include_pending: bool = False
    timeout_seconds: int = 60


@dataclass
class LLMConfig:
    enabled: bool = False
    endpoint: str = "http://127.0.0.1:11434"
    model: str = "llama3.1:8b"
    timeout_seconds: int = 60
    max_per_run: int = 150
    allow_remote: bool = False


@dataclass
class ReimbursementConfig:
    enabled: bool = False
    credit_pattern: str = ""            # regex matching the reimbursement deposit description
    expense_account_contains: str = ""  # the card the expenses go on
    expense_categories: list = field(default_factory=lambda: ["Restaurants", "Coffee"])
    auto_window_days: int = 14
    auto_max_items: int = 2
    tagged_window_days: int = 60
    stale_after_days: int = 45


@dataclass
class SummaryConfigToml:
    fixed_categories: list = field(default_factory=lambda: [
        "Mortgage/Rent", "Auto Payment", "Student Loan", "Insurance", "Utilities", "Internet & Phone"])
    salary_categories: list = field(default_factory=lambda: ["Paycheck"])
    equity_categories: list = field(default_factory=lambda: ["RSU Income", "Bonus"])
    one_off_threshold: float = 300.0
    home_value: float = 0.0


@dataclass
class Config:
    spreadsheet_id: str = ""
    timezone: str = "America/Chicago"
    min_hours_between_runs: float = 20
    simplefin: SimpleFINConfig = field(default_factory=SimpleFINConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    reimbursements: ReimbursementConfig = field(default_factory=ReimbursementConfig)
    summary: SummaryConfigToml = field(default_factory=SummaryConfigToml)


def _apply(dc, values: dict) -> None:
    for k, v in values.items():
        if hasattr(dc, k):
            setattr(dc, k, v)


def load(path: Path | None = None) -> Config:
    path = path or paths.CONFIG_FILE
    cfg = Config()
    if not path.exists():
        return cfg
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    cfg.spreadsheet_id = str(raw.get("sheet", {}).get("spreadsheet_id", "") or "").strip()
    cfg.timezone = raw.get("general", {}).get("timezone", cfg.timezone)
    cfg.min_hours_between_runs = float(
        raw.get("sync", {}).get("min_hours_between_runs", cfg.min_hours_between_runs)
    )
    _apply(cfg.simplefin, raw.get("simplefin", {}))
    _apply(cfg.llm, raw.get("llm", {}))
    _apply(cfg.reimbursements, raw.get("reimbursements", {}))
    _apply(cfg.summary, raw.get("summary", {}))
    return cfg


_SHEET_URL_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")
_SHEET_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{20,}$")


def parse_sheet_id(value: str) -> str:
    value = value.strip()
    m = _SHEET_URL_RE.search(value)
    if m:
        return m.group(1)
    if _SHEET_ID_RE.match(value):
        return value
    raise ValueError("That doesn't look like a Google Sheet URL or ID.")


def set_spreadsheet_id(sheet_id: str, path: Path | None = None) -> None:
    """Rewrite the spreadsheet_id line in config.toml, keeping comments."""
    path = path or paths.CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run the install script first "
                                "(scripts\\install.ps1 on Windows, scripts/install.sh on macOS/Linux).")
    text = path.read_text(encoding="utf-8")
    new_line = f'spreadsheet_id = "{sheet_id}"'
    text, n = re.subn(r'(?m)^spreadsheet_id\s*=.*$', new_line, text, count=1)
    if n == 0:
        text = f"[sheet]\n{new_line}\n\n" + text
    path.write_text(text, encoding="utf-8")
