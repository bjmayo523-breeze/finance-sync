"""Exercise the real CLI wiring and the real GoogleSheetStore against a fake gspread."""

from datetime import datetime, timezone

import pytest
from fake_store import FakeStore
from test_sync import FixtureProvider

from finance_sync import cli, paths
from finance_sync import schema as S
from finance_sync.sheets import GoogleSheetStore, ensure_tabs
from finance_sync.sync import init_sheet
from finance_sync.vault import MemoryKeyStore, Vault


# ---------------------------------------------------------------- fake gspread
class FakeWS:
    _next_id = 100

    def __init__(self, title, rows=1000, cols=10):
        self.title, self.col_count, self.row_count = title, cols, rows
        FakeWS._next_id += 1
        self.id = FakeWS._next_id
        self.grid: list[list] = []
        self.calls = []

    def _set(self, r, c, v):
        while len(self.grid) < r:
            self.grid.append([])
        row = self.grid[r - 1]
        while len(row) < c:
            row.append("")
        row[c - 1] = v

    def clear(self):
        self.grid = []

    def resize(self, rows=None, cols=None):
        self.row_count, self.col_count = rows or self.row_count, cols or self.col_count

    def batch_format(self, fmts):
        self.calls.append(("batch_format", len(fmts)))

    def update(self, values=None, range_name=None, **kw):
        from gspread.utils import a1_to_rowcol

        if kw.get("value_input_option") is not None:
            self.calls.append(("update", getattr(kw["value_input_option"], "value", kw["value_input_option"])))

        r0, c0 = a1_to_rowcol(range_name)
        for i, row in enumerate(values):
            for j, v in enumerate(row):
                self._set(r0 + i, c0 + j, v)

    def freeze(self, rows=None, cols=None):
        self.calls.append(("freeze", rows))

    def format(self, rng, fmt):
        self.calls.append(("format", rng))

    def row_values(self, n):
        return list(self.grid[n - 1]) if len(self.grid) >= n else []

    def col_values(self, c):
        return [row[c - 1] if len(row) >= c else "" for row in self.grid]

    reads = 0

    def get_all_values(self):
        FakeWS.reads += 1
        w = max((len(r) for r in self.grid), default=0)
        return [list(r) + [""] * (w - len(r)) for r in self.grid]

    def add_cols(self, n):
        self.col_count += n

    def append_rows(self, values, value_input_option=None, insert_data_option=None, table_range=None):
        self.calls.append(("append_rows", value_input_option.value, insert_data_option.value, table_range))
        self.grid.extend([list(v) for v in values])

    def batch_update(self, data, value_input_option=None):
        self.calls.append(("batch_update", len(data)))
        for d in data:
            self.update(values=d["values"], range_name=d["range"])


class FakeSpreadsheet:
    title = "Finance Sync Test"

    def __init__(self):
        self.ws = {"Sheet1": FakeWS("Sheet1")}

    listings = 0

    def worksheets(self):
        FakeSpreadsheet.listings += 1
        return list(self.ws.values())

    def worksheet(self, t):
        return self.ws[t]

    def add_worksheet(self, title, rows, cols, index=None):
        ws = FakeWS(title, rows, cols)
        if index == 0:
            self.ws = {title: ws, **self.ws}
        else:
            self.ws[title] = ws
        return ws

    def del_worksheet(self, ws):
        del self.ws[ws.title]

    def batch_update(self, body):
        import json

        json.dumps(body)
        self.batches = getattr(self, "batches", []) + [body]


def test_google_adapter_end_to_end(payload, tmp_path):
    from finance_sync.config import Config
    from finance_sync.state import State
    from finance_sync.sync import Categorizer, recategorize, run_sync

    sh = FakeSpreadsheet()
    store = GoogleSheetStore(sh)
    init_sheet(store)
    assert set(S.TABS) <= set(store.tab_names())
    assert "Sheet1" in store.tab_names()  # never deletes user tabs
    t = sh.ws[S.TRANSACTIONS]
    assert t.grid[0] == S.TXN_HEADERS and ("freeze", 1) in t.calls

    rep = run_sync(Config(), FixtureProvider(payload), store, Categorizer.from_store(store),
                   State(tmp_path / "s.json"), now=datetime(2026, 10, 23, 12, tzinfo=timezone.utc))
    assert rep.new == 11
    appends = [c for c in t.calls if c[0] == "append_rows"]
    assert appends == [("append_rows", "USER_ENTERED", "INSERT_ROWS", "A1")]
    assert len(t.grid) == 12
    # amounts are numbers, not strings
    amt_col = S.TXN_HEADERS.index("Amount")
    assert all(isinstance(r[amt_col], float) for r in t.grid[1:])

    # recategorize writes via batch_update to the right column
    store2 = GoogleSheetStore(sh)
    store2.append(S.RULES, [{"Priority": "1", "Pattern": "LOCAL SHOP", "Category": "Shopping"}])
    ex, ch = recategorize(store2, Categorizer.from_store(store2))
    assert ch == 1
    cat_col = S.TXN_HEADERS.index("Category")
    desc_col = S.TXN_HEADERS.index("Description")
    shop = next(r for r in t.grid if r[desc_col] == "SOME LOCAL SHOP")
    assert shop[cat_col] == "Shopping"


