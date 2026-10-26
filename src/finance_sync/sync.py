"""The sync: fetch -> dedupe -> categorize -> append to sheet."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from finance_sync import schema as S
from finance_sync.categorize import RuleSet
from finance_sync.config import Config
from finance_sync.models import FetchResult, Transaction
from finance_sync.sheets import Store, ensure_tabs, nonempty, rows_from_csv
from finance_sync.state import State

log = logging.getLogger(__name__)


@dataclass
class SyncReport:
    status: str = "OK"
    skipped_reason: str = ""
    window_start: datetime | None = None
    window_end: datetime | None = None
    accounts: int = 0
    fetched: int = 0
    new: int = 0
    by_rules: int = 0
    by_llm: int = 0
    uncategorized: int = 0
    balances_logged: int = 0
    holdings_logged: int = 0
    warnings: list[str] = field(default_factory=list)
    category_counts: Counter = field(default_factory=Counter)
    new_rows: list[dict] = field(default_factory=list)
    top_uncategorized: list = field(default_factory=list)  # [(merchant, (count, total))]
    reconcile_summary: str = ""

    def summary(self) -> str:
        if self.skipped_reason:
            return f"SKIPPED: {self.skipped_reason}"
        lines = [
            f"Status: {self.status}",
            f"Window: {self.window_start:%Y-%m-%d} -> {self.window_end:%Y-%m-%d}",
            f"Accounts: {self.accounts}",
            f"Transactions fetched: {self.fetched}, new: {self.new}",
            f"Categorized by rules: {self.by_rules}, by LLM: {self.by_llm}, "
            f"uncategorized: {self.uncategorized}",
            f"Balance rows: {self.balances_logged}, holding rows: {self.holdings_logged}",
        ]
        if self.category_counts:
            top = ", ".join(f"{c} {n}" for c, n in self.category_counts.most_common(8))
            lines.append(f"Top categories (new): {top}")
        if self.reconcile_summary:
            lines.append("Reimbursements: " + self.reconcile_summary)
        for w in self.warnings:
            lines.append(f"WARNING: {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------- sheet init
def init_sheet(store: Store) -> list[str]:
    from finance_sync import paths

    actions = ensure_tabs(store, S.TABS)
    if not nonempty(store.records(S.CATEGORIES), "Category"):
        rows = rows_from_csv(paths.SEED_CATEGORIES)
        store.append(S.CATEGORIES, rows)
        actions.append(f"seeded {len(rows)} categories")
    if not nonempty(store.records(S.RULES), "Pattern"):
        rows = rows_from_csv(paths.SEED_RULES)
        store.append(S.RULES, rows)
        actions.append(f"seeded {len(rows)} rules")
    return actions


def add_new_seed_rules(store: Store) -> int:
    """Append starter rules whose Pattern isn't in the Rules tab yet. Never edits existing rows."""
    from finance_sync import paths

    def key(r):
        return (str(r.get("Pattern", "")).strip().lower(), str(r.get("Category", "")).strip().lower())

    have = {key(r) for r in store.records(S.RULES)}
    new = [r for r in rows_from_csv(paths.SEED_RULES) if key(r) not in have]
    store.append(S.RULES, new)
    return len(new)


def add_new_seed_categories(store: Store) -> int:
    """Append starter categories missing from the Categories tab. Never edits existing rows."""
    from finance_sync import paths

    have = {str(r.get("Category", "")).strip().lower() for r in store.records(S.CATEGORIES)}
    new = [r for r in rows_from_csv(paths.SEED_CATEGORIES) if r["Category"].strip().lower() not in have]
    store.append(S.CATEGORIES, new)
    return len(new)


