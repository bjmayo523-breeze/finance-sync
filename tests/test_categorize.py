from datetime import date
from decimal import Decimal

from finance_sync import paths
from finance_sync.categorize import RuleSet, parse_amount
from finance_sync.models import Transaction
from finance_sync.sheets import rows_from_csv


def txn(desc, amount, payee=""):
    return Transaction("simplefin", "A", desc, date(2026, 10, 1), Decimal(amount), desc, payee)


def seed_rules():
    cats = {r["Category"] for r in rows_from_csv(paths.SEED_CATEGORIES)}
    rs = RuleSet.from_records(rows_from_csv(paths.SEED_RULES), cats)
    assert rs.warnings == [], rs.warnings  # every seed rule points at a seed category
    return rs


def cat(rs, desc, amount, payee="", account=""):
    hit = rs.categorize(txn(desc, amount, payee), account)
    return hit[0] if hit else None


def test_seed_rules():
    rs = seed_rules()
    assert cat(rs, "H-E-B #123 SPRINGFIELD", "-84.12") == "Groceries"
    assert cat(rs, "HEB PLUS 456", "-20") == "Groceries"
    assert cat(rs, "ACME CORP PAYROLL", "3150") == "Paycheck"
    assert cat(rs, "UBER EATS 8005928996", "-41.75") == "Restaurants"
    assert cat(rs, "UBER TRIP HELP.UBER.COM", "-19.20") == "Rideshare"
    assert cat(rs, "PAYMENT - THANK YOU", "900") == "Credit Card Payment"
    assert cat(rs, "NETFLIX.COM", "-15.99") == "Subscriptions"
    assert cat(rs, "DIVIDEND RECEIVED VTI", "45.10") == "Dividends"
    assert cat(rs, "YOU BOUGHT VXUS", "-1000") == "Brokerage Trade"
    assert cat(rs, "ONLINE TRANSFER TO SAVINGS", "-500") == "Transfer"
    assert cat(rs, "AMAZON PRIME*AB12", "-14.99") == "Subscriptions"
    assert cat(rs, "AMAZON MKTPL*XY", "-30") == "Shopping"
    assert cat(rs, "COSTCO GAS #1", "-50") == "Fuel"
    assert cat(rs, "COSTCO WHSE #1", "-150") == "Groceries"
    assert cat(rs, "TXTAG TOLL", "-4") == "Parking & Tolls"
    assert cat(rs, "SOME LOCAL SHOP", "-62") is None
    # "THEB" must not match the HEB rule
    assert cat(rs, "THEBOOKSTORE", "-10") is None


def test_amount_filters():
    rs = seed_rules()
    # payroll rule requires a positive amount
    assert cat(rs, "PAYROLL CORRECTION", "-10") is None
    # interest charge only when money out
    assert cat(rs, "INTEREST CHARGE ON PURCHASES", "-12") == "Interest Charged"


def test_priority_and_account_filter():
    recs = [
        {"_row": 2, "Priority": "50", "Pattern": "SHOP", "Category": "Shopping"},
        {"_row": 3, "Priority": "5", "Pattern": "SHOP", "Account Contains": "business", "Category": "Software"},
        {"_row": 4, "Priority": "", "Pattern": "re:[unclosed", "Category": "X"},
        {"_row": 5, "Priority": "1", "Pattern": "", "Category": "Ignored"},
    ]
    rs = RuleSet.from_records(recs)
    assert any("bad regex" in w for w in rs.warnings)
    assert rs.categorize(txn("SHOP", "-1"), "Personal Visa")[0] == "Shopping"
    assert rs.categorize(txn("SHOP", "-1"), "Business Card")[0] == "Software"
    assert rs.categorize(txn("SHOP", "-1"), "Business Card")[1] == "rule:row3"