def test_adapter_adds_missing_headers_without_reordering():
    sh = FakeSpreadsheet()
    ws = sh.add_worksheet(S.TRANSACTIONS, 1000, 3)
    ws.update(values=[["Date", "Mine", "Amount"]], range_name="A1")
    store = GoogleSheetStore(sh)
    ensure_tabs(store, {S.TRANSACTIONS: S.TXN_HEADERS})
    hdr = ws.row_values(1)
    assert hdr[:3] == ["Date", "Mine", "Amount"] and "Transaction ID" in hdr
    assert ws.col_count >= len(hdr)


# --------------------------------------------------------------------- CLI
@pytest.fixture
def cli_env(monkeypatch, tmp_path, payload):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    for name in ("config.example.toml", "seed_categories.csv", "seed_rules.csv"):
        (root / "config" / name).write_bytes((paths.CONFIG_DIR / name).read_bytes())
    (root / "config" / "config.toml").write_bytes((paths.CONFIG_DIR / "config.example.toml").read_bytes())
    for attr, rel in [("ROOT", ""), ("CONFIG_DIR", "config"), ("CONFIG_FILE", "config/config.toml"),
                      ("SEED_CATEGORIES", "config/seed_categories.csv"), ("SEED_RULES", "config/seed_rules.csv"),
                      ("DATA_DIR", "data"), ("VAULT_FILE", "data/secrets.vault"), ("STATE_FILE", "data/state.json"),
                      ("LOCK_FILE", "data/sync.lock"), ("LOG_DIR", "logs"), ("LOG_FILE", "logs/finance-sync.log")]:
        monkeypatch.setattr(paths, attr, root / rel if rel else root)
    ks = MemoryKeyStore()
    monkeypatch.setattr(cli, "_vault", lambda: Vault(paths.VAULT_FILE, ks))
    store = FakeStore()
    monkeypatch.setattr(cli, "_store", lambda cfg, vault: store)
    monkeypatch.setattr(cli, "_provider", lambda cfg, vault: FixtureProvider(payload))
    return root, store


def test_cli_flow(cli_env, capsys, tmp_path):
    import json

    root, store = cli_env
    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdEF"
    assert cli.main(["set-sheet", f"https://docs.google.com/spreadsheets/d/{sid}/edit"]) == 0
    key = tmp_path / "sa.json"
    key.write_text(json.dumps({"type": "service_account", "private_key": "k", "client_email": "bot@p.iam.gserviceaccount.com"}))
    assert cli.main(["setup-google", str(key)]) == 0
    assert not key.exists()  # plaintext key deleted
    assert "bot@p.iam.gserviceaccount.com" in capsys.readouterr().out

    assert cli.main(["init-sheet"]) == 0
    assert cli.main(["sync", "--dry-run"]) == 0
    o = capsys.readouterr().out
    assert "DRY RUN" in o and "new: 11" in o
    assert store.records(S.TRANSACTIONS) == []

    assert cli.main(["sync"]) == 0
    assert len(store.records(S.TRANSACTIONS)) == 11
    assert cli.main(["sync"]) == 0
    assert "SKIPPED" in capsys.readouterr().out
    assert cli.main(["status"]) == 0
    assert "google_service_account" in capsys.readouterr().out

    log_text = (root / "logs" / "finance-sync.log").read_text()
    assert "Sync finished" in log_text and ":p@" not in log_text


def test_cli_sync_failure_logged_to_sheet(cli_env, monkeypatch, capsys):
    root, store = cli_env
    init_sheet(store)

    class Boom:
        def fetch(self, *a, **k):
            raise RuntimeError("SimpleFIN rejected the access credentials (403)")

    monkeypatch.setattr(cli, "_provider", lambda cfg, vault: Boom())
    assert cli.main(["sync", "--force"]) == 1
    log = store.records(S.SYNC_LOG)
    assert log[-1]["Status"] == "FAIL" and "403" in log[-1]["Warnings"]
    assert not (root / "data" / "sync.lock").exists()


