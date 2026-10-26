"""Import older history from bank/card CSV exports.

SimpleFIN only reaches back ~90 days. Most banks let you download 12-24
months as CSV; this imports those rows into the same Transactions tab,
categorized by the same rules.

Safety:
  - Only rows dated BEFORE the account's first SimpleFIN transaction are
    imported (the cutoff), so the two sources never overlap. Override with
    --until only for an account SimpleFIN doesn't cover.
  - Each CSV row gets a stable ID, so importing the same file twice adds
    nothing. Two identical charges on the same day are both kept.
  - Text is written as plain text (formula-safe), same as the sync.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from finance_sync import schema as S
from finance_sync.models import Transaction

FORMATS = ("auto", "chase-checking", "chase-card", "capital-one", "generic")


class ImportErrorCSV(RuntimeError):
    pass


@dataclass
class ParsedRow:
    date: date
    description: str
    amount: Decimal  # negative = money out
    memo: str = ""


@dataclass
class ImportReport:
    fmt: str = ""
    parsed: int = 0
    before_cutoff: int = 0
    after_cutoff: int = 0
    duplicates: int = 0
    new: int = 0
    by_rules: int = 0
    uncategorized: int = 0
    cutoff: date | None = None
    first: date | None = None
    last: date | None = None
    warnings: list[str] = field(default_factory=list)
    sample: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Format: {self.fmt}",
            f"Rows in file: {self.parsed} ({self.first} -> {self.last})",
            f"Cutoff: before {self.cutoff} (first SimpleFIN transaction for this account)"
            if self.cutoff else "Cutoff: none",
            f"Before cutoff: {self.before_cutoff}, skipped at/after cutoff: {self.after_cutoff}",
            f"Already imported: {self.duplicates}, new: {self.new}",
            f"Categorized by rules: {self.by_rules}, uncategorized: {self.uncategorized}",
        ]
        lines += [f"WARNING: {w}" for w in self.warnings]
        return "\n".join(lines)


def _norm(h: str) -> str:
    return re.sub(r"[^a-z]", "", h.lower())


def _money(s: str) -> Decimal | None:
    s = str(s or "").strip().replace("$", "").replace(",", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    try:
        d = Decimal(s.strip("()"))
    except InvalidOperation:
        return None
    return -d if neg else d


def _date(s: str) -> date | None:
    s = str(s or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def detect_format(headers: list[str]) -> str:
    h = {_norm(x) for x in headers}
    if {"details", "postingdate", "description", "amount"} <= h:
        return "chase-checking"
    if {"transactiondate", "postdate", "description", "amount"} <= h:
        return "chase-card"
    if {"transactiondate", "posteddate", "description", "debit", "credit"} <= h:
        return "capital-one"
    if any("date" in x for x in h) and any(x in h for x in ("description", "payee", "merchant", "name")) and (
        "amount" in h or {"debit", "credit"} <= h
    ):
        return "generic"
    raise ImportErrorCSV(
        "Couldn't recognize this CSV. Expected columns like Date, Description, Amount "
        f"(or Debit/Credit). Found: {', '.join(headers)}"
    )


def _pick(row: dict, *names: str) -> str:
    keys = {_norm(k): k for k in row if k}  # extra trailing columns arrive under None
    for n in names:
        if n in keys:
            return row[keys[n]] or ""
    return ""


def parse_csv(text: str, fmt: str = "auto", flip_sign: bool = False) -> tuple[str, list[ParsedRow]]:
    # tolerate banks that put a title line or two above the header
    lines = text.lstrip("﻿").splitlines()
    start = 0
    for i, line in enumerate(lines[:10]):
        low = line.lower()
        if "date" in low and ("amount" in low or "debit" in low):
            start = i
            break
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    headers = [h for h in (reader.fieldnames or []) if h]
    if fmt == "auto":
        fmt = detect_format(headers)
    out: list[ParsedRow] = []
    for row in reader:
        if fmt == "chase-checking":
            d, desc, amt = _date(_pick(row, "postingdate")), _pick(row, "description"), _money(_pick(row, "amount"))
            memo = ""
        elif fmt == "chase-card":
            d, desc, amt = _date(_pick(row, "transactiondate")), _pick(row, "description"), _money(_pick(row, "amount"))
            memo = _pick(row, "memo")
        elif fmt == "capital-one":
            d, desc = _date(_pick(row, "transactiondate")), _pick(row, "description")
            debit, credit = _money(_pick(row, "debit")) or Decimal(0), _money(_pick(row, "credit")) or Decimal(0)
            amt, memo = credit - debit, ""
        else:  # generic
            d = _date(_pick(row, "date", "transactiondate", "postingdate", "postdate", "posteddate"))
            desc = _pick(row, "description", "payee", "merchant", "name")
            if _pick(row, "amount"):
                amt = _money(_pick(row, "amount"))
            else:
                amt = (_money(_pick(row, "credit")) or Decimal(0)) - (_money(_pick(row, "debit")) or Decimal(0))
            memo = _pick(row, "memo", "notes")
        if d is None or amt is None or not str(desc).strip():
            continue
        if flip_sign:
            amt = -amt
        out.append(ParsedRow(d, " ".join(str(desc).split()), amt, " ".join(str(memo).split())))
    return fmt, out


def resolve_account(store, text: str) -> dict:
    accts = [r for r in store.records(S.ACCOUNTS) if r.get("Account ID")]
    hits = [a for a in accts if text.lower() in a.get("Account", "").lower()]
    if len(hits) != 1:
        names = ", ".join(a.get("Account", "") for a in accts)
        raise ImportErrorCSV(
            f"--account '{text}' matched {len(hits)} accounts; it must match exactly one. Accounts: {names}"
        )
    return hits[0]


def first_simplefin_date(store, account_id: str) -> date | None:
    first = None
    for r in store.records(S.TRANSACTIONS):
        if r.get("Account ID") != account_id or not str(r.get("Transaction ID", "")).startswith("si_"):
            continue
        d = _date(r.get("Date", ""))
        if d and (first is None or d < first):
            first = d
    return first


def import_rows(store, categorizer, account: dict, rows: list[ParsedRow], cutoff: date | None,
                imported_at: str, dry_run: bool = False) -> ImportReport:
    rep = ImportReport(parsed=len(rows), cutoff=cutoff)
    if rows:
        rep.first, rep.last = min(r.date for r in rows), max(r.date for r in rows)
    existing = set(store.column(S.TRANSACTIONS, "Transaction ID"))
    seen: Counter = Counter()
    out = []
    for r in sorted(rows, key=lambda r: r.date):
        if cutoff and r.date >= cutoff:
            rep.after_cutoff += 1
            continue
        rep.before_cutoff += 1
        sig = f"{r.date.isoformat()}|{r.amount}|{r.description.upper()}"
        seen[sig] += 1
        t = Transaction(
            provider="csv", account_id=account["Account ID"], provider_txn_id=f"{sig}|{seen[sig]}",
            date=r.date, amount=r.amount, description=r.description, memo=r.memo,
        )
        if t.key in existing:
            rep.duplicates += 1
            continue
        cat, source = categorizer(t, account.get("Account", ""))
        rep.by_rules += source.startswith("rule:")
        rep.uncategorized += cat == S.UNCATEGORIZED
        out.append({
            "Date": t.date, "Description": t.description, "Amount": t.amount, "Category": cat,
            "Account": account.get("Account", ""), "Institution": account.get("Institution", ""),
            "Payee": "", "Memo": t.memo, "Categorized By": source, "Auto Category": cat,
            "Transaction ID": t.key, "Account ID": t.account_id, "Imported At": imported_at,
            "Notes": "imported from CSV",
        })
    rep.new = len(out)
    rep.sample = out[:10]

    # sign sanity check: on a card, most rows should be purchases (negative)
    try:
        bal = float(str(account.get("Balance", "0")).replace(",", "") or 0)
    except ValueError:
        bal = 0
    pos = sum(1 for r in rows if r.amount > 0)
    if rows and bal < 0 and pos / len(rows) > 0.6:
        rep.warnings.append(
            f"{pos} of {len(rows)} rows are positive on what looks like a credit card. "
            "Purchases should be negative: re-run with --flip-sign if this bank exports them positive."
        )
    if not dry_run and out:
        store.add_missing_headers(S.TRANSACTIONS, S.TXN_HEADERS)
        store.append(S.TRANSACTIONS, out)
    return rep


def read_file(path: str | Path) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        raise ImportErrorCSV(f"File not found: {p}")
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ImportErrorCSV("Couldn't read the file's text encoding.")
