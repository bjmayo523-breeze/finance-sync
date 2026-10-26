"""Sheet tabs and their columns.

Columns are matched by header *name*, so you can reorder columns or add your
own columns anywhere; the sync fills blanks for columns it doesn't know.
Don't rename the headers listed here.
"""

TRANSACTIONS = "Transactions"
ACCOUNTS = "Accounts"
BALANCES = "Balance History"
HOLDINGS = "Holdings"
CATEGORIES = "Categories"
RULES = "Rules"
SYNC_LOG = "Sync Log"
MANUAL = "Manual Accounts"

TXN_HEADERS = [
    "Date", "Description", "Amount", "Category", "Account", "Institution",
    "Payee", "Memo", "Categorized By", "Auto Category", "Reimbursement Match", "Transaction ID", "Account ID",
    "Imported At", "Notes",
]
ACCOUNT_HEADERS = [
    "Account ID", "Account", "Institution", "Currency", "Balance", "Available",
    "Balance Date", "Last Synced", "Type", "Notes",
]
# Columns the sync overwrites on Accounts; "Type" and "Notes" are yours.
ACCOUNT_MANAGED = ACCOUNT_HEADERS[:8]
BALANCE_HEADERS = ["Date", "Account", "Institution", "Balance", "Available", "Account ID", "Key"]
HOLDING_HEADERS = [
    "Snapshot Date", "Account", "Symbol", "Description", "Shares",
    "Market Value", "Cost Basis", "Currency", "Account ID", "Key",
]
CATEGORY_HEADERS = ["Category", "Group", "Type"]
RULE_HEADERS = [
    "Priority", "Pattern", "Match", "Field", "Account Contains",
    "Min Amount", "Max Amount", "Category", "Notes",
]
SYNC_LOG_HEADERS = [
    "Timestamp", "Status", "Window Start", "Window End", "Accounts",
    "New Transactions", "By Rules", "By LLM", "Uncategorized",
    "Balances Logged", "Holdings Logged", "Warnings",
]

# Accounts SimpleFIN can't reach. You type the balance; net worth and allocation include it.
MANUAL_HEADERS = ["Account", "Institution", "Bucket", "Balance", "As Of", "Notes"]

TABS: dict[str, list[str]] = {
    TRANSACTIONS: TXN_HEADERS,
    ACCOUNTS: ACCOUNT_HEADERS,
    BALANCES: BALANCE_HEADERS,
    HOLDINGS: HOLDING_HEADERS,
    CATEGORIES: CATEGORY_HEADERS,
    RULES: RULE_HEADERS,
    SYNC_LOG: SYNC_LOG_HEADERS,
    MANUAL: MANUAL_HEADERS,
}

UNCATEGORIZED = "Uncategorized"
