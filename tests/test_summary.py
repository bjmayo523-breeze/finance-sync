from datetime import date

from fake_store import FakeStore

from finance_sync import schema as S
from finance_sync.summary import (MONTHLY_HEADERS, SummaryConfig, classify_account, underlying, write_summaries)
from finance_sync.sync import init_sheet

CHK, SAP, IND, RSU, UTMA, K401, MORT = "C", "S", "I", "R", "U", "K", "M"


def test_classify_account():
    assert classify_account("TOTAL CHECKING (1111)", "Chase Bank", 100) == "Cash"
    assert classify_account("Chase Rewards Card (2222)", "Chase Bank", -5000) == "Credit Card"
    assert classify_account("Rewards Visa (3333)", "Capital One", -352) == "Credit Card"
    assert classify_account("MORTGAGE LOAN (4444)", "Chase Bank", -300000) == "Loan"
    assert classify_account("EMPLOYER RSU (6666)", "Fidelity Investments", 50000) == "Unvested"
    assert classify_account("Uniform Transfers to Minors (UTMA) (7777)", "Fidelity", 20000) == "Custodial"
    assert classify_account("EMPLOYER 401(K) PLAN (8888)", "Fidelity", 9534) == "Retirement"
    assert classify_account("Health Savings Account (9999)", "Fidelity", 2521) == "Retirement"
    assert classify_account("Individual - TOD (5555)", "Fidelity Investments", 60000) == "Taxable"
    assert classify_account("Joint Tenant ...297 (297)", "Charles Schwab US", 3326) == "Taxable"
    assert classify_account("Anything", "Bank", 5, user_type="Retirement") == "Retirement"  # user override wins


def test_underlying():
    assert underlying("NVDA270617C240", "CALL (NVDA) NVIDIA CORPORATION JUN 17 27 $240 (100 SHS)") == "NVDA"
    assert underlying("", "CALL (MSFT) ...") == "MSFT"
    assert underlying("VTI", "Vanguard") == "VTI"


def make_store():
    s = FakeStore()
    init_sheet(s)
    accts = [(CHK, "TOTAL CHECKING (1111)", 10500.25), (SAP, "Chase Rewards Card (2222)", -4200.40),
             (IND, "Individual - TOD (5555)", 60000.50), (RSU, "EMPLOYER RSU (6666)", 50000.00),
             (UTMA, "Uniform Transfers to Minors (UTMA) (7777)", 20000.10), (K401, "EMPLOYER 401(K) PLAN (8888)", 9000.30),
             (MORT, "MORTGAGE LOAN (4444)", -300000.75)]
    s.append(S.ACCOUNTS, [{"Account ID": a, "Account": n, "Institution": "Fidelity Investments" if a in "IRUK" else "Chase",
                           "Balance": b} for a, n, b in accts])
    s.append(S.BALANCES, [{"Date": "2026-10-23", "Account ID": a, "Balance": b, "Key": f"x{a}"} for a, _, b in accts])
    T = []

    def t(d, desc, amt, cat, acct):
        T.append({"Date": d, "Description": desc, "Amount": amt, "Category": cat, "Account ID": acct,
                  "Transaction ID": f"t{len(T)}"})
    # July (partial: starts 7/25)
    t("2026-07-25", "COFFEE", -5, "Coffee", SAP)
    t("2026-07-31", "ACME CORP PAYROLL PPD ID: 9000000003", 6000.01, "Paycheck", CHK)
    # August
    t("2026-08-03", "JPMORGAN CHASE CHASE ACH PPD ID: 1", -2000.13, "Mortgage/Rent", CHK)
    t("2026-08-15", "Payment to Chase card", -3000, "Credit Card Payment", CHK)  # transfer: excluded
    t("2026-08-15", "Payment Thank You", 3000, "Credit Card Payment", SAP)
    t("2026-08-16", "DD *DOORDASH", -33.57, "Work Meals", SAP)  # transfer type: excluded
    t("2026-08-17", "Acme.com Servi PAYMENTS TLR1 CCD", 33.57, "Reimbursement", CHK)  # excluded
    t("2026-08-20", "Acme.com Servi PAYMENTS PPD ID: 9000000099", 1200.14, "Bonus", CHK)
    t("2026-08-18", "ACME CORP PAYROLL PPD ID: 9000000001", 104.94, "RSU Income", CHK)
    t("2026-08-27", "IKEA SPRINGFIELD", -1370.92, "Shopping", SAP)
    t("2026-08-28", "TST* CORNER BISTRO", -43.51, "Restaurants", SAP)
    t("2026-08-29", "ACME CORP PAYROLL PPD ID: 9000000003", 6000.02, "Paycheck", CHK)
    t("2026-08-30", "DIVIDEND RECEIVED", 45.10, "Dividends", IND)  # investment account: not cash flow
    t("2026-08-31", "REFUND IKEA", 100, "Shopping", SAP)
    # September
    t("2026-09-02", "JPMORGAN CHASE CHASE ACH PPD ID: 1", -2000.13, "Mortgage/Rent", CHK)
    t("2026-09-05", "TST* CORNER BISTRO", -60, "Restaurants", SAP)
    t("2026-09-06", "IKEA SPRINGFIELD", -400, "Shopping", SAP)  # IKEA seen last month -> not a one-off
    t("2026-09-30", "ACME CORP PAYROLL PPD ID: 9000000003", 6000.01, "Paycheck", CHK)
    s.append(S.TRANSACTIONS, T)
    s.append(S.HOLDINGS, [
        {"Snapshot Date": "2026-10-23", "Account ID": IND, "Symbol": "NVDA", "Description": "NVIDIA", "Market Value": 20000.00},
        {"Snapshot Date": "2026-10-23", "Account ID": IND, "Symbol": "NVDA270617C240",
         "Description": "CALL (NVDA) NVIDIA CORPORATION JUN 17 27 $240", "Market Value": 1800},
        {"Snapshot Date": "2026-10-23", "Account ID": IND, "Symbol": "VTI", "Description": "Vanguard Total Stock Market Index Fund",
         "Market Value": 15000.00},
        {"Snapshot Date": "2026-10-23", "Account ID": UTMA, "Symbol": "VOO", "Description": "Vanguard 500", "Market Value": 17000.00},
    ])
    return s


