# Finance Sync

A stripped-down, self-hosted Tiller: pulls **read-only** bank, credit card and
brokerage data from SimpleFIN once a day, categorizes it with your own rules
(plus an optional local LLM), and appends it to a Google Sheet you own. Any AI
assistant that can read Google Sheets (Claude, ChatGPT) can then analyze it.

Runs on **Windows, macOS and Linux**. **Start with `SETUP_CHECKLIST.md`.**

## Quick start

```bash
# 1. a control folder for your own projects, then the code inside it
mkdir -p ~/Projects && cd ~/Projects          # Windows PowerShell: mkdir $HOME\Projects; cd $HOME\Projects
git clone https://github.com/bjmayo523-breeze/finance-sync.git
cd finance-sync

# 2. install
bash scripts/install.sh                        # macOS / Linux
# powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1    # Windows
```

| | Windows | macOS | Linux |
|---|---|---|---|
| Install | `scripts\install.ps1` | `scripts/install.sh` | `scripts/install.sh` |
| Run a command | `.\scripts\finance-sync.ps1 <cmd>` | `./scripts/finance-sync.sh <cmd>` | `./scripts/finance-sync.sh <cmd>` |
| Daily schedule | `scripts\register-task.ps1` (Task Scheduler) | `scripts/register-launchd.sh` (launchd) | `scripts/register-systemd.sh` (systemd user timer) |
| Vault key stored in | Credential Manager | login Keychain | Secret Service (GNOME Keyring / KWallet) |
| Disk encryption | BitLocker / Device Encryption | FileVault | LUKS |

The examples below use the Windows form; on macOS/Linux swap
`.\scripts\finance-sync.ps1` for `./scripts/finance-sync.sh`.

To update later: `git pull`, then rerun the install script and
`init-sheet --update-rules`. Your `config/config.toml`, `data/` and `logs/` are
git-ignored, so a pull never touches them.

```
 Banks / cards / brokerage
          |  (your bank logins live here, at the aggregator)
   SimpleFIN Bridge  --- read-only protocol, no money movement ---
          |  HTTPS, access URL (encrypted on this laptop)
   Finance Sync (this folder; Task Scheduler / launchd / systemd, daily)
     - dedupe by transaction ID
     - Rules tab  ->  optional local Ollama  ->  Uncategorized
          |  Sheets API, service account (sees only the one shared sheet)
   Google Sheet  <--- your AI assistant reads it (Claude or ChatGPT connector)
```

## What it does / doesn't do

| Does | Doesn't |
|---|---|
| Transactions, balances, daily balance history | Move money (impossible: the protocol has no such endpoint) |
| Brokerage holdings snapshot *if* your institution provides it via SimpleFIN | Guarantee holdings for every brokerage |
| Rule-based categorization you edit in the sheet | Overwrite a category you set by hand |
| Append and update only | Delete rows, tabs, or your own columns |
| Skips pending transactions (they can change ID when they post) | Real-time updates (SimpleFIN refreshes about once a day) |

## Folder layout

```
finance-sync/
  SETUP_CHECKLIST.md        manual steps, ordered by importance
  README.md                 this file
  config/
    config.example.toml     template (install copies it to config.toml)
    config.toml             your settings - nothing secret (created by install)
    seed_categories.csv     starter categories (loaded into the sheet once)
    seed_rules.csv          starter rules (loaded into the sheet once)
  scripts/
    install.ps1 / install.sh            one-time setup (.venv + package + config)
    finance-sync.ps1 / finance-sync.sh  run any command
    register-task.ps1                   Windows: daily scheduled task (unregister-task.ps1 removes it)
    register-launchd.sh                 macOS: daily launchd agent (unregister-launchd.sh removes it)
    register-systemd.sh                 Linux: daily systemd user timer
  src/finance_sync/         the program
  tests/                    79 tests (fixtures only; no network)
  data/   (created)         secrets.vault (encrypted), state.json, sync.lock
  logs/   (created)         finance-sync.log (counts and errors, no transaction details)
```

## Commands

