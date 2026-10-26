"""Monthly summaries computed from the ledger, written to read-only tabs.

These tabs are generated: every run replaces their contents. Don't edit them;
change Categories, Rules or account Types instead and they follow.

Definitions (also on the README):
  Cash-flow accounts  checking/savings + credit cards. Investment, retirement,
                      loan and custodial accounts are excluded from income and
                      spending (they appear in Net Worth / Investments).
  Spending            Expense-type categories, net of refunds. Transfer-type
                      categories (card payments, trades, reimbursements, work
                      meals...) never count.
  Salary              Paycheck. Equity & Bonus: RSU Income + Bonus.
  Fixed               mortgage, auto, student loan, insurance, utilities, phone
                      (configurable). Flexible = everything else.
  One-off             a flexible charge >= one_off_threshold from a merchant not
                      seen in the previous 2 months. Run-rate = spending - one-offs.
  Net worth           Cash + Taxable + Retirement + Home - cards - loans.
                      Unvested equity and custodial (UTMA) shown beside it, not in it.
  Cash Position       spendable cash - card balances - expected rest-of-month spending
                      (see build_cash_position). Account Balances: every account now.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from finance_sync import schema as S
from finance_sync.categorize import merchant_key, parse_amount

MONTHLY = "Monthly Summary"
DETAIL = "Month Detail"
TRENDS = "Category Trends"
MERCHANTS = "Top Merchants"
ONEOFFS = "One-offs"
NETWORTH = "Net Worth"
ALLOC = "Allocation"
PERF = "Investment Performance"
CASH = "Cash Position"
BAL = "Account Balances"
SUMMARY_TABS = [MONTHLY, DETAIL, TRENDS, MERCHANTS, ONEOFFS, NETWORTH, ALLOC, PERF, CASH, BAL]

CASH_HEADERS = [
    "As Of", "Month", "Cash", "Cash Available", "Spendable Cash", "Card Balances", "Spent So Far",
    "Baseline Monthly Spending", "Baseline Months", "Expected Rest of Month", "Bills Still Due",
    "Free Cash After Month", "Avg Salary", "Salary Gap", "Avg Equity & Bonus", "Last Paycheck Date",
    "Last Paycheck", "Next Paycheck (est.)", "Taxable Investments",
]
BAL_HEADERS = ["Account", "Institution", "Bucket", "Balance", "Available", "Balance Date"]

MONTHLY_HEADERS = [
    "Month", "Partial", "Salary", "Equity & Bonus", "Other Income", "Total Income",
    "Spending", "Fixed", "Flexible", "One-offs", "Run-rate Spending",
    "Net Cash Flow", "Savings Rate", "Salary Savings Rate", "Transactions",
]
PERCENT_COLS = ["Savings Rate", "Salary Savings Rate", "Share of Spending", "Change vs Avg %", "% of Investable",
                "Market Change %"]

BUCKETS = ["Cash", "Credit Card", "Loan", "Taxable", "Retirement", "Unvested", "Custodial"]


@dataclass
class SummaryConfig:
    fixed_categories: list = field(default_factory=lambda: [
        "Mortgage/Rent", "Auto Payment", "Student Loan", "Insurance", "Utilities", "Internet & Phone"])
    salary_categories: list = field(default_factory=lambda: ["Paycheck"])
    equity_categories: list = field(default_factory=lambda: ["RSU Income", "Bonus"])
    one_off_threshold: float = 300.0
    home_value: float = 0.0


def _d(s) -> date | None:
    s = str(s or "").strip()
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        pass
    try:
        return datetime.strptime(s.split(" ")[0], "%m/%d/%Y").date()
    except ValueError:
        return None


def _f(s) -> float:
    v = parse_amount(s)
    return float(v) if v is not None else 0.0


def _r(x: float) -> float:
    return round(x + 0.0, 2)


# ------------------------------------------------------------- accounts
_RX = [
    ("Loan", r"MORTGAGE|\bLOAN\b"),
    ("Unvested", r"\bRSU\b"),
    ("Custodial", r"UTMA|UGMA|CUSTODIAL"),
    ("Retirement", r"401\s*\(?K\)?|\bIRA\b|ROTH|403\s*\(?B\)?|HSA|HEALTH SAVINGS"),
    ("Taxable", r"INDIVIDUAL|JOINT|BROKERAGE|\bTOD\b|DESIGNATED BENEFICIARY"),
    ("Cash", r"CHECKING|SAVINGS|MONEY MARKET"),
]
_BROKERS = r"FIDELITY|SCHWAB|VANGUARD|E\*?TRADE|ROBINHOOD|MERRILL|MORGAN STANLEY|WEALTHFRONT|BETTERMENT"


def classify_account(name: str, institution: str, balance: float, user_type: str = "") -> str:
    ut = user_type.strip().lower()
    for b in BUCKETS:
        if ut == b.lower() or (b == "Credit Card" and ut in ("credit", "card", "credit card")):
            return b
    up = name.upper()
    for bucket, rx in _RX:
        if re.search(rx, up):
            return bucket
    if re.search(_BROKERS, institution.upper()):
        return "Taxable"
    return "Credit Card" if balance < 0 else "Cash"


# ------------------------------------------------------------- core
@dataclass
class Ledger:
    txns: list
    cat_type: dict
    cat_group: dict
    acct_bucket: dict
    acct_name: dict
    today: date


def load_ledger(store, today: date | None = None) -> Ledger:
    cats = {r["Category"].strip(): r for r in store.records(S.CATEGORIES) if str(r.get("Category", "")).strip()}
    cat_type = {c: (r.get("Type") or "Expense").strip() for c, r in cats.items()}
    cat_group = {c: (r.get("Group") or "").strip() for c, r in cats.items()}
    acct_bucket, acct_name = {}, {}
    for a in store.records(S.ACCOUNTS):
        aid = a.get("Account ID", "")
        if not aid:
            continue
        acct_bucket[aid] = classify_account(a.get("Account", ""), a.get("Institution", ""),
                                            _f(a.get("Balance")), a.get("Type", ""))
        acct_name[aid] = a.get("Account", "")
    txns = []
    for r in store.records(S.TRANSACTIONS):
        d = _d(r.get("Date"))
        if d is None:
            continue
        txns.append({**r, "_date": d, "_amt": _f(r.get("Amount")), "_cat": str(r.get("Category", "")).strip()
                     or S.UNCATEGORIZED})
    return Ledger(txns, cat_type, cat_group, acct_bucket, acct_name, today or date.today())


def _ym(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _prev_months(m: str, n: int) -> list[str]:
    y, mo = int(m[:4]), int(m[5:])
    out = []
    for _ in range(n):
        mo -= 1
        if mo == 0:
            y, mo = y - 1, 12
        out.append(f"{y:04d}-{mo:02d}")
    return out


def build(led: Ledger, cfg: SummaryConfig) -> dict[str, list[list]]:
    """Return {tab: rows including header}."""
    fixed = set(cfg.fixed_categories)
    salary, equity = set(cfg.salary_categories), set(cfg.equity_categories)
    cash_accts = {a for a, b in led.acct_bucket.items() if b in ("Cash", "Credit Card")}

    flow = [t for t in led.txns if t.get("Account ID") in cash_accts or t.get("Account ID") not in led.acct_bucket]
    flow = [t for t in flow if led.cat_type.get(t["_cat"], "Expense") != "Transfer"]
    months = sorted({_ym(t["_date"]) for t in flow})
    first_day = min((t["_date"] for t in flow), default=None)
    this_month = _ym(led.today)

    by_month = defaultdict(list)
    for t in flow:
        by_month[_ym(t["_date"])].append(t)

    # one-offs: flexible, big, merchant unseen in the previous 2 months
    merchants_by_month = defaultdict(set)
    for t in flow:
        merchants_by_month[_ym(t["_date"])].add(merchant_key(t.get("Description", "")))
    oneoff_rows, oneoff_total = [], Counter()
    for t in flow:
        m = _ym(t["_date"])
        if led.cat_type.get(t["_cat"], "Expense") != "Expense" or t["_cat"] in fixed:
            continue
        if -t["_amt"] < cfg.one_off_threshold:
            continue
        prev2 = [p for p in _prev_months(m, 2) if p in merchants_by_month]
        if not prev2:  # no history to compare against yet (first month of data)
            continue
        mk = merchant_key(t.get("Description", ""))
        if any(mk in merchants_by_month[p] for p in prev2):
            continue
        oneoff_total[m] += -t["_amt"]
        oneoff_rows.append([m, t["_date"].isoformat(), t.get("Description", ""), t["_cat"],
                            led.acct_name.get(t.get("Account ID"), t.get("Account", "")), _r(-t["_amt"])])

    # monthly summary
    monthly = [MONTHLY_HEADERS]
    spend_cat = defaultdict(Counter)
    for m in months:
        inc_sal = inc_eq = inc_other = spend = fx = 0.0
        for t in by_month[m]:
            ty = led.cat_type.get(t["_cat"], "Expense")
            if ty == "Income":
                if t["_cat"] in salary:
                    inc_sal += t["_amt"]
                elif t["_cat"] in equity:
                    inc_eq += t["_amt"]
                else:
                    inc_other += t["_amt"]
            elif ty == "Expense":
                spend += -t["_amt"]
                spend_cat[m][t["_cat"]] += -t["_amt"]
                if t["_cat"] in fixed:
                    fx += -t["_amt"]
        total = inc_sal + inc_eq + inc_other
        partial = "yes" if (m == this_month or (first_day and m == _ym(first_day) and first_day.day > 3)) else ""
        net = total - spend
        monthly.append([
            m, partial, _r(inc_sal), _r(inc_eq), _r(inc_other), _r(total), _r(spend), _r(fx), _r(spend - fx),
            _r(oneoff_total[m]), _r(spend - oneoff_total[m]), _r(net),
            round(net / total, 4) if total else "", round((inc_sal - spend) / inc_sal, 4) if inc_sal else "",
            len(by_month[m]),
        ])

    # month detail (long) with prior-3-month average of complete months
    complete = {row[0] for row in monthly[1:] if not row[1]}
    detail = [["Month", "Category", "Group", "Amount", "Prior 3-Mo Avg", "Change vs Avg", "Change vs Avg %",
               "Share of Spending", "Fixed"]]
    for m in months:
        total = sum(spend_cat[m].values())
        prior = [p for p in _prev_months(m, 3) if p in complete]
        cats = set(spend_cat[m]) | {c for p in prior for c in spend_cat[p]}
        rows = []
        for c in cats:
            amt = spend_cat[m].get(c, 0.0)
            avg = sum(spend_cat[p].get(c, 0.0) for p in prior) / len(prior) if prior else None
            if abs(amt) < 0.005 and not avg:
                continue
            rows.append([m, c, led.cat_group.get(c, ""), _r(amt),
                         _r(avg) if avg is not None else "",
                         _r(amt - avg) if avg is not None else "",
                         round((amt - avg) / avg, 4) if avg else "",
                         round(amt / total, 4) if total else "", "yes" if c in fixed else ""])
        detail += sorted(rows, key=lambda r: -r[3])

    # category trends (wide), columns ordered by total spend
    totals = Counter()
    for m in months:
        totals.update(spend_cat[m])
    ranked = [c for c, _ in totals.most_common() if totals[c] > 0]
    order = [c for c in ranked if c not in fixed] + [c for c in ranked if c in fixed]
    trends = [["Month", *order]] + [[m, *[_r(spend_cat[m].get(c, 0.0)) for c in order]] for m in months]

    # top merchants per month
    merch = [["Month", "Merchant", "Category", "Fixed", "Count", "Amount"]]
    for m in months:
        agg = defaultdict(lambda: [0, 0.0, Counter()])
        for t in by_month[m]:
            if led.cat_type.get(t["_cat"], "Expense") != "Expense":
                continue
            k = merchant_key(t.get("Description", ""))
            agg[k][0] += 1
            agg[k][1] += -t["_amt"]
            agg[k][2][t["_cat"]] += 1
        top = sorted(agg.items(), key=lambda kv: -kv[1][1])[:20]
        for k, v in top:
            if v[1] > 0:
                c = v[2].most_common(1)[0][0]
                merch.append([m, k, c, "yes" if c in fixed else "", v[0], _r(v[1])])

    oneoffs = [["Month", "Date", "Description", "Category", "Account", "Amount"]] + sorted(oneoff_rows)

    return {MONTHLY: monthly, DETAIL: detail, TRENDS: trends, MERCHANTS: merch, ONEOFFS: oneoffs}


def manual_accounts(store) -> list[dict]:
    """Rows from the Manual Accounts tab with a number and a date. Bucket defaults from the name."""
    if S.MANUAL not in store.tab_names():
        return []
    out = []
    for r in store.records(S.MANUAL):
        name, d, bal = str(r.get("Account", "")).strip(), _d(r.get("As Of")), parse_amount(r.get("Balance"))
        if not name or d is None or bal is None:
            continue
        out.append({"name": name, "as_of": d, "balance": float(bal),
                    "bucket": classify_account(name, str(r.get("Institution", "")), float(bal), str(r.get("Bucket", "")))})
    return out


def build_net_worth(store, led: Ledger, cfg: SummaryConfig) -> list[list]:
    by_date = defaultdict(dict)
    for r in store.records(S.BALANCES):
        d, aid = _d(r.get("Date")), r.get("Account ID", "")
        if d and aid:
            by_date[d][aid] = _f(r.get("Balance"))
    hdr = ["Date", "Cash", "Taxable", "Retirement", "Home", "Credit Cards", "Loans", "Net Worth",
           "Liquid Net Worth", "Unvested Equity", "Custodial"]
    out = [hdr]
    last: dict = {}
    manual = manual_accounts(store)
    for d in sorted(by_date):
        last.update(by_date[d])  # carry forward accounts missing that day
        b = Counter()
        for aid, bal in last.items():
            b[led.acct_bucket.get(aid, "Cash")] += bal
        newest: dict = {}
        for m in manual:  # newest entry on or before this date, per manual account
            if m["as_of"] <= d and (m["name"] not in newest or m["as_of"] >= newest[m["name"]]["as_of"]):
                newest[m["name"]] = m
        for m in newest.values():
            b[m["bucket"]] += m["balance"]
        nw = b["Cash"] + b["Taxable"] + b["Retirement"] + cfg.home_value + b["Credit Card"] + b["Loan"]
        liquid = b["Cash"] + b["Taxable"] + b["Credit Card"]
        out.append([d.isoformat(), _r(b["Cash"]), _r(b["Taxable"]), _r(b["Retirement"]), _r(cfg.home_value),
                    _r(b["Credit Card"]), _r(b["Loan"]), _r(nw), _r(liquid), _r(b["Unvested"]), _r(b["Custodial"])])
    return out


_OPT = re.compile(r"^([A-Z]{1,5})\d{6}[CP]\d+")
_OPT_DESC = re.compile(r"^(CALL|PUT)\s*\(([A-Z.]+)\)", re.I)


def underlying(symbol: str, description: str) -> str:
    m = _OPT.match(symbol or "") or None
    if m:
        return m.group(1)
    m2 = _OPT_DESC.match(description or "")
    if m2:
        return m2.group(2).upper()
    return (symbol or description or "?").strip().upper()


def build_allocation(store, led: Ledger) -> list[list]:
    recs = store.records(S.HOLDINGS)
    latest = max((_d(r.get("Snapshot Date")) for r in recs if _d(r.get("Snapshot Date"))), default=None)
    hdr = ["Holding", "Description", "Market Value", "% of Investable", "Bucket", "Accounts", "Note"]
    agg = defaultdict(lambda: {"v": 0.0, "desc": "", "accts": set(), "bucket": set(), "opt": False})
    for r in recs:
        if latest is None or _d(r.get("Snapshot Date")) != latest:
            continue
        bucket = led.acct_bucket.get(r.get("Account ID", ""), "Taxable")
        if bucket not in ("Taxable", "Retirement"):
            continue
        sym, desc = r.get("Symbol", ""), r.get("Description", "")
        if not sym and desc.upper() == "CASH":
            sym = "CASH"
        u = underlying(sym, desc)
        a = agg[u]
        a["v"] += _f(r.get("Market Value"))
        a["desc"] = a["desc"] or (desc if not _OPT_DESC.match(desc or "") else "")
        a["accts"].add(led.acct_name.get(r.get("Account ID", ""), r.get("Account", "")))
        a["bucket"].add(bucket)
        a["opt"] |= bool(_OPT.match(sym or "") or _OPT_DESC.match(desc or ""))
    newest: dict = {}
    for m in manual_accounts(store):  # e.g. Betterment: one line, treated as a diversified portfolio
        if m["bucket"] in ("Taxable", "Retirement") and (m["name"] not in newest or m["as_of"] >= newest[m["name"]]["as_of"]):
            newest[m["name"]] = m
    for m in newest.values():
        a = agg[m["name"]]
        a["v"], a["desc"], a["manual"] = m["balance"], f"Manual balance as of {m['as_of'].isoformat()}", True
        a["accts"].add(m["name"])
        a["bucket"].add(m["bucket"])
    total = sum(a["v"] for a in agg.values())
    rows = []
    for u, a in sorted(agg.items(), key=lambda kv: -kv[1]["v"]):
        share = a["v"] / total if total else 0
        note = []
        if a["opt"]:
            note.append("includes options")
        if a.get("manual"):
            note.append("manual balance")
        elif share >= 0.10 and u not in _FUNDS_HINT and not _looks_like_fund(a["desc"]):
            note.append("single-stock concentration")
        rows.append([u, a["desc"] or u, _r(a["v"]), round(share, 4), "/".join(sorted(a["bucket"])),
                     "; ".join(sorted(a["accts"])), ", ".join(note)])
    return [hdr] + rows


_FUNDS_HINT = {"VTI", "VOO", "VXUS", "BND", "FXAIX", "SPY", "IVV", "SCHB", "SCHE", "VT", "QQQ", "CASH"}


def _looks_like_fund(desc: str) -> bool:
    return bool(re.search(r"\b(FUND|INDEX|IDX|ETF|TRUST|TR|POOL|PORTFOLIO|CASH RESERVES|MONEY MARKET)\b", desc.upper()))


def _latest_balances(store) -> dict:
    best: dict = {}
    for r in store.records(S.BALANCES):
        d, aid = _d(r.get("Date")), r.get("Account ID", "")
        if d and aid and (aid not in best or d >= best[aid][0]):
            best[aid] = (d, _f(r.get("Balance")))
    return {aid: v for aid, (d, v) in best.items()}


def build_performance(store, led: Ledger) -> list[list]:
    """Month-level: value change minus identified contributions, investable accounts only."""
    hdr = ["Month", "Start Value", "End Value", "Net Contributions", "Market Change", "Market Change %", "Note"]
    inv = {a for a, b in led.acct_bucket.items() if b in ("Taxable", "Retirement")}
    snaps = defaultdict(dict)
    for r in store.records(S.BALANCES):
        d, aid = _d(r.get("Date")), r.get("Account ID", "")
        if d and aid in inv:
            snaps[d][aid] = _f(r.get("Balance"))
    dates = sorted(snaps)
    if len(dates) < 2:
        return [hdr]
    running, values = {}, []
    for d in dates:
        running.update(snaps[d])
        values.append((d, sum(running.values())))
    flows, vests = Counter(), set()
    for t in led.txns:
        if t.get("Account ID") not in inv:
            continue
        m = _ym(t["_date"])
        if t["_cat"] in ("Investment Contribution", "Transfer"):
            flows[m] += t["_amt"]
        if "RSU" in str(t.get("Description", "")).upper() and "BOUGHT" in str(t.get("Description", "")).upper():
            vests.add(m)
    out = [hdr]
    months = sorted({_ym(d) for d, _ in values})
    for m in months:
        in_m = [(d, v) for d, v in values if _ym(d) == m]
        before = [(d, v) for d, v in values if _ym(d) < m]
        start = before[-1][1] if before else in_m[0][1]
        end = in_m[-1][1]
        if not before and len(in_m) < 2:
            continue
        change = end - start - flows[m]
        out.append([m, _r(start), _r(end), _r(flows[m]), _r(change), round(change / start, 4) if start else "",
                    "includes an RSU vest (shares arrive without a cash line)" if m in vests else ""])
    return out


def current_accounts(store, today: date) -> list[dict]:
    """Every account's latest balance with its bucket: synced accounts plus Manual Accounts (as of today)."""
    out = []
    for a in store.records(S.ACCOUNTS):
        name = str(a.get("Account", "")).strip()
        if not name:
            continue
        bal = _f(a.get("Balance"))
        avail = parse_amount(a.get("Available"))
        out.append({"name": name, "institution": str(a.get("Institution", "")), "balance": bal,
                    "available": float(avail) if avail is not None else None,
                    "date": str(a.get("Balance Date", "")).strip(),
                    "bucket": classify_account(name, str(a.get("Institution", "")), bal, str(a.get("Type", "")))})
    newest: dict = {}
    for m in manual_accounts(store):
        if m["as_of"] <= today and (m["name"] not in newest or m["as_of"] >= newest[m["name"]]["as_of"]):
            newest[m["name"]] = m
    for m in newest.values():
        out.append({"name": m["name"], "institution": "manual", "balance": m["balance"], "available": None,
                    "date": m["as_of"].isoformat(), "bucket": m["bucket"]})
    return out