def row(s, tab, key):
    hdr = s.tabs[tab][0]
    for r in s.tabs[tab][1:]:
        if r[0] == key:
            return dict(zip(hdr, r))
    raise KeyError(key)


def test_monthly_summary_math():
    s = make_store()
    write_summaries(s, SummaryConfig(), today=date(2026, 10, 23))
    assert s.tabs["Monthly Summary"][0] == MONTHLY_HEADERS
    jul, aug, sep = (row(s, "Monthly Summary", m) for m in ("2026-07", "2026-08", "2026-09"))
    assert jul["Partial"] == "yes" and aug["Partial"] == "" and sep["Partial"] == ""
    assert aug["Salary"] == 6000.02 and aug["Equity & Bonus"] == 1305.08 and aug["Other Income"] == 0
    # spending excludes transfers, work meals and reimbursements; refunds net
    assert aug["Spending"] == round(2000.13 + 1370.92 + 43.51 - 100, 2)
    assert aug["Fixed"] == 2000.13 and aug["Flexible"] == round(1370.92 + 43.51 - 100, 2)
    assert aug["One-offs"] == 1370.92 and aug["Run-rate Spending"] == round(aug["Spending"] - 1370.92, 2)
    assert aug["Net Cash Flow"] == round(6000.02 + 1305.08 - aug["Spending"], 2)
    assert abs(aug["Salary Savings Rate"] - (6000.02 - aug["Spending"]) / 6000.02) < 1e-4
    assert sep["One-offs"] == 0  # IKEA already seen in August
    assert jul["One-offs"] == 0  # first month: nothing to compare against


def test_detail_trends_merchants_networth_allocation():
    s = make_store()
    write_summaries(s, SummaryConfig(home_value=500000), today=date(2026, 10, 23))
    det = [dict(zip(s.tabs["Month Detail"][0], r)) for r in s.tabs["Month Detail"][1:] if r[0] == "2026-09"]
    rest = next(d for d in det if d["Category"] == "Restaurants")
    assert rest["Amount"] == 60 and rest["Prior 3-Mo Avg"] == 43.51  # only complete months (Aug) count
    assert s.tabs["Category Trends"][0][1] != "Mortgage/Rent"  # flexible categories first
    assert s.tabs["Category Trends"][0][-1] == "Mortgage/Rent"
    merch = [r for r in s.tabs["Top Merchants"][1:] if r[0] == "2026-08"]
    assert merch[0][1] == "JPMORGAN CHASE CHASE ACH" and merch[0][3] == "yes"
    nw = dict(zip(s.tabs["Net Worth"][0], s.tabs["Net Worth"][1]))
    assert nw["Unvested Equity"] == 50000.00 and nw["Custodial"] == 20000.10
    assert nw["Net Worth"] == round(10500.25 + 60000.50 + 9000.30 + 500000 - 4200.40 - 300000.75, 2)
    assert nw["Liquid Net Worth"] == round(10500.25 + 60000.50 - 4200.40, 2)
    alloc = [dict(zip(s.tabs["Allocation"][0], r)) for r in s.tabs["Allocation"][1:]]
    assert alloc[0]["Holding"] == "NVDA" and alloc[0]["Market Value"] == 21800.00
    assert "single-stock concentration" in alloc[0]["Note"] and "includes options" in alloc[0]["Note"]
    assert all(a["Holding"] != "VOO" for a in alloc)  # custodial excluded
    vti = next(a for a in alloc if a["Holding"] == "VTI")
    assert "concentration" not in vti["Note"]  # funds are not single stocks


