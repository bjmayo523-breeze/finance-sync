"""In-memory stand-in for GoogleSheetStore with the same semantics."""

from __future__ import annotations

from typing import Any

from finance_sync.sheets import to_cell


class FakeStore:
    title = "Fake Sheet"

    def __init__(self) -> None:
        self.tabs: dict[str, list[list[Any]]] = {}
        self.append_calls = 0

    def tab_names(self) -> list[str]:
        return list(self.tabs)

    def create_tab(self, tab: str, headers: list[str]) -> None:
        self.tabs[tab] = [list(headers)]

    def headers(self, tab: str) -> list[str]:
        return list(self.tabs[tab][0]) if self.tabs[tab] else []

    def add_missing_headers(self, tab: str, headers: list[str]) -> list[str]:
        cur = self.headers(tab)
        missing = [h for h in headers if h not in cur]
        if missing:
            if not self.tabs[tab]:
                self.tabs[tab].append([])
            self.tabs[tab][0].extend(missing)
        return missing

    def records(self, tab: str) -> list[dict]:
        rows = self.tabs.get(tab, [])
        if not rows:
            return []
        hdr = rows[0]
        out = []
        for i, row in enumerate(rows[1:], start=2):
            rec = {h: str(row[j]) if j < len(row) else "" for j, h in enumerate(hdr)}
            rec["_row"] = i
            out.append(rec)
        return out

    def column(self, tab: str, header: str) -> list[str]:
        hdr = self.headers(tab)
        if header not in hdr:
            return []
        j = hdr.index(header)
        return [str(r[j]) if j < len(r) else "" for r in self.tabs[tab][1:]]

    def append(self, tab: str, rows: list[dict]) -> None:
        if not rows:
            return
        self.append_calls += 1
        hdr = self.headers(tab)
        for r in rows:
            self.tabs[tab].append([to_cell(r.get(h)) for h in hdr])

    def replace_tab(self, tab: str, rows, percent_cols=None) -> None:
        self.tabs[tab] = [list(r) for r in rows]

    def update_cells(self, tab: str, updates) -> None:
        hdr = self.headers(tab)
        for row, header, value in updates:
            if header not in hdr:
                continue
            j = hdr.index(header)
            r = self.tabs[tab][row - 1]
            while len(r) <= j:
                r.append("")
            r[j] = to_cell(value)
