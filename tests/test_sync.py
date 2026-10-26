from datetime import datetime, timedelta, timezone

import pytest
from fake_store import FakeStore

from finance_sync import schema as S
from finance_sync.config import Config
from finance_sync.models import FetchResult
from finance_sync.providers.simplefin import SimpleFINProvider
from finance_sync.state import RunLock, State
from finance_sync.sync import Categorizer, init_sheet, recategorize, run_sync

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


class FixtureProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def fetch(self, start, end, include_pending=False):
        self.calls.append((start, end))
        r = FetchResult()
        SimpleFINProvider("https://u:p@bridge.simplefin.org/simplefin").parse(self.payload, r, include_pending)
        return r


@pytest.fixture
def env(tmp_path, payload):
    store = FakeStore()
    init_sheet(store)
    state = State(tmp_path / "state.json")
    return store, state, FixtureProvider(payload), Config()


def txn_rows(store):
    return store.records(S.TRANSACTIONS)


def test_init_sheet_idempotent():
    store = FakeStore()
    a1 = init_sheet(store)
    assert set(store.tab_names()) == set(S.TABS)
    assert any("seeded" in a for a in a1)
    a2 = init_sheet(store)
    assert a2 == []
    assert len(store.records(S.CATEGORIES)) == 48


def test_init_sheet_adds_missing_columns_to_existing_tab():
    store = FakeStore()
    store.create_tab(S.TRANSACTIONS, ["Date", "My Own Column"])
    init_sheet(store)
    hdr = store.headers(S.TRANSACTIONS)
    assert hdr[:2] == ["Date", "My Own Column"]
    assert "Transaction ID" in hdr


def test_first_sync(env):
    store, state, prov, cfg = env
    rep = run_sync(cfg, prov, store, Categorizer.from_store(store), state, now=NOW)
    assert rep.status == "WARN"  # SimpleFIN connection warning is surfaced
    assert rep.new == 11
    assert rep.window_start == NOW - timedelta(days=90)
    rows = txn_rows(store)
    assert len(rows) == 11
    # dates sorted ascending
    assert [r["Date"] for r in rows] == sorted(r["Date"] for r in rows)
    by_desc = {r["Description"]: r for r in rows}
    assert by_desc["H-E-B #123 SPRINGFIELD TX"]["Category"] == "Groceries"
    assert by_desc["H-E-B #123 SPRINGFIELD TX"]["Categorized By"].startswith("rule:")
    assert by_desc["SOME LOCAL SHOP"]["Category"] == "Uncategorized"
    # formula injection neutralised
    assert "'=HYPERLINK(\"http://evil\")" in by_desc
    assert rep.uncategorized == 2
    # accounts, balances, holdings, log
    assert len(store.records(S.ACCOUNTS)) == 3
    assert len(store.records(S.BALANCES)) == 3
    assert len(store.records(S.HOLDINGS)) == 2
    log = store.records(S.SYNC_LOG)
    assert log[-1]["Status"] == "WARN" and "may need attention" in log[-1]["Warnings"]
    assert state.last_success == NOW


def test_second_sync_dedupes_and_respects_guard(env):
    store, state, prov, cfg = env
    run_sync(cfg, prov, store, Categorizer.from_store(store), state, now=NOW)
    # 2 hours later: skipped, provider not called
    rep = run_sync(cfg, prov, store, Categorizer.from_store(store), state, now=NOW + timedelta(hours=2))
    assert rep.skipped_reason and len(prov.calls) == 1
    # forced: provider called, nothing duplicated
    rep = run_sync(cfg, prov, store, Categorizer.from_store(store), state,
                   now=NOW + timedelta(hours=2), force=True)
    assert rep.new == 0
    assert len(txn_rows(store)) == 11
    assert len(store.records(S.BALANCES)) == 3  # same day -> no new balance rows
    # next day: overlap window, new balance rows, still no dup txns
    rep = run_sync(cfg, prov, store, Categorizer.from_store(store), state, now=NOW + timedelta(days=1))
    assert prov.calls[-1][0] == NOW + timedelta(hours=2) - timedelta(days=7)
    assert rep.new == 0 and rep.balances_logged == 3


def test_accounts_upsert_keeps_user_columns(env):
    store, state, prov, cfg = env
    run_sync(cfg, prov, store, Categorizer.from_store(store), state, now=NOW)
    recs = store.records(S.ACCOUNTS)
    row = next(r for r in recs if r["Account ID"] == "ACT-card-002")["_row"]
    store.update_cells(S.ACCOUNTS, [(row, "Type", "Credit Card")])
    prov.payload["accounts"][1]["balance"] = "-99.00"
    run_sync(cfg, prov, store, Categorizer.from_store(store), state, now=NOW + timedelta(days=1))
    recs = store.records(S.ACCOUNTS)
    card = next(r for r in recs if r["Account ID"] == "ACT-card-002")
    assert card["Type"] == "Credit Card"
    assert card["Balance"] == "-99.0"
    assert len(recs) == 3


def test_dry_run_writes_nothing(env):
    store, state, prov, cfg = env
    before = store.append_calls
    rep = run_sync(cfg, prov, store, Categorizer.from_store(store), state, now=NOW, dry_run=True, force=True)
    assert rep.new == 11
    assert store.append_calls == before
    assert state.last_success is None