def test_match_modes_and_fields():
    recs = [
        {"_row": 2, "Pattern": "acme", "Match": "exact", "Category": "A"},
        {"_row": 3, "Pattern": "zel", "Match": "starts", "Field": "payee", "Category": "B"},
        {"_row": 4, "Pattern": "memo-word", "Field": "any", "Category": "C"},
    ]
    rs = RuleSet.from_records(recs)
    assert rs.categorize(txn("ACME", "-1"))[0] == "A"
    assert rs.categorize(txn("ACME INC", "-1")) is None
    assert rs.categorize(txn("X", "-1", payee="Zelle Jane"))[0] == "B"
    t = txn("X", "-1")
    t.memo = "has memo-word"
    assert rs.categorize(t)[0] == "C"


def test_parse_amount():
    assert parse_amount("$1,234.50") == Decimal("1234.50")
    assert parse_amount("(12.00)") == Decimal("-12.00")
    assert parse_amount("") is None
    assert parse_amount("abc") is None
    assert parse_amount("'-20") == Decimal("-20")


def test_seed_rules_cover_common_cases():
    rs = seed_rules()
    assert cat(rs, "SYNCHRONY BANK PAYMENT", "-200") == "Credit Card Payment"  # beats the AMAZON rule
    assert cat(rs, "AMAZON MKTPL*ZK1", "-30") == "Shopping"
    assert cat(rs, "TST* CORNER BISTRO", "-43.51") == "Restaurants"
    assert cat(rs, "TST* STARBUCKS 1234", "-6.10") == "Coffee"  # coffee beats the Toast rule
    assert cat(rs, "CHICK-FIL-A #04330", "-21.46") == "Restaurants"
    assert cat(rs, "GOOGLE *Crunchyroll An", "-9.99") == "Subscriptions"
    assert cat(rs, "OPENAI *CHATGPT SUBSCR", "-21.28") == "Software"
    assert cat(rs, "INTEREST EARNED CASH", "0.64") == "Interest"
    assert cat(rs, "REINVESTMENT CASH", "-0.64") == "Brokerage Trade"
    assert cat(rs, "CAPITAL ONE CRCARDPMT 2611", "-500") == "Credit Card Payment"
    assert cat(rs, "CAPITAL ONE MOBILE PYMT", "277.02") == "Credit Card Payment"
    assert cat(rs, "GOOGLE *FI ABC123", "-65") == "Internet & Phone"
    assert cat(rs, "SHELL OIL 57444", "-48.20") == "Fuel"
    assert cat(rs, "QT 1234", "-62.10") == "Fuel"
    assert cat(rs, "DEPT EDUCATION STUDENT LN", "-150") == "Student Loan"
    assert cat(rs, "SPLITWISE INC PAYMENT", "-21.16") == "Shared Expenses"
    assert cat(rs, "PAYMENT", "1200", account="MORTGAGE LOAN (0000)") == "Transfer"  # loan side of a mortgage
    assert cat(rs, "GOLFNOW 123", "-60") == "Golf"
    assert cat(rs, "Ecommerce", "-54.50") is None  # vague lines stay for review


def test_merchant_key_groups_store_numbers():
    from finance_sync.categorize import merchant_key

    assert merchant_key("CHICK-FIL-A #04330") == merchant_key("CHICK-FIL-A #01112") == "CHICK-FIL-A"
    assert merchant_key("TEXACO 0379467") == "TEXACO"


def test_amount_limits_split_one_merchant():
    """The pattern for 'small purchases at a gas station are snacks': two rules, one with limits."""
    recs = [
        {"_row": 2, "Priority": "48", "Pattern": "re:SHELL|TEXACO", "Match": "regex", "Min Amount": "-29.99",
         "Max Amount": "-0.01", "Category": "Snacks & Drinks"},
        {"_row": 3, "Priority": "50", "Pattern": "re:SHELL|TEXACO", "Match": "regex", "Category": "Fuel"},
    ]
    rs = RuleSet.from_records(recs)
    assert rs.categorize(txn("TEXACO 0379467", "-15.00"))[0] == "Snacks & Drinks"
    assert rs.categorize(txn("SHELL OIL 57444", "-30.00"))[0] == "Fuel"
    assert rs.categorize(txn("TEXACO 0379467", "5.00"))[0] == "Fuel"  # refunds aren't snacks