def build_account_balances(accounts: list[dict]) -> list[list]:
    order = {b: i for i, b in enumerate(BUCKETS)}
    rows = sorted(accounts, key=lambda a: (order.get(a["bucket"], 99), -abs(a["balance"])))
    return [BAL_HEADERS] + [[a["name"], a["institution"], a["bucket"], _r(a["balance"]),
                             _r(a["available"]) if a["available"] is not None else "", a["date"]] for a in rows]


def _last_weekday(y: int, m: int) -> date:
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    d = date.fromordinal(nxt.toordinal() - 1)
    while d.weekday() >= 5:
        d = date.fromordinal(d.toordinal() - 1)
    return d


def _next_paycheck(last: date) -> date:
    """Monthly pay. Paid near month-end -> last weekday of next month; otherwise the same day next month,
    moved back to Friday if it lands on a weekend."""
    y, m = (last.year + 1, 1) if last.month == 12 else (last.year, last.month + 1)
    if last.day >= 25:
        return _last_weekday(y, m)
    month_end = date.fromordinal(date(y + (m == 12), m % 12 + 1, 1).toordinal() - 1)
    d = date(y, m, min(last.day, month_end.day))
    while d.weekday() >= 5:
        d = date.fromordinal(d.toordinal() - 1)
    return d


