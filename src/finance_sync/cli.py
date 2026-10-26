"""Command line entry point.

  finance-sync setup-simplefin        paste the one-time SimpleFIN setup token
  finance-sync setup-google KEYFILE   import the service-account key (then deletes the file)
  finance-sync set-sheet URL_OR_ID    point at your Google Sheet
  finance-sync init-sheet             create tabs, seed categories + rules
  finance-sync doctor [--online]      check everything is wired up
  finance-sync sync [--dry-run] [--force]
  finance-sync recategorize [--dry-run]
  finance-sync status
  finance-sync forget-secrets         wipe stored credentials
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import logging.handlers
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from finance_sync import __version__, paths
from finance_sync import config as config_mod
from finance_sync import schema as S
from finance_sync.vault import GOOGLE_SERVICE_ACCOUNT, SIMPLEFIN_ACCESS_URL, Vault, VaultError

log = logging.getLogger("finance_sync")

_URL_CRED_RE = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@")


class RedactFilter(logging.Filter):
    """Belt-and-braces: strip user:pass@ from any URL that reaches a log line."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "@" in msg:
            record.msg = _URL_CRED_RE.sub(r"\1***@", msg)
            record.args = ()
        return True


def setup_logging(verbose: bool) -> None:
    paths.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for h in [h for h in root.handlers if getattr(h, "_finance_sync", False)]:
        root.removeHandler(h)
        h.close()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        paths.LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    fh.addFilter(RedactFilter())
    fh._finance_sync = True
    root.addHandler(fh)
    if sys.stderr is not None:  # pythonw.exe (scheduled task) has no console
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        sh.addFilter(RedactFilter())
        sh.setLevel(logging.WARNING)
        sh._finance_sync = True
        root.addHandler(sh)
    # third-party libraries never get DEBUG (urllib3 debug logs full URLs)
    for noisy in ("urllib3", "google", "gspread", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def out(msg: str = "") -> None:
    if sys.stdout is not None:
        print(msg)


# ------------------------------------------------------------------ helpers
def _vault() -> Vault:
    return Vault()


def _cfg() -> config_mod.Config:
    if not paths.CONFIG_FILE.exists():
        raise RuntimeError("config/config.toml not found. Run the install script first "
                           "(scripts\\install.ps1 on Windows, scripts/install.sh on macOS/Linux).")
    return config_mod.load()


def _store(cfg: config_mod.Config, vault: Vault):
    from finance_sync.sheets import GoogleSheetStore

    if not cfg.spreadsheet_id:
        raise RuntimeError("No sheet set. Run: finance-sync set-sheet <sheet URL>")
    info = json.loads(vault.require(GOOGLE_SERVICE_ACCOUNT, "Run: finance-sync setup-google KEYFILE"))
    return GoogleSheetStore.connect(info, cfg.spreadsheet_id)


def _provider(cfg: config_mod.Config, vault: Vault):
    from finance_sync.providers.simplefin import SimpleFINProvider

    url = vault.require(SIMPLEFIN_ACCESS_URL, "Run: finance-sync setup-simplefin")
    return SimpleFINProvider(
        url,
        tz=cfg.timezone,
        timeout=cfg.simplefin.timeout_seconds,
        chunk_days=cfg.simplefin.chunk_days,
    )


def _categorizer(cfg: config_mod.Config, store):
    from finance_sync.sync import Categorizer

    llm, name = None, ""
    if cfg.llm.enabled:
        from finance_sync.llm import OllamaClassifier

        clf = OllamaClassifier(
            cfg.llm.endpoint, cfg.llm.model, cfg.llm.timeout_seconds, cfg.llm.allow_remote
        )
        ok, msg = clf.health()
        if ok:
            llm, name = clf.classify, cfg.llm.model
        else:
            log.warning("LLM disabled for this run: %s", msg)
    return Categorizer.from_store(store, llm=llm, llm_name=name, llm_budget=cfg.llm.max_per_run)


# ----------------------------------------------------------------- commands
def cmd_setup_simplefin(args) -> int:
    from finance_sync.providers.simplefin import claim

    vault = _vault()
    if vault.path.exists() and vault.get(SIMPLEFIN_ACCESS_URL) and not args.replace:
        out("A SimpleFIN connection is already stored. Use --replace to swap it.")
        return 1
    out("Paste the SimpleFIN setup token (input is hidden), then press Enter.")
    token = getpass.getpass("Setup token: ")
    access_url = claim(token)
    vault.set(SIMPLEFIN_ACCESS_URL, access_url)
    out("Claimed. The setup token is now spent; the access credential is encrypted in data\\secrets.vault.")
    out("Next: finance-sync doctor --online   (uses one SimpleFIN request)")
    return 0


def cmd_setup_google(args) -> int:
    key_path = Path(args.keyfile).expanduser()
    info = json.loads(key_path.read_text(encoding="utf-8"))
    if info.get("type") != "service_account" or "private_key" not in info:
        out("That file is not a Google service-account key (expected type=service_account).")
        return 1
    _vault().set(GOOGLE_SERVICE_ACCOUNT, json.dumps(info))
    out("Service-account key stored encrypted.")
    out("")
    out("Share your Google Sheet with this address as EDITOR (untick 'Notify people'):")
    out(f"    {info.get('client_email')}")
    out("")
    if args.keep:
        out(f"--keep given: {key_path} was NOT deleted. Delete it yourself once things work.")
    else:
        key_path.unlink()
        out(f"Deleted the plaintext key file {key_path.name}. Empty your Recycle Bin/Downloads if you copied it.")
    return 0


def cmd_set_sheet(args) -> int:
    sheet_id = config_mod.parse_sheet_id(args.sheet)
    config_mod.set_spreadsheet_id(sheet_id)
    out(f"Sheet set: {sheet_id}")
    return 0


def cmd_init_sheet(args) -> int:
    from finance_sync.sync import init_sheet

    cfg = _cfg()
    store = _store(cfg, _vault())
    actions = init_sheet(store)
    if args.update_rules:
        from finance_sync.sync import add_new_seed_categories, add_new_seed_rules

        nc = add_new_seed_categories(store)
        n = add_new_seed_rules(store)
        actions.append(f"added {nc} new categories" if nc else "categories already up to date")
        actions.append(f"added {n} new starter rules" if n else "rules already up to date")
    out(f"Sheet: {store.title}")
    for a in actions or ["nothing to do - all tabs present"]:
        out(f"  - {a}")
    return 0


def cmd_sync(args) -> int:
    from finance_sync.state import RunLock, State
    from finance_sync.sync import SyncReport, run_sync, write_log_row

    cfg = _cfg()
    vault = _vault()
    tz = ZoneInfo(cfg.timezone)
    with RunLock():
        state = State()
        store = None
        try:
            store = _store(cfg, vault)
            core = [S.TRANSACTIONS, S.CATEGORIES, S.RULES]
            missing = [t for t in core if t not in store.tab_names()]
            if missing:
                raise RuntimeError(f"Sheet is missing tabs {missing}. Run: finance-sync init-sheet")
            if not args.dry_run:
                from finance_sync.sheets import ensure_tabs

                for action in ensure_tabs(store, S.TABS):  # adds newer helper tabs/columns automatically
                    log.info("Sheet upgrade: %s", action)
            rep = run_sync(
                cfg,
                _provider(cfg, vault),
                store,
                _categorizer(cfg, store),
                state,
                dry_run=args.dry_run,
                force=args.force or args.dry_run,
            )
        except Exception as e:
            log.exception("Sync failed")
            if store is not None and not args.dry_run:
                try:
                    write_log_row(store, SyncReport(status="FAIL"), datetime.now(timezone.utc), tz, error=str(e))
                except Exception:
                    log.exception("Could not write failure to Sync Log")
            out(f"FAILED: {e}")
            return 1
    log.info("Sync finished: %s", rep.summary().replace("\n", " | "))
    out(("DRY RUN - nothing written\n" if args.dry_run else "") + rep.summary())
    if args.dry_run and rep.new_rows:
        out("\nFirst new rows:")
        for r in rep.new_rows[:15]:
            out(f"  {r['Date']}  {float(r['Amount']):>10.2f}  {r['Category']:<20} {r['Description'][:50]}")
    if rep.top_uncategorized:
        out("\nTop uncategorized merchants (count, total) - add Rules for these:")
        for name, (n, total) in rep.top_uncategorized[:25]:
            out(f"  {n:>4}  {total:>10.2f}  {name}")
    return 0


def cmd_recategorize(args) -> int:
    from finance_sync.sync import recategorize

    cfg = _cfg()
    store = _store(cfg, _vault())
    store.add_missing_headers(S.TRANSACTIONS, S.TXN_HEADERS)
    examined, changed = recategorize(
        store, _categorizer(cfg, store), dry_run=args.dry_run, include_auto=args.include_auto
    )
    scope = "uncategorized + auto-categorized" if args.include_auto else "uncategorized"
    out(f"Looked at {examined} {scope} rows; {'would change' if args.dry_run else 'changed'} {changed}.")
    return 0


def cmd_reconcile(args) -> int:
    from finance_sync.reconcile import reconcile

    cfg = _cfg()
    if not cfg.reimbursements.enabled:
        out("Reimbursement matching is off ([reimbursements] enabled = false in config.toml).")
        return 1
    store = _store(cfg, _vault())
    rep = reconcile(store, cfg.reimbursements, dry_run=args.dry_run)
    out(("DRY RUN - nothing written\n" if args.dry_run else "") + rep.summary())
    return 0


def cmd_summarize(args) -> int:
    from finance_sync.summary import SummaryConfig, write_summaries

    cfg = _cfg()
    store = _store(cfg, _vault())
    counts = write_summaries(store, SummaryConfig(**vars(cfg.summary)))
    for tab, n in counts.items():
        out(f"  {tab}: {n} rows")
    return 0


def cmd_init_dashboard(args) -> int:
    from finance_sync import sheet_dashboard
    from finance_sync.summary import SummaryConfig, write_summaries

    cfg = _cfg()
    store = _store(cfg, _vault())
    write_summaries(store, SummaryConfig(**vars(cfg.summary)))
    out(sheet_dashboard.build(store, rebuild=args.rebuild))
    return 0


def cmd_import_csv(args) -> int:
    from datetime import date as _date

    from finance_sync import importer
    from finance_sync.summary import SummaryConfig, write_summaries

    cfg = _cfg()
    store = _store(cfg, _vault())
    acct = importer.resolve_account(store, args.account)
    fmt, rows = importer.parse_csv(importer.read_file(args.file), args.format, args.flip_sign)
    if args.until:
        cutoff = _date.fromisoformat(args.until)
    else:
        cutoff = importer.first_simplefin_date(store, acct["Account ID"])
        if cutoff is None:
            raise RuntimeError(
                f"No SimpleFIN transactions for '{acct['Account']}' yet, so there is no safe cutoff. "
                "Pass --until YYYY-MM-DD (the first day NOT to import)."
            )
    now = datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d %H:%M")
    rep = importer.import_rows(store, _categorizer(cfg, store), acct, rows, cutoff, now, dry_run=args.dry_run)
    rep.fmt = fmt
    out(f"Account: {acct['Account']} ({acct.get('Institution', '')})")
    out(("DRY RUN - nothing written\n" if args.dry_run else "") + rep.summary())
    if args.dry_run and rep.sample:
        out("\nFirst rows:")
        for r in rep.sample:
            out(f"  {r['Date']}  {float(r['Amount']):>10.2f}  {r['Category']:<20} {r['Description'][:50]}")
    if not args.dry_run and rep.new:
        if cfg.reimbursements.enabled:
            from finance_sync.reconcile import reconcile

            out("Reimbursements: " + reconcile(store, cfg.reimbursements).summary())
        write_summaries(store, SummaryConfig(**vars(cfg.summary)))
        out("Summary tabs updated.")
    return 0


def cmd_status(args) -> int:
    from finance_sync.state import State

    st = State()
    last = st.last_success
    out(f"finance-sync {__version__}")
    out(f"Project folder: {paths.ROOT}")
    out(f"Last successful sync: {last.astimezone().strftime('%Y-%m-%d %H:%M') if last else 'never'}")
    try:
        out(f"Stored secrets: {', '.join(_vault().names()) or 'none'}")
    except VaultError as e:
        out(f"Vault: {e}")
    return 0



def _schedule_check() -> tuple[bool, str] | None:
    """(registered?, how to fix) for this OS's scheduler, or None if we can't tell."""
    system = platform.system()
    try:
        if system == "Windows":
            r = subprocess.run(["schtasks", "/Query", "/TN", r"\FinanceSync\Finance Sync"],
                               capture_output=True, text=True)
            return r.returncode == 0, "run scripts\\register-task.ps1 (last setup step)"
        if system == "Darwin":
            r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.financesync.sync"],
                               capture_output=True, text=True)
            return r.returncode == 0, "run bash scripts/register-launchd.sh (last setup step)"
        if system == "Linux":
            r = subprocess.run(["systemctl", "--user", "is-enabled", "finance-sync.timer"],
                               capture_output=True, text=True)
            return r.returncode == 0, "run bash scripts/register-systemd.sh (last setup step)"
    except (FileNotFoundError, OSError):
        return None
    return None