| Command | What it does |
|---|---|
| `setup-simplefin [--replace]` | Paste a one-time SimpleFIN token; stores the access credential encrypted |
| `setup-google KEYFILE [--keep]` | Imports the service-account key, prints its email, deletes the file |
| `set-sheet URL_OR_ID` | Points at your sheet |
| `init-sheet [--update-rules]` | Creates missing tabs/columns; seeds Categories and Rules if empty. `--update-rules` appends newer starter categories and rules without touching yours |
| `doctor [--online]` | Checks everything. `--online` opens the sheet and makes one SimpleFIN call |
| `sync [--dry-run] [--force]` | Pull and append. `--dry-run` writes nothing. `--force` ignores the 20h guard |
| `recategorize [--dry-run] [--include-auto]` | Applies current rules to blank/Uncategorized rows. `--include-auto` also re-runs rules on rows the sync categorized that you haven't changed |
| `reconcile [--dry-run]` | Matches reimbursement deposits to the card charges they repay (also runs after every sync) |
| `summarize` | Rebuild the monthly summary tabs (also runs after every sync) |
| `init-dashboard [--rebuild]` | Create the **Dashboard** tab (charts + month picker) in the Sheet |
| `import-csv FILE --account NAME [--dry-run]` | Import older history from a bank/card CSV export (see below) |
| `status` | Last sync time and which secrets are stored |
| `forget-secrets` | Deletes the vault and its key from this computer |

## Sheet tabs

| Tab | Owner | Notes |
|---|---|---|
| Transactions | sync appends; **you** may edit Category, Notes, or add columns | Amount: negative = money out. `Transaction ID` is the dedupe key; don't edit it. `Auto Category` is what the rules chose: if Category differs from it, that row is yours and is never recategorized |
| Accounts | sync updates columns A-H; **Type** and **Notes** are yours | Fill in Type (Checking/Credit Card/Brokerage...) |
| Balance History | sync appends one row per account per day | Net-worth-over-time source |
| Holdings | sync appends a daily snapshot when available | Empty if your brokerage doesn't expose positions |
| Categories | **you** | Category, Group, Type (Income / Expense / Transfer). Transfers are excluded from spending |
| Rules | **you** | See below |
| Sync Log | sync appends | Status OK / WARN / FAIL and warnings from SimpleFIN |
| Manual Accounts | **you** | Accounts SimpleFIN can't connect (e.g. a robo-advisor). One row per update: Account, Institution, Bucket (Taxable / Retirement / Cash / Loan...), Balance, As Of. Net worth and allocation use the newest row per account. Add a new row each month rather than overwriting, and the history stays intact |

Columns are matched by header name, so you can reorder them or insert your own.
Don't rename the built-in headers.

### Rules tab

Lowest **Priority** number wins (blank = 1000); on a tie, the rule nearer the top wins. Matching is case-insensitive.

| Column | Values |
|---|---|
| Pattern | text, or `re:<regex>` |
| Match | `contains` (default), `exact`, `starts`, `regex` |
| Field | `description` (default), `payee`, `any` |
| Account Contains | only apply to accounts whose name contains this |
| Min Amount / Max Amount | on the signed amount (e.g. Min `0.01` = deposits only) |
| Category | must exist in Categories |

Put specific rules at a lower priority number than broad ones (the seed rules
show this: `UBER EATS` at 40 beats `UBER` at 50).

## Importing older history (CSV)

SimpleFIN only reaches back ~90 days. Trends need 6-12 months, so pull older
history from each bank's CSV download once:

1. **Download** 12-24 months of transactions as **CSV** (not PDF, not QFX/OFX):
   - **Chase checking / Chase cards:** chase.com > the account > Download account activity > CSV > choose dates.
   - **Capital One:** capitalone.com > the card > View statements/transactions > Download > CSV.
   - Any other bank: any CSV with Date, Description and Amount (or Debit/Credit) columns works.
2. **Dry run first** (writes nothing; shows the format it detected, the date
   cutoff, how many rows it would add and how they categorize):
   ```powershell
   .\scripts\finance-sync.ps1 import-csv "$HOME\Downloads\Chase1234_Activity.CSV" --account "checking" --dry-run
   ```
   `--account` is any part of the account's name as shown in the **Accounts**
   tab ("checking", "rewards", "visa"). It must match exactly one account.