def build_cash_position(accounts: list[dict], led: Ledger, cfg: SummaryConfig, tabs: dict) -> list[list]:
    """One row: what is in cash right now, what is already owed, and what the rest of this month needs.

    Spendable cash   cash accounts at the lower of balance and available (pending debits count)
    Card balances    what the cards owe now; paid in full each month, so it is already spoken for
    Baseline         average run-rate spending (no one-offs) over the last <=3 complete months
    Expected rest    baseline minus this month's run-rate spending so far (never below 0)
    Bills still due  fixed categories at least $1 below their 3-month average this month (part of expected rest)
    Free cash        spendable - card balances - expected rest. The next paycheck is NOT counted:
                     it funds next month.
    """
    this_month = _ym(led.today)
    hdr, *mrows = tabs[MONTHLY]
    mon = [dict(zip(hdr, r)) for r in mrows]
    complete = [r for r in mon if not r["Partial"] and r["Month"] < this_month][-3:]

    def avg(k):
        return sum(float(r[k] or 0) for r in complete) / len(complete) if complete else 0.0

    baseline = avg("Run-rate Spending")
    cur = next((r for r in mon if r["Month"] == this_month), None)
    spent = float(cur["Run-rate Spending"] or 0) if cur else 0.0
    rest = max(0.0, baseline - spent)

    dhdr, *drows = tabs[DETAIL]
    bills = 0.0
    for r in (dict(zip(dhdr, x)) for x in drows):
        if r["Month"] == this_month and r["Fixed"] == "yes" and r["Prior 3-Mo Avg"] != "":
            gap = float(r["Prior 3-Mo Avg"]) - float(r["Amount"] or 0)
            if gap >= 1:  # ignore cents of drift on bills already paid
                bills += gap

    cash = [a for a in accounts if a["bucket"] == "Cash"]
    cash_bal = sum(a["balance"] for a in cash)
    cash_avail = sum(a["available"] if a["available"] else a["balance"] for a in cash)
    spendable = sum(min(a["balance"], a["available"]) if a["available"] else a["balance"] for a in cash)
    cards = -sum(a["balance"] for a in accounts if a["bucket"] == "Credit Card")
    taxable = sum(a["balance"] for a in accounts if a["bucket"] == "Taxable")
    free = spendable - cards - rest

    salary = set(cfg.salary_categories)
    pays = sorted((t for t in led.txns if t["_cat"] in salary and t["_amt"] > 0), key=lambda t: t["_date"])
    last = pays[-1] if pays else None
    avg_sal = avg("Salary")
    return [CASH_HEADERS, [
        led.today.isoformat(), this_month, _r(cash_bal), _r(cash_avail), _r(spendable), _r(cards), _r(spent),
        _r(baseline), len(complete), _r(rest), _r(bills), _r(free), _r(avg_sal), _r(baseline - avg_sal),
        _r(avg("Equity & Bonus")), last["_date"].isoformat() if last else "", _r(last["_amt"]) if last else "",
        _next_paycheck(last["_date"]).isoformat() if last else "", _r(taxable),
    ]]


def write_summaries(store, cfg: SummaryConfig, today: date | None = None) -> dict[str, int]:
    led = load_ledger(store, today)
    tabs = build(led, cfg)
    tabs[NETWORTH] = build_net_worth(store, led, cfg)
    tabs[ALLOC] = build_allocation(store, led)
    tabs[PERF] = build_performance(store, led)
    accounts = current_accounts(store, led.today)
    tabs[CASH] = build_cash_position(accounts, led, cfg, tabs)
    tabs[BAL] = build_account_balances(accounts)
    for name in SUMMARY_TABS:
        rows = tabs[name]
        pct = [i for i, h in enumerate(rows[0]) if h in PERCENT_COLS]
        store.replace_tab(name, rows, percent_cols=pct)
    return {k: len(v) - 1 for k, v in tabs.items()}