def test_sync_writes_summaries(tmp_path, payload):
    from test_sync import NOW, FixtureProvider

    from finance_sync.config import Config
    from finance_sync.state import State
    from finance_sync.sync import Categorizer, run_sync

    s = FakeStore()
    init_sheet(s)
    run_sync(Config(), FixtureProvider(payload), s, Categorizer.from_store(s), State(tmp_path / "s.json"), now=NOW)
    assert "Monthly Summary" in s.tabs and len(s.tabs["Monthly Summary"]) >= 2


def test_manual_accounts_feed_net_worth_and_allocation():
    s = make_store()
    s.append(S.MANUAL, [{"Account": "Betterment", "Institution": "Betterment", "Bucket": "Taxable",
                         "Balance": "$12,345.67", "As Of": "2026-10-20"},
                        {"Account": "Betterment", "Institution": "Betterment", "Bucket": "Taxable",
                         "Balance": "12000", "As Of": "2026-09-01"},
                        {"Account": "Future entry", "Bucket": "Taxable", "Balance": "999", "As Of": "2026-12-01"},
                        {"Account": "No balance yet", "Bucket": "Taxable", "Balance": "", "As Of": ""}])
    write_summaries(s, SummaryConfig(), today=date(2026, 10, 23))
    nw = dict(zip(s.tabs["Net Worth"][0], s.tabs["Net Worth"][1]))
    assert nw["Taxable"] == round(60000.50 + 12345.67, 2)  # newest Betterment entry; future one ignored
    alloc = {r[0]: dict(zip(s.tabs["Allocation"][0], r)) for r in s.tabs["Allocation"][1:]}
    assert alloc["Betterment"]["Market Value"] == 12345.67 and "manual balance" in alloc["Betterment"]["Note"]
    assert "concentration" not in alloc["Betterment"]["Note"]


def test_cash_position_and_account_balances():
    from finance_sync.summary import BAL, CASH, CASH_HEADERS

    s = make_store()
    hdr = s.tabs[S.ACCOUNTS][0]
    chk = next(r for r in s.tabs[S.ACCOUNTS][1:] if r[0] == CHK)
    chk[hdr.index("Available")] = 10200.15  # pending debits: spendable is the lower figure
    s.append(S.TRANSACTIONS, [{"Date": "2026-10-05", "Description": "COFFEE", "Amount": -20, "Category": "Coffee",
                               "Account ID": SAP, "Transaction ID": "oct1"}])
    s.append(S.MANUAL, [{"Account": "Ally Savings", "Bucket": "Cash", "Balance": "1000", "As Of": "2026-10-01"},
                        {"Account": "Later", "Bucket": "Cash", "Balance": "5", "As Of": "2026-12-01"}])
    write_summaries(s, SummaryConfig(), today=date(2026, 10, 23))
    assert s.tabs[CASH][0] == CASH_HEADERS and len(s.tabs[CASH]) == 2
    c = dict(zip(CASH_HEADERS, s.tabs[CASH][1]))
    aug_rr = round(2000.13 + 43.51 - 100, 2)  # IKEA one-off left out
    sep_rr = round(2000.13 + 60 + 400, 2)
    assert c["Month"] == "2026-10" and c["Baseline Months"] == 2  # July is partial
    assert abs(c["Baseline Monthly Spending"] - (aug_rr + sep_rr) / 2) < 0.01
    assert c["Spent So Far"] == 20
    assert abs(c["Expected Rest of Month"] - ((aug_rr + sep_rr) / 2 - 20)) < 0.01
    assert c["Bills Still Due"] == 2000.13  # mortgage not paid yet this month
    assert c["Cash"] == round(10500.25 + 1000, 2) and c["Spendable Cash"] == round(10200.15 + 1000, 2)
    assert c["Card Balances"] == 4200.40
    assert abs(c["Free Cash After Month"] - (c["Spendable Cash"] - 4200.40 - c["Expected Rest of Month"])) < 0.01
    assert c["Last Paycheck Date"] == "2026-09-30" and c["Last Paycheck"] == 6000.01
    assert c["Next Paycheck (est.)"] == "2026-10-30"  # Oct 31 2026 is a Saturday
    assert abs(c["Salary Gap"] - (c["Baseline Monthly Spending"] - c["Avg Salary"])) < 0.01
    assert c["Taxable Investments"] == 60000.50
    bal = [dict(zip(s.tabs[BAL][0], r)) for r in s.tabs[BAL][1:]]
    assert [b["Bucket"] for b in bal][:3] == ["Cash", "Cash", "Credit Card"]
    assert all(b["Account"] != "Later" for b in bal)
    assert "Account ID" not in s.tabs[BAL][0]


def test_next_paycheck_estimate():
    from finance_sync.summary import _next_paycheck

    assert _next_paycheck(date(2026, 8, 29)) == date(2026, 9, 30)
    assert _next_paycheck(date(2026, 12, 31)) == date(2027, 1, 29)
    assert _next_paycheck(date(2026, 1, 15)) == date(2026, 2, 13)  # Feb 15 2026 is a Sunday
    assert _next_paycheck(date(2026, 1, 20)) == date(2026, 2, 20)