def test_recategorize_only_fills_uncategorized(env):
    store, state, prov, cfg = env
    run_sync(cfg, prov, store, Categorizer.from_store(store), state, now=NOW)
    rows = txn_rows(store)
    shop = next(r for r in rows if r["Description"] == "SOME LOCAL SHOP")
    heb = next(r for r in rows if r["Description"].startswith("H-E-B"))
    # user manually overrides HEB to something else
    store.update_cells(S.TRANSACTIONS, [(heb["_row"], "Category", "Restaurants")])
    # user adds a rule for the local shop
    store.append(S.RULES, [{"Priority": "1", "Pattern": "LOCAL SHOP", "Category": "Shopping"}])
    examined, changed = recategorize(store, Categorizer.from_store(store))
    assert changed == 1 and examined == 2
    rows = {r["Description"]: r for r in txn_rows(store)}
    assert rows["SOME LOCAL SHOP"]["Category"] == "Shopping"
    assert rows["H-E-B #123 SPRINGFIELD TX"]["Category"] == "Restaurants"  # untouched


def test_llm_fallback_validated(env):
    store, state, prov, cfg = env
    answers = iter(["Shopping", "NOT A CATEGORY"])

    def fake_llm(t, acct, cats):
        a = next(answers, None)
        return a if a in cats else None

    c = Categorizer.from_store(store, llm=fake_llm, llm_name="test-model", llm_budget=5)
    rep = run_sync(cfg, prov, store, c, state, now=NOW)
    assert rep.by_llm == 1 and rep.uncategorized == 1
    assert any(r["Categorized By"] == "llm:test-model" for r in txn_rows(store))


def test_llm_budget(env):
    store, state, prov, cfg = env
    calls = []
    c = Categorizer.from_store(store, llm=lambda *a: calls.append(1) or "Shopping", llm_name="m", llm_budget=1)
    run_sync(cfg, prov, store, c, state, now=NOW)
    assert len(calls) == 1


def test_run_lock(tmp_path):
    p = tmp_path / "x.lock"
    with RunLock(p):
        with pytest.raises(RuntimeError):
            with RunLock(p):
                pass
    assert not p.exists()


def test_auto_category_lets_new_rules_fix_old_rule_hits(env):
    store, state, prov, cfg = env
    run_sync(cfg, prov, store, Categorizer.from_store(store), state, now=NOW)
    rows = {r["Description"]: r for r in txn_rows(store)}
    assert rows["NETFLIX.COM"]["Auto Category"] == "Subscriptions"
    # user overrides one machine-categorized row
    store.update_cells(S.TRANSACTIONS, [(rows["H-E-B #123 SPRINGFIELD TX"]["_row"], "Category", "Restaurants")])
    # a new, higher-priority rule changes the answer for Netflix and HEB
    store.append(S.RULES, [
        {"Priority": "1", "Pattern": "NETFLIX", "Category": "Entertainment"},
        {"Priority": "1", "Pattern": "H-E-B", "Category": "Shopping"},
    ])
    ex, ch = recategorize(store, Categorizer.from_store(store))  # default: blank only
    assert ch == 0
    ex, ch = recategorize(store, Categorizer.from_store(store), include_auto=True)
    rows = {r["Description"]: r for r in txn_rows(store)}
    assert rows["NETFLIX.COM"]["Category"] == "Entertainment"
    assert rows["NETFLIX.COM"]["Auto Category"] == "Entertainment"
    assert rows["H-E-B #123 SPRINGFIELD TX"]["Category"] == "Restaurants"  # user's choice kept
    # idempotent
    assert recategorize(store, Categorizer.from_store(store), include_auto=True)[1] == 0


def test_add_new_seed_rules_only_appends_missing(env):
    from finance_sync.sync import add_new_seed_rules

    store, *_ = env
    before = len(store.records(S.RULES))
    assert add_new_seed_rules(store) == 0
    # simulate an old sheet seeded with a shorter list, plus a user-edited rule
    store.tabs[S.RULES] = store.tabs[S.RULES][:5]
    store.append(S.RULES, [{"Priority": "1", "Pattern": "MY OWN", "Category": "Shopping"}])
    n = add_new_seed_rules(store)
    assert n == before - 4
    assert any(r["Pattern"] == "MY OWN" for r in store.records(S.RULES))


def test_report_lists_top_uncategorized(env):
    store, state, prov, cfg = env
    rep = run_sync(cfg, prov, store, Categorizer.from_store(store), state, now=NOW, dry_run=True)
    names = [n for n, _ in rep.top_uncategorized]
    assert "SOME LOCAL SHOP" in names


def test_update_rules_adds_new_category_to_old_sheet(env):
    from finance_sync.sync import add_new_seed_categories

    store, *_ = env
    store.tabs[S.CATEGORIES] = [r for r in store.tabs[S.CATEGORIES] if r[0] != "Snacks & Drinks"]
    store.append(S.CATEGORIES, [{"Category": "My Custom", "Group": "Other", "Type": "Expense"}])
    assert add_new_seed_categories(store) == 1
    names = [r["Category"] for r in store.records(S.CATEGORIES)]
    assert "Snacks & Drinks" in names and "My Custom" in names
    assert add_new_seed_categories(store) == 0


def test_split_rules_with_same_pattern_both_seeded(env, tmp_path, monkeypatch):
    """Two starter rules may share a pattern (e.g. small vs large gas-station charges); both get added once."""
    from finance_sync import paths
    from finance_sync.sync import add_new_seed_rules

    store, *_ = env
    seed = tmp_path / "seed_rules.csv"
    seed.write_text("Priority,Pattern,Match,Field,Account Contains,Min Amount,Max Amount,Category,Notes\n"
                    "48,re:GAS MART,regex,description,,-29.99,-0.01,Snacks & Drinks,\n"
                    "50,re:GAS MART,regex,description,,,,Fuel,\n")
    monkeypatch.setattr(paths, "SEED_RULES", seed)
    assert add_new_seed_rules(store) == 2
    both = [r for r in store.records(S.RULES) if r["Pattern"] == "re:GAS MART"]
    assert {r["Category"] for r in both} == {"Snacks & Drinks", "Fuel"}
    assert add_new_seed_rules(store) == 0


