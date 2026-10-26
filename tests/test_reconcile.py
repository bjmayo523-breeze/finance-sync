from datetime import date

from fake_store import FakeStore

from finance_sync import schema as S
from finance_sync.config import ReimbursementConfig
from finance_sync.reconcile import MATCH, REIMB, WORK, reconcile
from finance_sync.sync import init_sheet

SAP, CHK = "Chase Rewards Card (2222)", "TOTAL CHECKING (1111)"
CFG = ReimbursementConfig(enabled=True, credit_pattern=r"ACME\.COM SERVI\w* PAYMENTS",
                          expense_account_contains="Rewards")


def store_with(rows):
    s = FakeStore()
    init_sheet(s)
    out = []
    for i, (d, desc, amt, acct, cat) in enumerate(rows):
        out.append({"Date": d, "Description": desc, "Amount": amt, "Account": acct, "Category": cat,
                    "Auto Category": cat, "Categorized By": "rule:row1", "Transaction ID": f"t{i}"})
    s.append(S.TRANSACTIONS, out)
    return s


def by_desc(s):
    return {r["Description"]: r for r in s.records(S.TRANSACTIONS)}


def test_single_and_pair_matches():
    s = store_with([
        ("2026-08-16", "DD *DOORDASH NOODLE HOUSE", -33.57, SAP, "Restaurants"),
        ("2026-08-17", "Acme.com Servi PAYMENTS A", 33.57, CHK, REIMB),
        ("2026-09-27", "DD *DOORDASH WINGSTOP", -149.99, SAP, "Restaurants"),
        ("2026-10-07", "DD *DOORDASH ICHIUMIJA", -318.73, SAP, "Restaurants"),
        ("2026-10-08", "Acme.com Servi PAYMENTS B", 468.72, CHK, REIMB),
        ("2026-10-01", "PERSONAL DINNER", -33.57, SAP, "Restaurants"),  # same amount, after deposit A
    ])
    rep = reconcile(s, CFG, today=date(2026, 10, 23))
    assert len(rep.matched) == 2 and not rep.unmatched
    r = by_desc(s)
    assert r["DD *DOORDASH NOODLE HOUSE"]["Category"] == WORK
    assert r["DD *DOORDASH NOODLE HOUSE"][MATCH] == r["Acme.com Servi PAYMENTS A"]["Transaction ID"]
    assert r["DD *DOORDASH WINGSTOP"][MATCH] == r["DD *DOORDASH ICHIUMIJA"][MATCH] == "t4"
    assert r["PERSONAL DINNER"]["Category"] == "Restaurants"  # charge after the deposit is never taken
    assert r["DD *DOORDASH WINGSTOP"]["Categorized By"].startswith("match:")
    # idempotent
    rep2 = reconcile(s, CFG, today=date(2026, 10, 23))
    assert rep2.matched == [] and rep2.unmatched == []


def test_no_guessing_outside_food_or_window_or_manual():
    s = store_with([
        ("2026-08-07", "H-E-B #591", -40.21, SAP, "Groceries"),
        ("2026-08-11", "LIVING SPACES", -4.33, SAP, "Shopping"),
        ("2026-08-31", "Acme.com Servi PAYMENTS C", 44.54, CHK, REIMB),
        ("2026-07-01", "OLD DINNER", -50.00, SAP, "Restaurants"),       # outside 14-day window
        ("2026-08-25", "Acme.com Servi PAYMENTS D", 50.00, CHK, REIMB),
        ("2026-09-01", "MY DINNER", -25.00, SAP, "Restaurants"),
        ("2026-09-02", "Acme.com Servi PAYMENTS E", 25.00, CHK, REIMB),
    ])
    # user set MY DINNER by hand to something else -> Category != Auto Category
    row = by_desc(s)["MY DINNER"]["_row"]
    s.update_cells(S.TRANSACTIONS, [(row, "Category", "Gifts & Donations")])
    rep = reconcile(s, CFG, today=date(2026, 9, 3))
    assert rep.matched == []
    assert {u.amount for u in rep.unmatched} == {44.54, 50, 25} or len(rep.unmatched) == 3
    assert len(rep.warnings()) == 3


def test_manual_tags_resolve_unmatched_and_stale_warning():
    s = store_with([
        ("2026-08-01", "HOTEL RESTAURANT", -900.00, SAP, "Restaurants"),
        ("2026-08-03", "AIRPORT DINER", -54.14, SAP, "Restaurants"),
        ("2026-08-05", "TEAM DINNER", -246.00, SAP, "Restaurants"),
        ("2026-08-20", "Acme.com Servi PAYMENTS F", 1200.14, CHK, REIMB),
        ("2026-06-01", "FORGOTTEN EXPENSE", -80.00, SAP, "Restaurants"),
    ])
    assert len(reconcile(s, CFG, dry_run=True, today=date(2026, 8, 21)).unmatched) == 1
    # user tags the three charges + an old one as Work Meals
    r = by_desc(s)
    s.update_cells(S.TRANSACTIONS, [(r[d]["_row"], "Category", WORK) for d in
                                    ("HOTEL RESTAURANT", "AIRPORT DINER", "TEAM DINNER", "FORGOTTEN EXPENSE")])
    rep = reconcile(s, CFG, today=date(2026, 8, 21))
    assert len(rep.matched) == 1 and rep.matched[0][2] == "tagged"
    assert by_desc(s)["TEAM DINNER"][MATCH] == "t3"
    assert len(rep.stale) == 1 and rep.stale[0].rec["Description"] == "FORGOTTEN EXPENSE"


def test_disabled_does_nothing():
    s = store_with([("2026-08-16", "X", -1, SAP, "Restaurants"), ("2026-08-17", "Acme.com Servi PAYMENTS", 1, CHK, REIMB)])
    assert reconcile(s, ReimbursementConfig(), today=date(2026, 9, 1)).matched == []


def test_tlr_pattern_ignores_bonus_deposit():
    cfg = ReimbursementConfig(enabled=True, credit_pattern=r"ACME\.COM SERVI\w* PAYMENTS TLR",
                              expense_account_contains="Rewards")
    s = store_with([
        ("2026-08-20", "Acme.com Servi PAYMENTS PPD ID: 9000000099", 1200.14, CHK, "Bonus"),
        ("2026-08-16", "DD *DOORDASH NOODLE HOUSE", -33.57, SAP, "Restaurants"),
        ("2026-08-17", "Acme.com Servi PAYMENTS TLR000000000001 CCD ID: 99", 33.57, CHK, REIMB),
    ])
    rep = reconcile(s, cfg, today=date(2026, 8, 21))
    assert len(rep.matched) == 1 and rep.unmatched == []
