"""Google Sheets access via a service account.

Scope is `spreadsheets` only (no Drive scope), and a service account can only
open sheets explicitly shared with its email address. So this program can
touch exactly one file: the sheet you shared with it.

Write policy (matches the Cowork folder rules): append rows, and update cells
the sync owns. Never deletes rows, tabs, or user-entered values.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Protocol

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def to_cell(v: Any) -> Any:
    """Convert a Python value to a safe sheet value.

    Text that starts with = + - @ is prefixed with an apostrophe so a merchant
    name can never be evaluated as a formula (CSV/formula injection).
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.isoformat()
    s = str(v)
    if _PLAIN_NUMBER.match(s):  # "-20" is a number, not a formula
        return float(s) if "." in s else int(s)
    if s.startswith(_FORMULA_PREFIXES):
        return "'" + s
    return s


class Store(Protocol):
    def tab_names(self) -> list[str]: ...
    def create_tab(self, tab: str, headers: list[str]) -> None: ...
    def headers(self, tab: str) -> list[str]: ...
    def add_missing_headers(self, tab: str, headers: list[str]) -> list[str]: ...
    def records(self, tab: str) -> list[dict[str, str]]: ...
    def column(self, tab: str, header: str) -> list[str]: ...
    def append(self, tab: str, rows: list[dict[str, Any]]) -> None: ...
    def update_cells(self, tab: str, updates: list[tuple[int, str, Any]]) -> None: ...
    def replace_tab(self, tab: str, rows: list[list[Any]], percent_cols: list[int] | None = None) -> None: ...