def cmd_doctor(args) -> int:
    ok_all = True

    def check(label: str, ok: bool, detail: str = "", gate: bool = True) -> None:
        nonlocal ok_all
        if gate:
            ok_all &= ok
        tag = "OK " if ok else ("FAIL" if gate else "TODO")
        out(f"[{tag}] {label}" + (f" - {detail}" if detail else ""))

    check("Python >= 3.11", sys.version_info >= (3, 11), platform.python_version())
    check("config.toml present", paths.CONFIG_FILE.exists(), str(paths.CONFIG_FILE))
    cfg = config_mod.load()
    check("Sheet ID set", bool(cfg.spreadsheet_id), cfg.spreadsheet_id or "run set-sheet")

    vault = _vault()
    try:
        backend = vault.keystore.describe()
        win = platform.system() == "Windows"
        insecure = "fail" in backend or "Plaintext" in backend or "null" in backend
        on_windows_vault = "WinVault" in backend or "Windows" in backend
        check("Secure keyring", not insecure and (not win or on_windows_vault), backend)
        names = vault.names()
        check("SimpleFIN credential stored", SIMPLEFIN_ACCESS_URL in names)
        check("Google key stored", GOOGLE_SERVICE_ACCOUNT in names)
    except VaultError as e:
        check("Vault readable", False, str(e))

    stray = [p.name for p in paths.ROOT.glob("*.json")] + [p.name for p in (paths.ROOT / "config").glob("*.json")]
    check("No plaintext key files in project", not stray, ", ".join(stray))

    sched = _schedule_check()
    if sched:
        ok, hint = sched
        check("Scheduled task registered", ok, "" if ok else hint, gate=False)

    if cfg.llm.enabled:
        try:
            from finance_sync.llm import OllamaClassifier

            ok, msg = OllamaClassifier(cfg.llm.endpoint, cfg.llm.model, 5, cfg.llm.allow_remote).health()
            check("Local LLM", ok, msg)
        except Exception as e:
            check("Local LLM", False, str(e))

    if args.online and ok_all:
        try:
            store = _store(cfg, vault)
            tabs = store.tab_names()
            missing = [t for t in S.TABS if t not in tabs]
            check("Google Sheet opens", True, store.title)
            check("Sheet tabs", not missing, f"missing {missing} - run init-sheet" if missing else "all present")
        except Exception as e:
            check("Google Sheet opens", False, str(e))
        try:
            now = datetime.now(timezone.utc)
            from datetime import timedelta

            res = _provider(cfg, vault).fetch(now - timedelta(days=3), now)
            check("SimpleFIN reachable", True, f"{len(res.accounts)} accounts")
            for a in res.accounts.values():
                out(f"        {a.institution or '?'} / {a.name}: balance {a.balance}")
            for w in res.warnings:
                out(f"        WARNING from SimpleFIN: {w}")
        except Exception as e:
            check("SimpleFIN reachable", False, str(e))
    elif args.online:
        out("(online checks skipped until the offline checks pass)")

    out("\nAll good." if ok_all else "\nFix the FAIL items above.")
    return 0 if ok_all else 1


