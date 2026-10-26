"""Match work-expense reimbursements to the card charges they repay.

Model
  - The reimbursement deposit is categorized "Reimbursement" (by a rule).
  - The charges it repays are re-categorized "Work Meals".
  - Both categories are Type=Transfer, so neither inflates personal spending
    nor counts as income. Both rows get the same "Reimbursement Match" value
    (the deposit's Transaction ID), so you can filter a group together.

Matching (exact cents only; expense reports reimburse exact receipt amounts)
  1. Charges YOU tagged "Work Meals" that aren't matched yet, within
     tagged_window_days before the deposit, any combination up to 6 charges.
  2. Otherwise, auto-categorized charges in expense_categories on the
     expense account, within auto_window_days, up to auto_max_items charges.
     Only charges whose Category still equals Auto Category are eligible,
     so a category you set by hand is never taken.
  When several combinations add up, the one with the most recent charges wins.
  Anything that can't be matched is reported, never guessed.
"""

from __future__ import annotations

import itertools
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from finance_sync import schema as S
from finance_sync.categorize import parse_amount
from finance_sync.config import ReimbursementConfig
from finance_sync.sheets import Store

log = logging.getLogger(__name__)

WORK = "Work Meals"
REIMB = "Reimbursement"
MATCH = "Reimbursement Match"
MAX_TAGGED_ITEMS = 6
MAX_POOL = 24


def _date(s: str) -> date | None:
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


@dataclass
class Row:
    rec: dict
    row: int
    date: date
    amount: Decimal

    @property
    def id(self) -> str:
        return self.rec.get("Transaction ID", "")


@dataclass
class ReconcileReport:
    matched: list = field(default_factory=list)      # [(deposit Row, [charge Rows], source)]
    unmatched: list = field(default_factory=list)    # [deposit Row]
    stale: list = field(default_factory=list)        # [charge Row] tagged Work Meals, unreimbursed too long

    def warnings(self) -> list[str]:
        out = []
        for r in self.unmatched:
            out.append(
                f"Unmatched reimbursement {r.amount} on {r.date}: tag the charges it repays "
                f"as '{WORK}' and run reconcile"
            )
        if self.stale:
            total = sum(-r.amount for r in self.stale)
            out.append(f"{len(self.stale)} '{WORK}' charges (${total}) not reimbursed yet - check your expense reports")
        return out

    def summary(self, include_warnings: bool = True) -> str:
        lines = [f"Matched {len(self.matched)} reimbursements; {len(self.unmatched)} unmatched."]
        for dep, charges, src in self.matched:
            items = " + ".join(f"{c.date:%m-%d} {c.rec.get('Description', '')[:26]} ({-c.amount})" for c in charges)
            lines.append(f"  {dep.date} +{dep.amount} <= {items}  [{src}]")
        if include_warnings:
            for w in self.warnings():
                lines.append(f"  WARNING: {w}")
        return "\n".join(lines)


def _find(pool: list[Row], target: Decimal, max_items: int) -> list[Row] | None:
    pool = sorted(pool, key=lambda r: r.date, reverse=True)[:MAX_POOL]
    for n in range(1, min(max_items, len(pool)) + 1):
        hits = [c for c in itertools.combinations(pool, n) if -sum(r.amount for r in c) == target]
        if hits:
            return list(max(hits, key=lambda c: (min(r.date for r in c), max(r.date for r in c))))
    return None


def reconcile(store: Store, cfg: ReimbursementConfig, today: date | None = None,
              dry_run: bool = False) -> ReconcileReport:
    rep = ReconcileReport()
    if not cfg.enabled or not cfg.credit_pattern:
        return rep
    today = today or date.today()
    credit_rx = re.compile(cfg.credit_pattern, re.IGNORECASE)
    acct = cfg.expense_account_contains.lower()
    cats = {c.lower() for c in cfg.expense_categories}

    rows: list[Row] = []
    for rec in store.records(S.TRANSACTIONS):
        d, a = _date(rec.get("Date", "")), parse_amount(rec.get("Amount"))
        if d is None or a is None:
            continue
        rows.append(Row(rec, rec["_row"], d, a))

    taken = {r.id for r in rows if str(r.rec.get(MATCH, "")).strip()}
    deposits = sorted(
        (r for r in rows if r.amount > 0 and credit_rx.search(r.rec.get("Description", ""))
         and not str(r.rec.get(MATCH, "")).strip()),
        key=lambda r: r.date,
    )
    charges = [r for r in rows if r.amount < 0 and acct in r.rec.get("Account", "").lower()]
    updates: list[tuple[int, str, str]] = []

    for dep in deposits:
        def window(days: int):
            return [c for c in charges if c.id not in taken and dep.date - timedelta(days=days) <= c.date <= dep.date]

        tagged = [c for c in window(cfg.tagged_window_days) if c.rec.get("Category", "").strip() == WORK]
        hit, src = _find(tagged, dep.amount, MAX_TAGGED_ITEMS), "tagged"
        if not hit:
            auto = [
                c for c in window(cfg.auto_window_days)
                if c.rec.get("Category", "").strip().lower() in cats
                and c.rec.get("Category", "").strip() == c.rec.get("Auto Category", "").strip()
            ]
            hit, src = _find(auto, dep.amount, cfg.auto_max_items), "auto"
        if not hit:
            rep.unmatched.append(dep)
            continue
        rep.matched.append((dep, hit, src))
        taken.update(c.id for c in hit)
        updates.append((dep.row, MATCH, dep.id))
        if dep.rec.get("Category", "").strip() in ("", S.UNCATEGORIZED):
            updates.append((dep.row, "Category", REIMB))
        for c in hit:
            updates.append((c.row, MATCH, dep.id))
            if src == "auto":
                updates += [
                    (c.row, "Category", WORK),
                    (c.row, "Auto Category", WORK),
                    (c.row, "Categorized By", f"match:{dep.date:%Y-%m-%d}"),
                ]

    stale_cutoff = today - timedelta(days=cfg.stale_after_days)
    rep.stale = [
        c for c in charges
        if c.rec.get("Category", "").strip() == WORK and c.id not in taken and c.date < stale_cutoff
    ]
    if not dry_run and updates:
        store.add_missing_headers(S.TRANSACTIONS, S.TXN_HEADERS)
        store.update_cells(S.TRANSACTIONS, updates)
    return rep
