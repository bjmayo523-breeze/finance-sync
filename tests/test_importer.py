from datetime import date
from decimal import Decimal

import pytest
from fake_store import FakeStore

from finance_sync import schema as S
from finance_sync.importer import (ImportErrorCSV, detect_format, first_simplefin_date, import_rows, parse_csv,
                                   resolve_account)
from finance_sync.sync import Categorizer, init_sheet

CHASE_CHECKING = """Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #
DEBIT,06/28/2026,"JPMORGAN CHASE CHASE ACH PPD ID: 1000008113",-2000.13,ACH_DEBIT,9000.00,,
CREDIT,06/30/2026,"ACME CORP PAYROLL PPD ID: 9000000003",6000.01,ACH_CREDIT,15000.01,,
DEBIT,06/15/2026,"CORNER STORE SPRINGFIELD 06/15",-20.00,DEBIT_CARD,,,
DEBIT,06/15/2026,"CORNER STORE SPRINGFIELD 06/15",-20.00,DEBIT_CARD,,,
DEBIT,07/26/2026,"OVERLAPS SIMPLEFIN",-5.00,DEBIT_CARD,,,
"""
CHASE_CARD = """Transaction Date,Post Date,Description,Category,Type,Amount,Memo
05/02/2026,05/03/2026,H-E-B #591,Groceries,Sale,-84.12,
05/04/2026,05/05/2026,Payment Thank You-Mobile,,Payment,900.00,
"""
CAPONE = """Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit
2026-05-02,2026-05-03,3333,TEXACO 0379467,Gas/Automotive,15.00,
2026-05-10,2026-05-10,3333,CAPITAL ONE MOBILE PYMT,Payment/Credit,,277.02
"""


def test_detect_and_parse_formats():
    fmt, rows = parse_csv(CHASE_CHECKING)
    assert fmt == "chase-checking" and len(rows) == 5
    assert rows[0].amount == Decimal("-2000.13") and rows[0].date == date(2026, 6, 28)
    fmt, rows = parse_csv(CHASE_CARD)
    assert fmt == "chase-card" and rows[0].amount == Decimal("-84.12") and rows[1].amount == Decimal("900.00")
    fmt, rows = parse_csv(CAPONE)
    assert fmt == "capital-one" and rows[0].amount == Decimal("-15.00") and rows[1].amount == Decimal("277.02")
    fmt, rows = parse_csv("Date,Payee,Amount\n2026-01-02,Coffee shop,-4.50\n")
    assert fmt == "generic" and rows[0].description == "Coffee shop"
    _, rows = parse_csv(CHASE_CARD, flip_sign=True)
    assert rows[0].amount == Decimal("84.12")
    with pytest.raises(ImportErrorCSV):
        detect_format(["foo", "bar"])


def test_title_lines_above_header_are_skipped():
    fmt, rows = parse_csv("Account Activity Export\nGenerated 2026\n" + CHASE_CARD)
    assert fmt == "chase-card" and len(rows) == 2


@pytest.fixture
def store():
    s = FakeStore()
    init_sheet(s)
    s.append(S.ACCOUNTS, [{"Account ID": "A1", "Account": "TOTAL CHECKING (1111)", "Institution": "Chase", "Balance": 100},
                          {"Account ID": "A2", "Account": "Chase Rewards Card (2222)", "Institution": "Chase",
                           "Balance": -500}])
    s.append(S.TRANSACTIONS, [{"Date": "2026-07-26", "Description": "x", "Amount": -1, "Account ID": "A1",
                               "Transaction ID": "si_abc", "Category": "Shopping"}])
    return s


def test_resolve_account(store):
    assert resolve_account(store, "checking")["Account ID"] == "A1"
    with pytest.raises(ImportErrorCSV):
        resolve_account(store, "e")  # matches both accounts -> refuse
    with pytest.raises(ImportErrorCSV):
        resolve_account(store, "discover")  # matches none -> refuse


def test_import_cutoff_dedupe_and_rules(store):
    acct = resolve_account(store, "checking")
    cutoff = first_simplefin_date(store, "A1")
    assert cutoff == date(2026, 7, 26)
    _, rows = parse_csv(CHASE_CHECKING)
    rep = import_rows(store, Categorizer.from_store(store), acct, rows, cutoff, "now")
    assert rep.new == 4 and rep.after_cutoff == 1
    recs = {r["Description"]: r for r in store.records(S.TRANSACTIONS)}
    assert recs["ACME CORP PAYROLL PPD ID: 9000000003"]["Category"] == "Paycheck"  # rules apply on import
    assert recs["JPMORGAN CHASE CHASE ACH PPD ID: 1000008113"]["Category"] == "Uncategorized"
    same = [r for r in store.records(S.TRANSACTIONS) if r["Description"] == "CORNER STORE SPRINGFIELD 06/15"]
    assert len(same) == 2  # two identical same-day charges both kept
    rep2 = import_rows(store, Categorizer.from_store(store), acct, rows, cutoff, "now")
    assert rep2.new == 0 and rep2.duplicates == 4  # idempotent


def test_flip_warning_on_card(store):
    acct = resolve_account(store, "rewards")
    _, rows = parse_csv(CHASE_CARD.replace("-84.12", "84.12") + "05/06/2026,05/06/2026,X,,Sale,5.00,\n")
    rep = import_rows(store, Categorizer.from_store(store), acct, rows, date(2026, 7, 1), "now", dry_run=True)
    assert any("flip-sign" in w for w in rep.warnings)