def cmd_forget(args) -> int:
    if not args.yes:
        ans = input("Delete stored SimpleFIN + Google credentials from this computer? [y/N] ")
        if ans.strip().lower() != "y":
            return 1
    _vault().destroy()
    out("Credentials removed. Also revoke them at the source:")
    out("  - SimpleFIN Bridge: disable/delete the app connection")
    out("  - Google Cloud console: delete the service-account key")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="finance-sync", description="SimpleFIN -> Google Sheets sync")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup-simplefin", help="claim a SimpleFIN setup token")
    s.add_argument("--replace", action="store_true")
    s.set_defaults(fn=cmd_setup_simplefin)

    s = sub.add_parser("setup-google", help="import a service-account key file")
    s.add_argument("keyfile")
    s.add_argument("--keep", action="store_true", help="don't delete the key file afterwards")
    s.set_defaults(fn=cmd_setup_google)

    s = sub.add_parser("set-sheet", help="set the target Google Sheet")
    s.add_argument("sheet")
    s.set_defaults(fn=cmd_set_sheet)

    s = sub.add_parser("init-sheet", help="create tabs and seed data")
    s.add_argument("--update-rules", action="store_true",
                   help="append starter categories and rules shipped since you first ran init-sheet (keeps your edits)")
    s.set_defaults(fn=cmd_init_sheet)

    s = sub.add_parser("sync", help="pull and append new transactions")
    s.add_argument("--dry-run", action="store_true", help="fetch and categorize, write nothing")
    s.add_argument("--force", action="store_true", help="ignore min_hours_between_runs")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("recategorize", help="apply rules to blank/Uncategorized rows")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--include-auto", action="store_true",
                   help="also re-run rules on rows the sync categorized that you haven't changed")
    s.set_defaults(fn=cmd_recategorize)

    s = sub.add_parser("doctor", help="check setup")
    s.add_argument("--online", action="store_true", help="also open the sheet and call SimpleFIN once")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("reconcile", help="match reimbursement deposits to the card charges they repay")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_reconcile)

    sub.add_parser("summarize", help="rebuild the monthly summary tabs").set_defaults(fn=cmd_summarize)

    s = sub.add_parser("init-dashboard", help="create the Dashboard tab (charts) in the Google Sheet")
    s.add_argument("--rebuild", action="store_true", help="delete and recreate the Dashboard tab")
    s.set_defaults(fn=cmd_init_dashboard)

    s = sub.add_parser("import-csv", help="import older history from a bank/card CSV export")
    s.add_argument("file")
    s.add_argument("--account", required=True, help="part of the account name, as shown in the Accounts tab")
    s.add_argument("--format", default="auto", choices=["auto", "chase-checking", "chase-card", "capital-one", "generic"])
    s.add_argument("--flip-sign", action="store_true", help="for exports that show purchases as positive")
    s.add_argument("--until", help="YYYY-MM-DD: import only rows before this date (default: first SimpleFIN date)")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_import_csv)

    sub.add_parser("status").set_defaults(fn=cmd_status)

    s = sub.add_parser("forget-secrets", help="delete stored credentials")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_forget)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        return int(args.fn(args) or 0)
    except (VaultError, ValueError, FileNotFoundError, RuntimeError) as e:
        log.error("%s", e)
        out(f"ERROR: {e}")
        return 1
    except Exception as e:  # unexpected: keep the traceback in the log, short message on screen
        log.exception("Unexpected error")
        out(f"ERROR: {type(e).__name__}: {e}  (details in logs\\finance-sync.log)")
        return 1
    except KeyboardInterrupt:
        return 130
