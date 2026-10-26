"""Rule-based categorization (the AutoCat equivalent).

Rules live in the sheet's `Rules` tab, so you edit them in Google Sheets.
Evaluation: lowest Priority first (blank = 1000), ties in row order; first
match wins.

Columns:
  Pattern          text to look for. Prefix with `re:` for a regex.
  Match            contains (default) | exact | starts | regex
  Field            description (default) | payee | any (description+payee+memo)
  Account Contains optional: only apply when the account name contains this
  Min/Max Amount   optional, on the signed amount (negative = money out)
  Category         must exist in the Categories tab
All text matching is case-insensitive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from finance_sync.models import Transaction


_STORE_NUM = re.compile(r"[#*]?\b\d{3,}\b|\s#\d+")


_ACH_TAIL = re.compile(r"\s+(PPD|CCD|WEB|TEL)\s+ID:?.*$|\s+ID:\s*\S+$")


def merchant_key(desc: str) -> str:
    """Group 'CHICK-FIL-A #04330' and 'CHICK-FIL-A #01112' together for reporting."""
    s = _ACH_TAIL.sub("", str(desc).upper())
    s = _STORE_NUM.sub(" ", s)
    return " ".join(s.split())[:32]


def parse_amount(s) -> Decimal | None:
    s = str(s or "").strip().lstrip("'").replace("$", "").replace(",", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


@dataclass
class Rule:
    row: int
    priority: float
    pattern: str
    match: str
    field: str
    account_contains: str
    min_amount: Decimal | None
    max_amount: Decimal | None
    category: str
    _rx: re.Pattern | None = None

    def text_for(self, t: Transaction) -> str:
        if self.field == "payee":
            return t.payee
        if self.field == "any":
            return " ".join(x for x in (t.description, t.payee, t.memo) if x)
        return t.description

    def matches(self, t: Transaction, account_name: str) -> bool:
        if self.account_contains and self.account_contains.lower() not in account_name.lower():
            return False
        if self.min_amount is not None and t.amount < self.min_amount:
            return False
        if self.max_amount is not None and t.amount > self.max_amount:
            return False
        text = self.text_for(t)
        if self._rx is not None:
            return bool(self._rx.search(text))
        hay, needle = text.lower(), self.pattern.lower()
        if self.match == "exact":
            return hay == needle
        if self.match == "starts":
            return hay.startswith(needle)
        return needle in hay


class RuleSet:
    def __init__(self, rules: list[Rule], warnings: list[str] | None = None) -> None:
        self.rules = sorted(rules, key=lambda r: (r.priority, r.row))
        self.warnings = warnings or []

    @classmethod
    def from_records(cls, records: list[dict], categories: set[str] | None = None) -> "RuleSet":
        rules, warnings = [], []
        for i, r in enumerate(records):
            row = int(r.get("_row", i + 2))
            pattern = str(r.get("Pattern", "")).strip()
            category = str(r.get("Category", "")).strip()
            if not pattern or not category:
                continue
            match = (str(r.get("Match", "")).strip().lower() or "contains")
            field = (str(r.get("Field", "")).strip().lower() or "description")
            if field not in ("description", "payee", "any"):
                warnings.append(f"Rules row {row}: unknown Field '{field}', using description")
                field = "description"
            rx = None
            if pattern.lower().startswith("re:"):
                pattern, match = pattern[3:], "regex"
            if match == "regex":
                try:
                    rx = re.compile(pattern, re.IGNORECASE)
                except re.error as e:
                    warnings.append(f"Rules row {row}: bad regex ({e}); rule skipped")
                    continue
            elif match not in ("contains", "exact", "starts"):
                warnings.append(f"Rules row {row}: unknown Match '{match}', using contains")
                match = "contains"
            try:
                priority = float(str(r.get("Priority", "")).strip() or 1000)
            except ValueError:
                priority = 1000.0
            if categories and category not in categories:
                warnings.append(f"Rules row {row}: category '{category}' is not in Categories tab")
            rules.append(
                Rule(
                    row=row,
                    priority=priority,
                    pattern=pattern,
                    match=match,
                    field=field,
                    account_contains=str(r.get("Account Contains", "")).strip(),
                    min_amount=parse_amount(r.get("Min Amount")),
                    max_amount=parse_amount(r.get("Max Amount")),
                    category=category,
                    _rx=rx,
                )
            )
        return cls(rules, warnings)

    def categorize(self, t: Transaction, account_name: str = "") -> tuple[str, str] | None:
        for rule in self.rules:
            if rule.matches(t, account_name):
                return rule.category, f"rule:row{rule.row}"
        return None