class GoogleSheetStore:
    """Store backed by a real Google Sheet. Row numbers are 1-based sheet rows."""

    def __init__(self, spreadsheet) -> None:
        self.sh = spreadsheet
        self._ws: dict[str, Any] = {}
        self._hdr: dict[str, list[str]] = {}
        # Read cache: Google allows ~60 reads/minute, and one run reads the same tabs many
        # times. Each tab is read once and re-read only after this program writes to it.
        self._values: dict[str, list[list[str]]] = {}
        self._names_loaded = False

    def _invalidate(self, tab: str) -> None:
        self._values.pop(tab, None)

    @classmethod
    def connect(cls, service_account_info: dict, spreadsheet_id: str) -> "GoogleSheetStore":
        import gspread

        # BackOffHTTPClient waits and retries when Google answers 429 "slow down"
        client = gspread.service_account_from_dict(
            service_account_info, scopes=SCOPES, http_client=gspread.BackOffHTTPClient
        )
        try:
            sh = client.open_by_key(spreadsheet_id)
        except gspread.exceptions.APIError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (403, 404):
                raise RuntimeError(
                    f"Service account {service_account_info.get('client_email')} can't open the sheet "
                    f"(HTTP {code}). Share the sheet with that email as Editor, and check the "
                    "Google Sheets API is enabled in your Cloud project."
                ) from e
            raise
        return cls(sh)

    @property
    def title(self) -> str:
        return self.sh.title

    def _load_names(self) -> None:
        if not self._names_loaded:
            for w in self.sh.worksheets():  # one read for every tab's handle
                self._ws.setdefault(w.title, w)
            self._names_loaded = True

    def _w(self, tab: str):
        self._load_names()
        if tab not in self._ws:
            self._ws[tab] = self.sh.worksheet(tab)  # raises WorksheetNotFound with a clear message
        return self._ws[tab]

    def tab_names(self) -> list[str]:
        self._load_names()
        return list(self._ws)

    def _get_values(self, tab: str) -> list[list[str]]:
        if tab not in self._values:
            self._values[tab] = self._w(tab).get_all_values()
        return self._values[tab]

    def create_tab(self, tab: str, headers: list[str]) -> None:
        ws = self.sh.add_worksheet(title=tab, rows=1000, cols=max(len(headers), 10))
        ws.update(values=[headers], range_name="A1")
        ws.freeze(rows=1)
        ws.format("1:1", {"textFormat": {"bold": True}})
        self._ws[tab] = ws
        self._hdr[tab] = list(headers)
        self._invalidate(tab)

    def headers(self, tab: str) -> list[str]:
        if tab not in self._hdr:
            values = self._get_values(tab)
            self._hdr[tab] = [str(h).strip() for h in values[0]] if values else []
        return self._hdr[tab]

    def add_missing_headers(self, tab: str, headers: list[str]) -> list[str]:
        import gspread.utils as u

        current = self.headers(tab)
        missing = [h for h in headers if h not in current]
        if missing:
            ws = self._w(tab)
            start = len(current) + 1
            need = start + len(missing) - 1
            if need > ws.col_count:
                ws.add_cols(need - ws.col_count)
            ws.update(values=[missing], range_name=u.rowcol_to_a1(1, start))
            self._hdr[tab] = current + missing
            self._invalidate(tab)
        return missing

    def records(self, tab: str) -> list[dict[str, str]]:
        values = self._get_values(tab)
        if not values:
            return []
        hdr = [h.strip() for h in values[0]]
        out = []
        for i, row in enumerate(values[1:], start=2):
            rec = {h: (row[j] if j < len(row) else "") for j, h in enumerate(hdr) if h}
            rec["_row"] = i
            out.append(rec)
        return out

    def column(self, tab: str, header: str) -> list[str]:
        hdr = self.headers(tab)
        if header not in hdr:
            return []
        j = hdr.index(header)
        return [str(r[j]) if j < len(r) else "" for r in self._get_values(tab)[1:]]

    def append(self, tab: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        from gspread.utils import InsertDataOption, ValueInputOption

        hdr = self.headers(tab)
        values = [[to_cell(r.get(h)) for h in hdr] for r in rows]
        self._w(tab).append_rows(
            values,
            value_input_option=ValueInputOption.user_entered,
            insert_data_option=InsertDataOption.insert_rows,
            table_range="A1",
        )
        self._invalidate(tab)

    def update_cells(self, tab: str, updates: list[tuple[int, str, Any]]) -> None:
        if not updates:
            return
        import gspread.utils as u

        hdr = self.headers(tab)
        data = []
        for row, header, value in updates:
            if header not in hdr:
                continue
            data.append(
                {"range": u.rowcol_to_a1(row, hdr.index(header) + 1), "values": [[to_cell(value)]]}
            )
        for i in range(0, len(data), 500):
            self._w(tab).batch_update(
                data[i : i + 500], value_input_option=u.ValueInputOption.user_entered
            )
        self._invalidate(tab)

    def replace_tab(self, tab: str, rows: list[list[Any]], percent_cols: list[int] | None = None) -> None:
        """Overwrite a generated tab. RAW input: text stays text, numbers stay numbers."""
        from gspread.utils import ValueInputOption

        width = max((len(r) for r in rows), default=1)
        values = [[("" if v is None else v) for v in r] + [""] * (width - len(r)) for r in rows]
        if tab not in self.tab_names():
            ws = self.sh.add_worksheet(title=tab, rows=max(len(values) + 20, 100), cols=max(width, 5))
            ws.freeze(rows=1)
            self._ws[tab] = ws
        ws = self._w(tab)
        ws.clear()
        if ws.row_count < len(values) + 5 or ws.col_count < width:
            ws.resize(rows=max(ws.row_count, len(values) + 20), cols=max(ws.col_count, width))
        ws.update(values=values, range_name="A1", value_input_option=ValueInputOption.raw)
        fmts = [{"range": "1:1", "format": {"textFormat": {"bold": True}}}]
        for c in percent_cols or []:
            letter = _col(c + 1)
            fmts.append({"range": f"{letter}2:{letter}", "format": {"numberFormat": {"type": "PERCENT", "pattern": "0%"}}})
        ws.batch_format(fmts)
        self._hdr[tab] = [str(h) for h in values[0]] if values else []
        self._values[tab] = [[str(v) for v in r] for r in values]  # what we just wrote


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def ensure_tabs(store: Store, tabs: dict[str, list[str]]) -> list[str]:
    """Create missing tabs; add missing headers to existing ones. Returns actions taken."""
    actions = []
    existing = set(store.tab_names())
    for tab, headers in tabs.items():
        if tab not in existing:
            store.create_tab(tab, headers)
            actions.append(f"created tab '{tab}'")
        else:
            # Works for an empty hand-made tab too: headers are written from column A.
            added = store.add_missing_headers(tab, headers)
            if added:
                actions.append(f"added columns to '{tab}': {', '.join(added)}")
    return actions


def rows_from_csv(path) -> list[dict[str, str]]:
    import csv

    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def nonempty(records: Iterable[dict[str, str]], header: str) -> list[dict[str, str]]:
    return [r for r in records if str(r.get(header, "")).strip()]
