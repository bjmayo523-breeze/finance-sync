# Finance Sync - Setup Checklist

Ordered by importance. Steps 1-3 protect everything else, so do them first
even though they produce nothing visible. Rough total: 60-90 minutes.

---

## 1. Lock down your Google account  (most important)

**Why:** once this runs, one Google Sheet holds every transaction from every
account you own. Whoever controls your Google account reads all of it. Also,
SimpleFIN logs you in with an emailed link, so **your email account is your
SimpleFIN password too**.

Check at https://myaccount.google.com/security :
- [ ] 2-Step Verification is **on**, with a **passkey or hardware security key**
      as the main method. Remove SMS as a fallback if you can; it's the weakest one.
- [ ] Recovery email and phone are current and belong to you.
- [ ] "Your connections to third-party apps & services": remove anything you
      don't recognize.
- [ ] If the email you'll use for SimpleFIN isn't this Gmail, do the same for that account.

## 2. SimpleFIN Bridge account  (holds the link to all your banks)

Go to **https://bridge.simplefin.org** by typing it yourself. Don't follow ads or emailed links.

Before paying:
- [ ] Use the institution search to confirm **every** bank, card, and brokerage
      you care about is supported. If a major one is missing, stop and tell
      me. That's the case for using Plaid or Tiller instead.

While connecting institutions:
- [ ] Your bank logins go to SimpleFIN's upstream aggregator (MX). They never
      reach this program or SimpleFIN's app-facing API. When a bank offers its
      own "sign in with your bank" / OAuth screen, use it, and keep MFA on.
- [ ] After each connection, confirm the Bridge shows that account's **balance**.
- [ ] For the brokerage: note whether **holdings/positions** appear. If they
      don't, the Holdings tab will stay empty. Transactions and balances still work.
- [ ] Pay: $15/year (or $1.50/month).

Creating the token (do this right before running the install step that uses it, step 8):
- [ ] Create **one** app connection, named something like `Finance Sync - laptop`.
- [ ] Copy the setup token and claim it immediately with `setup-simplefin`.
      It's **single-use**. If the claim fails with "403 / already claimed" and
      you didn't claim it, someone else did: **delete that connection in the
      Bridge** and make a new one.
- [ ] Never paste the token into email, notes, or chat (including Claude).

## 3. Laptop: disk encryption and sign-in

**Why:** the vault key is protected by your Windows login. Disk encryption
is what makes a stolen laptop a hardware loss instead of a data loss.

- [ ] Settings > Privacy & security > **Device encryption = On** (Windows Home),
      or Control Panel > **BitLocker = On** for C: (Windows Pro).
      Verify in an *admin* PowerShell: `manage-bde -status C:` shows
      `Protection Status: Protection On`.
- [ ] The BitLocker recovery key is saved to your Microsoft account or printed,
      **not** in this folder.
- [ ] Windows sign-in requires a PIN, password, or Windows Hello. No auto-login.
- [ ] Screen locks after 10 minutes or less (Settings > Accounts > Sign-in options).

## 4. Google Cloud service account  (the robot that writes to your sheet)

At https://console.cloud.google.com, signed in with your personal Google account:
- [ ] Create a new project, e.g. `finance-sync`. **No billing account is needed**;
      if it nags for one, skip it.
- [ ] APIs & Services > Library > enable **Google Sheets API**. Only that one.
      You do *not* need the Drive API.
- [ ] IAM & Admin > Service Accounts > **Create service account**, name
      `finance-sync-writer`.
      - [ ] "Grant this service account access to project": **skip, no roles.**
      - [ ] "Grant users access": **skip.**
- [ ] Open it > Keys > Add key > Create new key > **JSON**. A file downloads.
      Treat that file as a password: don't email it, don't put it in Drive.
- [ ] You'll import it in step 8 with `setup-google`, which encrypts it and **deletes
      the file**. Afterwards, empty the Recycle Bin and check Downloads for copies.
- [ ] Put a yearly calendar reminder: "rotate Finance Sync key" (create a new key,
      run `setup-google` again, delete the old key in the console).

## 5. Create and share the Google Sheet

- [ ] Create a blank Google Sheet, e.g. **Finance Ledger**.
- [ ] Share > add the service-account email (`...@...iam.gserviceaccount.com`;
      `setup-google` prints it) > **Editor** > untick **Notify people** > Share.
- [ ] General access stays **Restricted**. Never "Anyone with the link".
- [ ] Keep the sheet URL handy for `set-sheet`.

## 6. Install Python

- [ ] https://www.python.org/downloads/windows/ : download the latest **3.12 or 3.13,
      64-bit** installer. On the first screen tick **"Add python.exe to PATH"**.
      Prefer this over the Microsoft Store version (the Store version's
      sandboxed paths can confuse Task Scheduler).