def test_setup_google_rejects_non_service_account(cli_env, tmp_path, capsys):
    p = tmp_path / "oauth.json"
    p.write_text('{"installed": {}}')
    assert cli.main(["setup-google", str(p)]) == 1
    assert p.exists()


def test_existing_sheet_from_v1_gets_auto_category_column(payload, tmp_path):
    """An early sheet was created before 'Auto Category' existed."""
    from finance_sync.config import Config
    from finance_sync.state import State
    from finance_sync.sync import Categorizer, add_new_seed_rules, run_sync

    old_headers = [h for h in S.TXN_HEADERS if h != "Auto Category"]
    sh = FakeSpreadsheet()
    store = GoogleSheetStore(sh)
    init_sheet(store)
    ws = sh.ws[S.TRANSACTIONS]
    ws.grid[0] = list(old_headers)
    ws.col_count = 13
    store = GoogleSheetStore(sh)  # fresh header cache, as in a new process
    # what cmd_sync does before writing:
    for tab, headers in S.TABS.items():
        store.add_missing_headers(tab, headers)
    assert ws.grid[0][-1] == "Auto Category" and ws.col_count >= 14
    run_sync(Config(), FixtureProvider(payload), store, Categorizer.from_store(store),
             State(tmp_path / "s.json"), now=datetime(2026, 10, 23, 12, tzinfo=timezone.utc))
    hdr = ws.grid[0]
    netflix = next(r for r in ws.grid[1:] if r[hdr.index("Description")] == "NETFLIX.COM")
    assert netflix[hdr.index("Auto Category")] == "Subscriptions"
    assert add_new_seed_rules(store) == 0


def test_google_adapter_summaries_and_dashboard(payload, tmp_path):
    from finance_sync import sheet_dashboard
    from finance_sync.config import Config
    from finance_sync.state import State
    from finance_sync.summary import MONTHLY, SummaryConfig, write_summaries
    from finance_sync.sync import Categorizer, run_sync

    sh = FakeSpreadsheet()
    store = GoogleSheetStore(sh)
    init_sheet(store)
    run_sync(Config(), FixtureProvider(payload), store, Categorizer.from_store(store),
             State(tmp_path / "s.json"), now=datetime(2026, 10, 23, 12, tzinfo=timezone.utc))
    write_summaries(store, SummaryConfig())
    ms = sh.ws[MONTHLY]
    assert ms.grid[0][0] == "Month" and ("update", "RAW") in ms.calls
    assert isinstance(ms.grid[1][0], str) and ms.grid[1][0].count("-") == 1  # "2025-10"-style text, not a date
    # rewriting is a clean replace, not an append
    n = len(ms.grid)
    write_summaries(store, SummaryConfig())
    assert len(ms.grid) == n
    msg = sheet_dashboard.build(store)
    assert "6 charts" in msg and list(sh.ws)[0] == "Dashboard"
    reqs = sh.batches[-1]["requests"]
    assert sum("addChart" in r for r in reqs) == 6 and "setDataValidation" in reqs[0]
    assert "already exists" in sheet_dashboard.build(store)
    old_id = sh.ws["Dashboard"].id
    sheet_dashboard.build(store, rebuild=True)
    assert sh.ws["Dashboard"].id != old_id and "Transactions" in sh.ws  # only the Dashboard tab was replaced


def test_sync_creates_newer_helper_tabs_instead_of_failing(cli_env, capsys):
    root, store = cli_env
    init_sheet(store)
    del store.tabs[S.MANUAL]  # a sheet built before this tab existed
    assert cli.main(["sync", "--force"]) == 0
    assert S.MANUAL in store.tabs and store.tabs[S.MANUAL][0] == S.MANUAL_HEADERS


def test_sync_plus_summaries_stay_well_under_read_quota(payload, tmp_path):
    from finance_sync.config import Config
    from finance_sync.state import State
    from finance_sync.sync import Categorizer, run_sync

    sh = FakeSpreadsheet()
    init_sheet(GoogleSheetStore(sh))
    store = GoogleSheetStore(sh)  # fresh process
    FakeWS.reads, FakeSpreadsheet.listings = 0, 0
    run_sync(Config(), FixtureProvider(payload), store, Categorizer.from_store(store),
             State(tmp_path / "s.json"), now=datetime(2026, 10, 23, 12, tzinfo=timezone.utc))
    # each source tab read about once per run, the tab list once
    assert FakeWS.reads <= 14, FakeWS.reads
    assert FakeSpreadsheet.listings <= 2, FakeSpreadsheet.listings