3. **Check the dry run:**
   - Purchases on a card must be **negative**. If they show positive, add `--flip-sign`.
     (Chase and Capital One exports are already correct; the tool warns if a
     card looks flipped.)
   - "Cutoff: before YYYY-MM-DD" means rows from that date on are skipped
     because SimpleFIN already has them. That's what prevents duplicates.
4. **Import:** run the same command without `--dry-run`. Then repeat for each
   account. Re-importing the same file adds nothing (each row has a stable ID).
5. Afterwards: matching re-runs on the new history and the summary tabs rebuild
   automatically. Check **Top uncategorized** with `sync --dry-run --force`, or
   filter Transactions by Uncategorized, and add Rules for older merchants.

Brokerage history CSVs (Fidelity, Schwab) aren't imported: they don't affect
spending, and their format varies too much. Investment history builds up from
the daily balance snapshots instead.

## Dashboards

Two views over the same numbers:

**1. The Dashboard tab in the Google Sheet** (for anyone the Sheet is shared with)
- Built once: `.\scripts\finance-sync.ps1 init-dashboard`. It's formulas and
  native charts over the summary tabs, so it updates itself after every sync.
- Pick a month in cell **B3**. The seven "This month" cards (each with its
  change from the month before, green or red by whether the change is good)
  and both tables follow it.
- **Cash right now** cards: spendable cash, cards owed, what's free after this
  month's remaining spending, and the next paycheck (estimated).
- **Spending by category** table: *Sort by* (amount, change vs average,
  3-month average, name, group), *Order*, and *Show* (all / flexible / fixed)
  dropdowns above it, plus a trend sparkline per category. Rises of 15% or
  more over the average are red; drops of 15% or more are green.
- **Top 10 flexible categories** table: the month's ten biggest non-fixed
  categories (where savings are possible), with *Sort by* and *Order*.
- **Trend sparklines** are green when the selected month is 5% or more below
  that category's 3-month average (spending less), red when 5% or more above,
  gray otherwise or when there's no history yet.
- **Hover notes**: hover any card label, column header or dropdown for what it
  means and how it's worked out. Charts show values on hover.
- Charts: income vs spending, run-rate spending vs salary, largest flexible
  categories over time, the selected month by category (follows the table's
  sort and filter), net worth, top holdings.
- To change the layout later: `init-dashboard --rebuild` (replaces only the
  Dashboard tab; any dropdown choices go back to their defaults).

**2. The Claude dashboard** (the richer view, with a mask toggle)
- Reads the summary tabs live from your Google Sheets connection when you open
  it. Your numbers are **not stored in the page**: someone who opens the link
  without access to this Sheet sees no real figures.
- **Investable cash** tab: spendable cash, less cards owed and spending still
  expected this month, less a safety reserve you pick (1, 2, 3 or 6 months of
  baseline spending), is what's available to invest. It also shows whether
  salary covers baseline spending on its own.
- **Mask amounts** toggle: covers every dollar amount on screen; shapes and
  percentages stay. For screen-sharing only: the numbers are still in the page
  for anyone viewing it with access to your Sheet.
- **Demo data** toggle: swaps in a synthetic household with the same shape, for
  showing the architecture to anyone. Share the link only with Demo on in mind:
  real numbers only appear for people who can open your Sheet.

### Summary tabs (generated; don't edit)

| Tab | What's in it |
|---|---|
| Monthly Summary | One row per month: salary, equity & bonus, other income, spending, fixed, flexible, one-offs, run-rate, net cash flow, savings rates. `Partial` = month not complete (first month of data, or the current month) |
| Month Detail | Every category for every month, with the prior 3-complete-month average and the change |
| Category Trends | Months x categories (flexible categories first) for trend charts |
| Top Merchants | Top 20 merchants per month, flagged fixed or flexible |
| One-offs | Big flexible charges from merchants not seen in the previous 2 months |
| Net Worth | Daily, by bucket: Cash, Taxable, Retirement, Home, Credit Cards, Loans, Net Worth, Liquid Net Worth, plus Unvested Equity and Custodial shown beside it (not in it) |
| Allocation | Latest holdings across taxable + retirement accounts, options rolled into their stock, single-stock concentration flagged |
| Investment Performance | Monthly value change minus contributions (fills in as daily snapshots accumulate) |
| Cash Position | One row, as of the last sync: spendable cash, card balances, run-rate spent so far this month, baseline monthly spending, expected rest of month, bills still due, free cash after this month, salary and equity averages, last/next paycheck, taxable investments |
| Account Balances | Every account's latest balance and available balance with its bucket (no account IDs) |