- [ ] Open a **new** PowerShell window: `py --version` shows 3.11 or newer.

## 7. Install Finance Sync

In PowerShell:
```powershell
cd "$HOME\Claude Cowork\Finance Sync"
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```
- [ ] It ends with a health check. FAIL lines for the secrets and sheet are expected at this point.
- [ ] If later commands are blocked with "running scripts is disabled", run
      `Set-ExecutionPolicy -Scope Process Bypass` in that window (affects that window only).

## 8. Connect everything (run in this order)

```powershell
.\scripts\finance-sync.ps1 setup-google "$HOME\Downloads\<the-key-file>.json"
#   -> prints the service-account email: share the sheet with it (step 5) if you haven't
.\scripts\finance-sync.ps1 set-sheet "<your sheet URL>"
.\scripts\finance-sync.ps1 setup-simplefin        # paste the token; input is hidden
.\scripts\finance-sync.ps1 init-sheet             # creates tabs, seeds categories + rules
.\scripts\finance-sync.ps1 doctor --online        # every line should be OK
.\scripts\finance-sync.ps1 sync --dry-run         # shows what WOULD be written
.\scripts\finance-sync.ps1 sync --force           # first real sync (pulls ~90 days)
```

## 9. Verify the first sync  (don't skip this)

Aggregators silently miss data. Check now, before you rely on it.
- [ ] **Sync Log** tab: Status is OK or WARN. Read every Warning; "needs attention"
      means re-authenticate that institution in the Bridge.
- [ ] **Completeness:** for 2-3 accounts, compare one full month against the bank's
      own statement: transaction count and total.
- [ ] **Signs:** purchases are **negative** and card payments **positive** on every
      credit card. If any card is flipped, tell me and I'll add a per-account fix.
- [ ] **Accounts** tab: fill the **Type** column yourself (Checking / Savings /
      Credit Card / Brokerage / Retirement). The sync never overwrites it, and
      the analysis uses it.
- [ ] Filter Transactions by Category = Uncategorized. Add rules in the **Rules** tab
      (Pattern + Category is enough), then run
      `.\scripts\finance-sync.ps1 recategorize --include-auto`. It re-applies rules
      to Uncategorized rows and to rows the rules filled earlier, and never
      overwrites a category you set by hand.

## 10. Turn on the daily schedule

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register-task.ps1
Start-ScheduledTask -TaskPath '\FinanceSync\' -TaskName 'Finance Sync'
Get-ScheduledTaskInfo -TaskPath '\FinanceSync\' -TaskName 'Finance Sync'
```
- [ ] `LastTaskResult` is `0`. The run right after a manual sync is skipped
      (20-hour guard); that's expected and still returns 0.

## 11. Bank-side tripwires  (independent of this system)

- [ ] In each bank/card app, turn on alerts for new-device logins and for
      transactions over a threshold. If anything upstream is ever compromised,
      the bank tells you, not this tool.

## 12. Optional: local LLM for leftover categories

Only if the laptop has **16 GB RAM or more**. Rules handle most transactions anyway.
- [ ] Install Ollama from https://ollama.com, then `ollama pull llama3.1:8b`.
- [ ] Set `enabled = true` under `[llm]` in `config\config.toml`.
- [ ] Ollama must listen on localhost only (its default). Do **not** set
      `OLLAMA_HOST=0.0.0.0`. The program also refuses non-local endpoints by default.
- [ ] Rows it categorizes are marked `llm:<model>` in "Categorized By". Review them.

## 13. Claude's side

- [ ] Your Google Sheets connector includes write tools. For strict read-only on
      Claude's side, restrict those tools in the connector settings.
- [ ] Give Claude the sheet URL and ask for the dashboard.

---

## If the laptop is lost or a secret leaks: kill switch (about 2 minutes)

1. SimpleFIN Bridge > delete the `Finance Sync - laptop` app connection.
   This kills the stored access URL immediately.
2. Google Cloud console > the service account > Keys > delete the key.
3. Change your Google password and sign out other sessions
   (myaccount.google.com > Security > Your devices).
4. On a recovered or new machine: `.\scripts\finance-sync.ps1 forget-secrets`,
   then redo steps 2 (token), 4 (new key) and 8.

## Ongoing upkeep (what you're on-call for)

| Signal | Meaning | Fix |
|---|---|---|
| Sync Log `WARN` + "needs attention" | A bank needs re-login at the aggregator | Re-authenticate that institution in the Bridge. Nothing to change here. |
| Sync Log `FAIL` + "403" | Access revoked/disabled | New token in the Bridge, then `setup-simplefin --replace` |
| Sync Log `FAIL` + "402" | SimpleFIN subscription lapsed | Renew |
| No new Sync Log rows for days | Task not running | `doctor`, and check `logs\finance-sync.log` |
| Yearly | Key hygiene | Rotate service-account key; review sheet sharing |