def top_uncategorized(rows: list[dict], limit: int = 40) -> list:
    from decimal import Decimal

    from finance_sync.categorize import merchant_key, parse_amount

    groups: dict[str, list] = {}
    for r in rows:
        if str(r.get("Category", "")).strip() not in ("", S.UNCATEGORIZED):
            continue
        g = groups.setdefault(merchant_key(r.get("Description", "")), [0, Decimal(0)])
        g[0] += 1
        amt = r.get("Amount")
        g[1] += amt if isinstance(amt, Decimal) else (parse_amount(amt) or Decimal(0))
    ranked = sorted(groups.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
    return [(name, (n, float(total))) for name, (n, total) in ranked[:limit]]


# -------------------------------------------------------------- categorizing
Classifier = Callable[[Transaction, str, list[str]], "str | None"]


@dataclass
class Categorizer:
    rules: RuleSet
    categories: list[str]
    llm: Classifier | None = None
    llm_name: str = ""
    llm_budget: int = 0

    @classmethod
    def from_store(cls, store: Store, llm=None, llm_name="", llm_budget=0) -> "Categorizer":
        cats = [r["Category"].strip() for r in nonempty(store.records(S.CATEGORIES), "Category")]
        rules = RuleSet.from_records(store.records(S.RULES), set(cats))
        return cls(rules, cats, llm, llm_name, llm_budget)

    def __call__(self, t: Transaction, account_name: str) -> tuple[str, str]:
        hit = self.rules.categorize(t, account_name)
        if hit:
            return hit
        if self.llm and self.llm_budget > 0:
            self.llm_budget -= 1
            cat = self.llm(t, account_name, self.categories)
            if cat and cat != S.UNCATEGORIZED:
                return cat, f"llm:{self.llm_name}"
        return S.UNCATEGORIZED, ""


# ---------------------------------------------------------------------- sync
def compute_window(cfg: Config, state: State, now: datetime) -> tuple[datetime, datetime]:
    last = state.last_success
    if last is None:
        start = now - timedelta(days=cfg.simplefin.backfill_days)
    else:
        start = last - timedelta(days=cfg.simplefin.overlap_days)
        # never request more than the backfill horizon
        start = max(start, now - timedelta(days=cfg.simplefin.backfill_days))
    return start, now


def should_skip(cfg: Config, state: State, now: datetime) -> str:
    last = state.last_success
    if last is None:
        return ""
    hours = (now - last).total_seconds() / 3600
    if hours < cfg.min_hours_between_runs:
        return (
            f"last successful sync was {hours:.1f}h ago "
            f"(< {cfg.min_hours_between_runs:g}h). Use --force to run anyway."
        )
    return ""


def _fmt_local(dt: datetime | None, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M") if dt else ""


def run_sync(
    cfg: Config,
    provider,
    store: Store,
    categorizer: Categorizer,
    state: State,
    now: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> SyncReport:
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(cfg.timezone)
    today = now.astimezone(tz).date().isoformat()
    rep = SyncReport()

    if not force:
        reason = should_skip(cfg, state, now)
        if reason:
            rep.skipped_reason = reason
            return rep

    rep.window_start, rep.window_end = compute_window(cfg, state, now)
    result: FetchResult = provider.fetch(
        rep.window_start, rep.window_end, include_pending=cfg.simplefin.include_pending
    )
    rep.accounts = len(result.accounts)
    rep.fetched = len(result.transactions)
    rep.warnings.extend(result.warnings)
    rep.warnings.extend(categorizer.rules.warnings)

    # --- transactions: append only new IDs
    existing = set(store.column(S.TRANSACTIONS, "Transaction ID"))
    imported_at = now.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    new_txns = sorted(
        (t for t in result.transactions if t.key not in existing),
        key=lambda t: (t.date, t.account_id, t.provider_txn_id),
    )
    rows = []
    for t in new_txns:
        acct = result.accounts.get(t.account_id)
        acct_name = acct.name if acct else t.account_id
        cat, source = categorizer(t, acct_name)
        if source.startswith("rule:"):
            rep.by_rules += 1
        elif source.startswith("llm:"):
            rep.by_llm += 1
        else:
            rep.uncategorized += 1
        rep.category_counts[cat] += 1
        rows.append(
            {
                "Date": t.date,
                "Description": t.description,
                "Amount": t.amount,
                "Category": cat,
                "Account": acct_name,
                "Institution": acct.institution if acct else "",
                "Payee": t.payee,
                "Memo": t.memo,
                "Categorized By": source,
                "Auto Category": cat,
                "Transaction ID": t.key,
                "Account ID": t.account_id,
                "Imported At": imported_at,
            }
        )
    rep.new = len(rows)
    rep.new_rows = rows
    rep.top_uncategorized = top_uncategorized(rows)

    # --- accounts: update managed columns in place, append new accounts
    acct_records = store.records(S.ACCOUNTS)
    by_id = {r.get("Account ID", ""): r["_row"] for r in acct_records if r.get("Account ID")}
    acct_updates, acct_new = [], []
    for a in result.accounts.values():
        vals = {
            "Account ID": a.id,
            "Account": a.name,
            "Institution": a.institution,
            "Currency": a.currency,
            "Balance": a.balance,
            "Available": a.available,
            "Balance Date": _fmt_local(a.balance_date, tz),
            "Last Synced": imported_at,
        }
        if a.id in by_id:
            acct_updates.extend((by_id[a.id], h, vals[h]) for h in S.ACCOUNT_MANAGED)
        else:
            acct_new.append(vals)

    # --- balance history: one row per account per day
    bal_keys = set(store.column(S.BALANCES, "Key"))
    bal_rows = []
    for a in result.accounts.values():
        key = f"{today}|{a.id}"
        if a.balance is not None and key not in bal_keys:
            bal_rows.append(
                {
                    "Date": today,
                    "Account": a.name,
                    "Institution": a.institution,
                    "Balance": a.balance,
                    "Available": a.available,
                    "Account ID": a.id,
                    "Key": key,
                }
            )
    rep.balances_logged = len(bal_rows)

    # --- holdings snapshot: one row per holding per day
    hold_keys = set(store.column(S.HOLDINGS, "Key"))
    hold_rows = []
    for h in result.holdings:
        key = f"{today}|{h.account_id}|{h.holding_id}"
        if key in hold_keys:
            continue
        acct = result.accounts.get(h.account_id)
        hold_rows.append(
            {
                "Snapshot Date": today,
                "Account": acct.name if acct else h.account_id,
                "Symbol": h.symbol,
                "Description": h.description,
                "Shares": h.shares,
                "Market Value": h.market_value,
                "Cost Basis": h.cost_basis,
                "Currency": h.currency,
                "Account ID": h.account_id,
                "Key": key,
            }
        )
    rep.holdings_logged = len(hold_rows)

    if rep.warnings:
        rep.status = "WARN"

    if dry_run:
        return rep

    store.append(S.TRANSACTIONS, rows)
    store.update_cells(S.ACCOUNTS, acct_updates)
    store.append(S.ACCOUNTS, acct_new)
    store.append(S.BALANCES, bal_rows)
    store.append(S.HOLDINGS, hold_rows)
    if cfg.reimbursements.enabled:
        from finance_sync.reconcile import reconcile

        rrep = reconcile(store, cfg.reimbursements, today=now.astimezone(tz).date())
        rep.reconcile_summary = rrep.summary(include_warnings=False)
        rep.warnings.extend(rrep.warnings())
        if rrep.warnings() and rep.status == "OK":
            rep.status = "WARN"
    write_log_row(store, rep, now, tz)
    state.mark_success(now)
    try:
        from finance_sync.summary import SummaryConfig, write_summaries

        write_summaries(store, SummaryConfig(**vars(cfg.summary)), today=now.astimezone(tz).date())
    except Exception as e:  # summaries are derived data; never fail the sync over them
        log.warning("Summary tabs not updated: %s", e)
        rep.warnings.append(f"Summary tabs not updated: {e}")
    return rep


def write_log_row(store: Store, rep: SyncReport, now: datetime, tz: ZoneInfo, error: str = "") -> None:
    warnings = list(rep.warnings) + ([error] if error else [])
    store.append(
        S.SYNC_LOG,
        [
            {
                "Timestamp": now.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                "Status": "FAIL" if error else rep.status,
                "Window Start": _fmt_local(rep.window_start, tz),
                "Window End": _fmt_local(rep.window_end, tz),
                "Accounts": rep.accounts,
                "New Transactions": rep.new,
                "By Rules": rep.by_rules,
                "By LLM": rep.by_llm,
                "Uncategorized": rep.uncategorized,
                "Balances Logged": rep.balances_logged,
                "Holdings Logged": rep.holdings_logged,
                "Warnings": " | ".join(warnings)[:45000],
            }
        ],
    )


# -------------------------------------------------------------- recategorize
def _is_machine(by: str) -> bool:
    return by.startswith("rule:") or by.startswith("llm:")


def recategorize(
    store: Store, categorizer: Categorizer, dry_run: bool = False, include_auto: bool = False
) -> tuple[int, int]:
    """Re-apply rules. Returns (examined, changed).

    Always: rows whose Category is blank or Uncategorized.
    With include_auto: also rows the sync categorized (Categorized By = rule:/llm:)
    whose Category still equals Auto Category, i.e. you haven't changed it.
    A row where you typed your own Category is never touched.
    """
    from decimal import Decimal

    from finance_sync.categorize import parse_amount

    examined, changed, updates = 0, 0, []
    for r in store.records(S.TRANSACTIONS):
        cat = str(r.get("Category", "")).strip()
        auto = str(r.get("Auto Category", "")).strip()
        by = str(r.get("Categorized By", "")).strip()
        blank = cat in ("", S.UNCATEGORIZED)
        machine = include_auto and not blank and _is_machine(by) and auto == cat
        if not (blank or machine):
            continue
        examined += 1
        t = Transaction(
            provider="sheet",
            account_id=r.get("Account ID", ""),
            provider_txn_id=r.get("Transaction ID", ""),
            date=datetime.now().date(),
            amount=parse_amount(r.get("Amount")) or Decimal(0),
            description=r.get("Description", ""),
            payee=r.get("Payee", ""),
            memo=r.get("Memo", ""),
        )
        if machine and by.startswith("llm:"):
            # don't re-ask the model; only a rule may override its answer
            hit = categorizer.rules.categorize(t, r.get("Account", ""))
            if not hit:
                continue
            new_cat, source = hit
        else:
            new_cat, source = categorizer(t, r.get("Account", ""))
        if blank and new_cat == S.UNCATEGORIZED:
            continue
        if new_cat == cat and source == by:
            continue
        changed += 1
        updates += [
            (r["_row"], "Category", new_cat),
            (r["_row"], "Auto Category", new_cat),
            (r["_row"], "Categorized By", source),
        ]
    if not dry_run:
        store.update_cells(S.TRANSACTIONS, updates)
    return examined, changed