**Definitions**
- *Cash flow* counts only checking/savings and credit-card accounts. Dividends
  and trades inside investment accounts are not income or spending.
- *Salary* = Paycheck. *Equity & Bonus* = RSU Income + Bonus, kept separate so
  quarterly money doesn't hide the month-to-month picture.
- *Fixed* = mortgage, auto, student loan, insurance, utilities, phone
  (`[summary] fixed_categories` in config.toml). *Flexible* = everything else.
- *Run-rate* = spending minus one-offs. *Salary savings rate* = (salary - spending) / salary.
- *Account buckets* come from the account's name; to override one, type a bucket
  in the **Type** column of the Accounts tab: Cash, Credit Card, Loan, Taxable,
  Retirement, Unvested, or Custodial.
- *Baseline spending* = average run-rate spending over the last 3 complete
  months (fewer until there's more history). *Spendable cash* = cash accounts
  at the lower of balance and available (pending debits count). *Free cash
  after this month* = spendable cash - card balances - (baseline - run-rate
  spent so far this month). The next paycheck isn't counted: it funds next
  month. *Next paycheck (est.)*: paid near month-end -> last weekday of the
  next month.
- *Home value*: set `home_value` under `[summary]` in config.toml, or net worth
  shows the mortgage without the house.

## Reimbursements

Work meals go on the card, and the employer repays them into checking. Counted
naively, that inflates Restaurants *and* adds fake income. With
`[reimbursements]` enabled in `config.toml`:

- The deposit is categorized **Reimbursement**. The charges it repays become
  **Work Meals**. Both are Type=Transfer, so they're out of personal spending
  and income.
- After each sync, `reconcile` pairs each deposit with charges on the expense
  card that add up to it **exactly**:
  - It tries charges you tagged *Work Meals* first, from up to 60 days before
    the deposit and up to 6 charges per deposit.
  - Then it tries Restaurants/Coffee charges from the 14 days before the
    deposit, up to 2 charges.
  - Both sides get the same **Reimbursement Match** ID.
- Deposits it can't pair are listed in the Sync Log. It never guesses. To fix
  one, set the Category of the charges it repays to *Work Meals* and run
  `reconcile`.
- Charges tagged Work Meals that are still unpaid after 45 days get a warning.
  That's your "submit the expense report" reminder.

## Security model (honest version)

- **Credentials:** the SimpleFIN access URL and Google key are encrypted with
  Fernet in `data\secrets.vault`. The key to that vault is in **Windows
  Credential Manager**, unlocked by your Windows login. Copying or backing up
  this folder does not expose them.
- **Limit:** any program running as your Windows user could read both. That's
  true of every user-level secret store. Disk encryption, a strong sign-in, and
  not running random software are what defend it.
- **Google access:** `spreadsheets` scope only (no Drive), via a service account
  with no project roles. It can open **only** sheets you explicitly share with it.
- **Sheet safety:** merchant text that starts with `= + - @` is written as plain
  text, so a crafted merchant name can't become a formula.
- **Logs:** counts and errors only. URL credentials are redacted from every log line.
- **LLM:** off by default; refuses non-localhost endpoints unless `allow_remote`
  is set, and refuses public addresses even then. Answers outside your category
  list are discarded.
- **Weakest link:** your Google account and email (see checklist step 1).

## Moving to the homelab later

The same code runs on Linux. Copy the folder (secrets don't travel; that's by
design), `pip install -e .`, run the setup commands again with a **new**
SimpleFIN token and service-account key, and schedule with a systemd timer
(`Persistent=true`). Then delete the laptop's SimpleFIN connection and key.

## Adding Plaid later

`src/finance_sync/providers/` holds one provider. A Plaid provider needs the same
`fetch(start, end, include_pending) -> FetchResult` method, plus a small local
page to run Plaid Link once. Worth doing if SimpleFIN misses an institution or
your brokerage's holdings.

## Development

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest
```
