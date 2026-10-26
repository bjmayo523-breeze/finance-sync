"""The 'Dashboard' tab inside the Google Sheet (the shared, family-facing view).

Built once with `finance-sync init-dashboard`. Everything on it is formulas
and native charts over the generated summary tabs, so it updates by itself
after every sync.

Layout
  B3                 month picker (the KPI cards and both tables follow it)
  A5:G8              this month: 7 cards, each with its change from the month before
  I5:L8              cash right now (from the Cash Position tab)
  A10:G37            spending by category, with Sort by / Order / Show dropdowns and a trend sparkline
  I10:M22            top 10 flexible categories, with Sort by / Order dropdowns and a trend sparkline
  Notes (hover tooltips) on every label and column header explain what each number means.
  Trend sparklines: green when the month is below its 3-month average, red when above.
  row 39 onward      six charts

`init-dashboard --rebuild` deletes and recreates this one tab (it's
program-owned); no other tab is touched.
"""

from __future__ import annotations

from finance_sync.sheets import _col
from finance_sync.summary import (ALLOC, CASH, DETAIL, MONTHLY, MONTHLY_HEADERS, NETWORTH, TRENDS)

TAB = "Dashboard"
ROWS = 200  # chart source ranges cover this many rows, so new months appear automatically
NCOLS = 13  # A..M

# row indexes (0-based)
R_TITLE, R_SUB, R_MONTH = 0, 1, 2
R_SECTION, R_KLABEL, R_KVALUE, R_KDELTA = 4, 5, 6, 7
R_TTITLE, R_CTRL, R_THEAD = 9, 10, 11
R_TFIRST = 12
CAT_ROWS, TOP_ROWS = 25, 10
R_CHARTS_HEAD = R_TFIRST + CAT_ROWS + 1  # 38
R_CHART1 = R_CHARTS_HEAD + 1  # 39
CHART_STEP = 16

MONTH_CELL = "$B$3"
CAT_SORT, CAT_ORDER, CAT_SHOW = "$B$11", "$D$11", "$F$11"
TOP_SORT, TOP_ORDER = "$J$11", "$L$11"

CAT_SORT_OPTIONS = ["Amount", "Change vs avg", "3-month average", "Category A–Z", "Group"]
ORDER_OPTIONS = ["High to low", "Low to high"]
SHOW_OPTIONS = ["All", "Flexible only", "Fixed only"]
TOP_SORT_OPTIONS = ["Amount", "Change vs avg", "Share"]

# KPI card: (label, Monthly Summary header, higher is better?, percent?)
KPIS = [
    ("Total income", "Total Income", True, False),
    ("Spending", "Spending", False, False),
    ("Net cash flow", "Net Cash Flow", True, False),
    ("Savings rate", "Savings Rate", True, True),
    ("Run-rate spending", "Run-rate Spending", False, False),
    ("Fixed bills", "Fixed", False, False),
    ("Flexible", "Flexible", False, False),
]
CASH_COL0 = 8  # column I

NAVY = {"red": 0.184, "green": 0.357, "blue": 0.541}
TEAL = {"red": 0.227, "green": 0.616, "blue": 0.561}
AMBER = {"red": 0.89, "green": 0.639, "blue": 0.231}
RED = {"red": 0.78, "green": 0.25, "blue": 0.2}
GREEN = {"red": 0.16, "green": 0.52, "blue": 0.33}
MUTED = {"red": 0.42, "green": 0.45, "blue": 0.5}
CARD = {"red": 0.953, "green": 0.961, "blue": 0.973}
BAND = {"red": 0.965, "green": 0.973, "blue": 0.98}
WHITE = {"red": 1, "green": 1, "blue": 1}
INK = {"red": 0.12, "green": 0.14, "blue": 0.17}


def _mcol(header: str) -> str:
    return _col(MONTHLY_HEADERS.index(header) + 1)


def _lookup(header: str) -> str:
    c = _mcol(header)
    return f"=IFERROR(INDEX('{MONTHLY}'!{c}:{c}, MATCH({MONTH_CELL}, '{MONTHLY}'!A:A, 0)), \"\")"


def _delta(header: str, pct: bool) -> str:
    c = _mcol(header)
    m = f"MATCH({MONTH_CELL}, '{MONTHLY}'!A:A, 0)"
    expr = f"(INDEX('{MONTHLY}'!{c}:{c}, {m}) - INDEX('{MONTHLY}'!{c}:{c}, {m} - 1))"
    return f"=IFERROR({expr}{' * 100' if pct else ''}, \"\")"


def _cash(header: str) -> str:
    """Expression (no '=') for one Cash Position field."""
    return f"INDEX('{CASH}'!$A$2:$Z$2, MATCH(\"{header}\", '{CASH}'!$A$1:$Z$1, 0))"


def _show(sel: str, fixed_col: str) -> str:
    return (f"((({sel}=\"All\")) + (({sel}=\"Fixed only\") * ({fixed_col}=\"yes\")) "
            f"+ (({sel}=\"Flexible only\") * ({fixed_col}<>\"yes\")))")


def category_formula() -> str:
    d = f"'{DETAIL}'!"
    arr = f"{{{d}B2:B, {d}C2:C, {d}D2:D, {d}E2:E, {d}G2:G, {d}H2:H}}"
    key = (f"SWITCH({CAT_SORT}, \"Amount\", 3, \"Change vs avg\", 5, \"3-month average\", 4, "
           f"\"Category A–Z\", 1, \"Group\", 2, 3)")
    asc = f"IF(OR({CAT_SORT}=\"Category A–Z\", {CAT_SORT}=\"Group\"), TRUE, {CAT_ORDER}=\"Low to high\")"
    cond = f"({d}A2:A={MONTH_CELL}) * {_show(CAT_SHOW, d + 'I2:I')}"
    return (f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({arr}, {cond}), {key}, {asc}, 3, FALSE), {CAT_ROWS}, 6), "
            f"\"No spending for this selection\")")


def top_flexible_formula() -> str:
    """The selected month's 10 largest flexible categories, then sorted by the chosen column."""
    d = f"'{DETAIL}'!"
    arr = f"{{{d}B2:B, {d}D2:D, {d}G2:G, {d}H2:H}}"  # Category, Amount, Change vs Avg %, Share
    cond = f"({d}A2:A={MONTH_CELL}) * ({d}I2:I<>\"yes\") * ({d}D2:D>0)"
    top = f"ARRAY_CONSTRAIN(SORT(FILTER({arr}, {cond}), 2, FALSE), {TOP_ROWS}, 4)"
    key = f"SWITCH({TOP_SORT}, \"Amount\", 2, \"Change vs avg\", 3, \"Share\", 4, 2)"
    return (f"=IFERROR(SORT({top}, {key}, {TOP_ORDER}=\"Low to high\"), "
            f"\"No flexible spending this month\")")


GREEN_HEX, RED_HEX, GRAY_HEX = "#2E8B57", "#C8553D", "#9AA5B1"


def sparkline_formula(row: int, cat_col: str = "A", vs_col: str = "E") -> str:
    """Trend of one category (named in cat_col of this row) across every month.

    Colored by the row's change vs its 3-month average: 5%+ below is green (spending less),
    5%+ above is red, otherwise (or no average yet) gray.
    """
    tr = f"'{TRENDS}'!"
    a, vs = f"${cat_col}{row + 1}", f"${vs_col}{row + 1}"
    series = f"FILTER(INDEX({tr}$B$2:$AZ${ROWS}, 0, MATCH({a}, {tr}$B$1:$AZ$1, 0)), {tr}$A$2:$A${ROWS}<>\"\")"
    color = (f"IF(NOT(ISNUMBER({vs})), \"{GRAY_HEX}\", IF({vs}<=-0.05, \"{GREEN_HEX}\", "
             f"IF({vs}>=0.05, \"{RED_HEX}\", \"{GRAY_HEX}\")))")
    return (f"=IF({a}=\"\", \"\", IFERROR(SPARKLINE({series}, "
            f"{{\"charttype\", \"column\"; \"color\", {color}}}), \"\"))")


def cell_values(default_month: str) -> list[list]:
    """Values/formulas for A1:M38 (written USER_ENTERED)."""
    grid = [[""] * NCOLS for _ in range(R_CHARTS_HEAD + 1)]
    grid[R_TITLE][0] = "Household Finances"
    grid[R_SUB][0] = ("Updates after every sync. Transfers, card payments and reimbursed work meals never count as "
                      "spending. Run-rate spending leaves out one-off purchases.")
    grid[R_MONTH][0] = "Month"
    grid[R_MONTH][1] = "'" + default_month if default_month else ""  # apostrophe keeps "2026-09" as text
    grid[R_MONTH][2] = (f"=IF({_lookup('Partial')[1:]}=\"yes\", \"Partial month: still in progress\", \"\")")

    grid[R_SECTION][0] = "This month"
    grid[R_SECTION][CASH_COL0] = "Cash right now"
    for i, (label, header, _good, pct) in enumerate(KPIS):
        grid[R_KLABEL][i] = label
        grid[R_KVALUE][i] = _lookup(header)
        grid[R_KDELTA][i] = _delta(header, pct)

    c = CASH_COL0
    grid[R_KLABEL][c:c + 4] = ["Spendable cash", "Cards owed", "Free after this month", "Next paycheck (est.)"]
    grid[R_KVALUE][c] = f"=IFERROR({_cash('Spendable Cash')}, \"\")"
    grid[R_KVALUE][c + 1] = f"=IFERROR({_cash('Card Balances')}, \"\")"
    grid[R_KVALUE][c + 2] = f"=IFERROR({_cash('Free Cash After Month')}, \"\")"
    grid[R_KVALUE][c + 3] = f"=IFERROR(TEXT(DATEVALUE({_cash('Next Paycheck (est.)')}), \"mmm d\"), \"\")"
    grid[R_KDELTA][c] = f"=IFERROR(\"as of \" & TEXT(DATEVALUE({_cash('As Of')}), \"mmm d\"), \"\")"
    grid[R_KDELTA][c + 1] = "paid in full each month"
    grid[R_KDELTA][c + 2] = (f"=IFERROR(\"after \" & TEXT({_cash('Expected Rest of Month')}, \"$#,##0\") & "
                             f"\" more spending\", \"\")")
    grid[R_KDELTA][c + 3] = (f"=IFERROR(\"last: \" & TEXT({_cash('Last Paycheck')}, \"$#,##0\") & \" on \" & "
                             f"TEXT(DATEVALUE({_cash('Last Paycheck Date')}), \"mmm d\"), \"\")")

    grid[R_TTITLE][0] = "Spending by category"
    grid[R_TTITLE][8] = "Top 10 flexible categories"
    grid[R_CTRL][0:6] = ["Sort by", "Amount", "Order", "High to low", "Show", "All"]
    grid[R_CTRL][8:12] = ["Sort by", "Amount", "Order", "High to low"]
    grid[R_THEAD][0:7] = ["Category", "Group", "Amount", "3-mo avg", "vs avg", "Share", "Trend (all months)"]
    grid[R_THEAD][8:13] = ["Category", "Amount", "vs avg", "Share", "Trend"]
    grid[R_TFIRST][0] = category_formula()
    grid[R_TFIRST][8] = top_flexible_formula()
    for r in range(R_TFIRST, R_TFIRST + CAT_ROWS):
        grid[r][6] = sparkline_formula(r)
    for r in range(R_TFIRST, R_TFIRST + TOP_ROWS):
        grid[r][12] = sparkline_formula(r, "I", "K")
    grid[R_CHARTS_HEAD][0] = "Trends and investments"
    return grid


# ------------------------------------------------------------------ requests
def _gr(sheet_id: int, c0: int, c1: int, r0: int = 0, r1: int = ROWS) -> dict:
    return {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}


def _src(gr: dict) -> dict:
    return {"sourceRange": {"sources": [gr]}}


def _pos(sheet_id: int, row: int, col: int, w: int, h: int = 300) -> dict:
    return {"overlayPosition": {"anchorCell": {"sheetId": sheet_id, "rowIndex": row, "columnIndex": col},
                                "widthPixels": w, "heightPixels": h}}


def _basic(title: str, kind: str, domain: dict, series: list[tuple[dict, str, dict]], dash_id: int, row: int,
           col: int, width: int, legend: str = "BOTTOM_LEGEND") -> dict:
    spec = {
        "title": title,
        "titleTextFormat": {"bold": True, "fontSize": 12, "foregroundColorStyle": {"rgbColor": INK}},
        "basicChart": {
            "chartType": kind,
            "legendPosition": legend,
            "headerCount": 1,
            "axis": [{"position": "BOTTOM_AXIS"}, {"position": "LEFT_AXIS"}],
            "domains": [{"domain": _src(domain)}],
            # horizontal BAR charts measure along the bottom axis; everything else along the left
            "series": [{"series": _src(g), "targetAxis": "BOTTOM_AXIS" if kind == "BAR" else "LEFT_AXIS",
                        "colorStyle": {"rgbColor": color}, **({"type": t} if t else {})} for g, t, color in series],
        },
    }
    return {"addChart": {"chart": {"spec": spec, "position": _pos(dash_id, row, col, width)}}}


LEFT_W, RIGHT_W = 800, 640


def chart_requests(ids: dict[str, int], dash_id: int) -> list[dict]:
    m, tr, nw, al = ids[MONTHLY], ids[TRENDS], ids[NETWORTH], ids[ALLOC]
    mc = MONTHLY_HEADERS.index
    r1, r2, r3 = R_CHART1, R_CHART1 + CHART_STEP, R_CHART1 + 2 * CHART_STEP
    palette = [NAVY, TEAL, AMBER, RED, MUTED, GREEN]
    return [
        _basic("Income vs spending by month", "COMBO", _gr(m, 0, 1),
               [(_gr(m, mc("Total Income"), mc("Total Income") + 1), "COLUMN", TEAL),
                (_gr(m, mc("Spending"), mc("Spending") + 1), "COLUMN", NAVY),
                (_gr(m, mc("Net Cash Flow"), mc("Net Cash Flow") + 1), "LINE", AMBER)], dash_id, r1, 0, LEFT_W),
        _basic("Run-rate spending vs salary", "COMBO", _gr(m, 0, 1),
               [(_gr(m, mc("Run-rate Spending"), mc("Run-rate Spending") + 1), "COLUMN", NAVY),
                (_gr(m, mc("Salary"), mc("Salary") + 1), "LINE", RED)], dash_id, r1, CASH_COL0, RIGHT_W),
        _basic("Largest flexible categories over time", "LINE", _gr(tr, 0, 1),
               [(_gr(tr, c, c + 1), "", palette[c - 1]) for c in range(1, 7)], dash_id, r2, 0, LEFT_W),
        _basic("Selected month by category (follows the table)", "BAR",
               _gr(dash_id, 0, 1, R_THEAD, R_TFIRST + CAT_ROWS),
               [(_gr(dash_id, 2, 3, R_THEAD, R_TFIRST + CAT_ROWS), "", NAVY)], dash_id, r2, CASH_COL0, RIGHT_W,
               legend="NO_LEGEND"),
        _basic("Net worth", "LINE", _gr(nw, 0, 1),
               [(_gr(nw, 7, 8), "", NAVY), (_gr(nw, 8, 9), "", TEAL)], dash_id, r3, 0, LEFT_W),
        _basic("Top holdings by market value", "BAR", _gr(al, 0, 1, 0, 13),
               [(_gr(al, 2, 3, 0, 13), "", NAVY)], dash_id, r3, CASH_COL0, RIGHT_W, legend="NO_LEGEND"),
    ]


def _rng(dash_id, r0, r1, c0, c1):
    return {"sheetId": dash_id, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}


def _fmt(r, f, fields):
    return {"repeatCell": {"range": r, "cell": {"userEnteredFormat": f}, "fields": f"userEnteredFormat({fields})"}}


def _num(pattern: str, kind: str = "NUMBER") -> dict:
    return {"numberFormat": {"type": kind, "pattern": pattern}}


def _text(size=10, bold=False, color=INK, italic=False) -> dict:
    return {"textFormat": {"fontSize": size, "bold": bold, "italic": italic, "foregroundColorStyle": {"rgbColor": color}}}


def _widths(dash_id: int) -> list[dict]:
    px = [150, 120, 110, 110, 100, 100, 130, 22, 190, 130, 110, 110, 120]
    return [{"updateDimensionProperties": {"range": {"sheetId": dash_id, "dimension": "COLUMNS", "startIndex": i,
                                                     "endIndex": i + 1}, "properties": {"pixelSize": p},
                                           "fields": "pixelSize"}} for i, p in enumerate(px)]


def _height(dash_id: int, row: int, px: int) -> dict:
    return {"updateDimensionProperties": {"range": {"sheetId": dash_id, "dimension": "ROWS", "startIndex": row,
                                                    "endIndex": row + 1}, "properties": {"pixelSize": px},
                                          "fields": "pixelSize"}}


def format_requests(dash_id: int) -> list[dict]:
    R = lambda r0, r1, c0, c1: _rng(dash_id, r0, r1, c0, c1)  # noqa: E731
    money = _num("$#,##0", "CURRENCY")
    signed_money = _num("+$#,##0;-$#,##0;$0", "CURRENCY")
    kpi_cols, cash_cols = (0, len(KPIS)), (CASH_COL0, CASH_COL0 + 4)
    pct_i = [i for i, k in enumerate(KPIS) if k[3]]
    reqs = [
        {"updateSheetProperties": {"properties": {"sheetId": dash_id, "gridProperties": {
            "hideGridlines": True, "frozenRowCount": R_MONTH + 1}},
            "fields": "gridProperties.hideGridlines,gridProperties.frozenRowCount"}},
        *_widths(dash_id),
        _height(dash_id, R_TITLE, 44), _height(dash_id, R_MONTH + 1, 8), _height(dash_id, R_KVALUE, 40), _height(dash_id, R_KDELTA, 30),
        _height(dash_id, R_KDELTA + 1, 12), _height(dash_id, R_TTITLE, 30), _height(dash_id, R_CHARTS_HEAD, 34),
        # whole sheet: base font, vertical centering
        _fmt(R(0, R_CHARTS_HEAD + 1, 0, NCOLS), {**_text(10), "verticalAlignment": "MIDDLE"}, "textFormat,verticalAlignment"),
        _fmt(R(R_TITLE, R_TITLE + 1, 0, 1), _text(20, True, NAVY), "textFormat"),
        _fmt(R(R_SUB, R_SUB + 1, 0, 1), _text(9, False, MUTED, True), "textFormat"),
        _fmt(R(R_MONTH, R_MONTH + 1, 0, 1), {**_text(10, True, MUTED), "horizontalAlignment": "RIGHT"},
             "textFormat,horizontalAlignment"),
        _fmt(R(R_MONTH, R_MONTH + 1, 1, 2), {**_text(12, True, NAVY), "backgroundColorStyle": {"rgbColor": CARD},
                                             "horizontalAlignment": "CENTER"},
             "textFormat,backgroundColorStyle,horizontalAlignment"),
        _fmt(R(R_MONTH, R_MONTH + 1, 2, 3), _text(9, True, AMBER), "textFormat"),
        # section headings
        *[_fmt(R(r, r + 1, c0, c1), _text(12, True, NAVY), "textFormat")
          for r, c0, c1 in ((R_SECTION, 0, 7), (R_SECTION, CASH_COL0, NCOLS), (R_TTITLE, 0, 7),
                            (R_TTITLE, CASH_COL0, NCOLS), (R_CHARTS_HEAD, 0, NCOLS))],
        # cards: background, label/value/delta text
        *[_fmt(R(R_KLABEL, R_KDELTA + 1, c0, c1), {"backgroundColorStyle": {"rgbColor": CARD}}, "backgroundColorStyle")
          for c0, c1 in (kpi_cols, cash_cols)],
        *[_fmt(R(R_KLABEL, R_KLABEL + 1, c0, c1), _text(9, True, MUTED), "textFormat") for c0, c1 in (kpi_cols, cash_cols)],
        *[_fmt(R(R_KVALUE, R_KVALUE + 1, c0, c1), {**_text(18, True, INK), **money, "horizontalAlignment": "LEFT"},
               "textFormat,numberFormat,horizontalAlignment") for c0, c1 in (kpi_cols, cash_cols)],
        *[_fmt(R(R_KDELTA, R_KDELTA + 1, c0, c1), {**_text(9, False, MUTED), "horizontalAlignment": "LEFT",
                                                    "wrapStrategy": "WRAP", "verticalAlignment": "TOP"},
               "textFormat,horizontalAlignment,wrapStrategy,verticalAlignment") for c0, c1 in (kpi_cols, cash_cols)],
        _fmt(R(R_KDELTA, R_KDELTA + 1, *kpi_cols), signed_money, "numberFormat"),
        *[_fmt(R(R_KVALUE, R_KVALUE + 1, i, i + 1), _num("0%", "PERCENT"), "numberFormat") for i in pct_i],
        *[_fmt(R(R_KDELTA, R_KDELTA + 1, i, i + 1), _num('+0" pts";-0" pts";"0 pts"'), "numberFormat") for i in pct_i],
        *[{"updateBorders": {"range": R(R_KLABEL, R_KDELTA + 1, c0, c1),
                             "innerVertical": {"style": "SOLID_THICK", "colorStyle": {"rgbColor": WHITE}}}}
          for c0, c1 in (kpi_cols, cash_cols)],
        # table controls
        *[_fmt(R(R_CTRL, R_CTRL + 1, c, c + 1), {**_text(9, True, MUTED), "horizontalAlignment": "RIGHT"},
               "textFormat,horizontalAlignment") for c in (0, 2, 4, 8, 10)],
        *[_fmt(R(R_CTRL, R_CTRL + 1, c, c + 1), {**_text(10, True, NAVY), "backgroundColorStyle": {"rgbColor": CARD}},
               "textFormat,backgroundColorStyle") for c in (1, 3, 5, 9, 11)],
        # table headers and numbers
        *[_fmt(R(R_THEAD, R_THEAD + 1, c0, c1), _text(10, True, WHITE), "textFormat")
          for c0, c1 in ((0, 7), (CASH_COL0, NCOLS))],
        _fmt(R(R_TFIRST, R_TFIRST + CAT_ROWS, 2, 4), money, "numberFormat"),
        _fmt(R(R_TFIRST, R_TFIRST + CAT_ROWS, 4, 5), _num("+0%;-0%;0%", "PERCENT"), "numberFormat"),
        _fmt(R(R_TFIRST, R_TFIRST + CAT_ROWS, 5, 6), _num("0%", "PERCENT"), "numberFormat"),
        _fmt(R(R_TFIRST, R_TFIRST + TOP_ROWS, 9, 10), money, "numberFormat"),
        _fmt(R(R_TFIRST, R_TFIRST + TOP_ROWS, 10, 11), _num("+0%;-0%;0%", "PERCENT"), "numberFormat"),
        _fmt(R(R_TFIRST, R_TFIRST + TOP_ROWS, 11, 12), _num("0%", "PERCENT"), "numberFormat"),
        _fmt(R(R_THEAD, R_TFIRST + CAT_ROWS, 2, 6), {"horizontalAlignment": "RIGHT"}, "horizontalAlignment"),
        _fmt(R(R_THEAD, R_TFIRST + TOP_ROWS, 9, 12), {"horizontalAlignment": "RIGHT"}, "horizontalAlignment"),
        *[{"addBanding": {"bandedRange": {"range": R(R_THEAD, R_TFIRST + n, c0, c1), "rowProperties": {
            "headerColorStyle": {"rgbColor": NAVY}, "firstBandColorStyle": {"rgbColor": WHITE},
            "secondBandColorStyle": {"rgbColor": BAND}}}}}
          for n, c0, c1 in ((CAT_ROWS, 0, 7), (TOP_ROWS, CASH_COL0, NCOLS))],
        {"updateBorders": {"range": R(R_CHARTS_HEAD, R_CHARTS_HEAD + 1, 0, NCOLS),
                           "bottom": {"style": "SOLID", "colorStyle": {"rgbColor": NAVY}}}},
    ]
    return reqs


def _cond(dash_id, rng: dict, kind: str, value: str, color: dict, bold: bool = True) -> dict:
    return {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [rng],
        "booleanRule": {"condition": {"type": kind, "values": [{"userEnteredValue": value}]},
                        "format": {"textFormat": {"bold": bold, "foregroundColorStyle": {"rgbColor": color}}}}}}}


def conditional_requests(dash_id: int) -> list[dict]:
    R = lambda r0, r1, c0, c1: _rng(dash_id, r0, r1, c0, c1)  # noqa: E731
    out = []
    for i, (_label, _h, up_good, _pct) in enumerate(KPIS):
        cell = R(R_KDELTA, R_KDELTA + 1, i, i + 1)
        out.append(_cond(dash_id, cell, "NUMBER_GREATER", "0", GREEN if up_good else RED))
        out.append(_cond(dash_id, cell, "NUMBER_LESS", "0", RED if up_good else GREEN))
    net = [k[1] for k in KPIS].index("Net Cash Flow")
    out.append(_cond(dash_id, R(R_KVALUE, R_KVALUE + 1, net, net + 1), "NUMBER_LESS", "0", RED))
    out.append(_cond(dash_id, R(R_KVALUE, R_KVALUE + 1, CASH_COL0 + 2, CASH_COL0 + 3), "NUMBER_LESS", "0", RED))
    for vs in (R(R_TFIRST, R_TFIRST + CAT_ROWS, 4, 5), R(R_TFIRST, R_TFIRST + TOP_ROWS, 10, 11)):
        out.append(_cond(dash_id, vs, "NUMBER_GREATER_THAN_EQ", "0.15", RED))
        out.append(_cond(dash_id, vs, "NUMBER_LESS_THAN_EQ", "-0.15", GREEN))
    return out


def _list_rule(dash_id: int, row: int, col: int, options: list[str]) -> dict:
    return {"setDataValidation": {
        "range": _rng(dash_id, row, row + 1, col, col + 1),
        "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": o} for o in options]},
                 "strict": True, "showCustomUi": True}}}


def validation_requests(dash_id: int) -> list[dict]:
    return [
        {"setDataValidation": {
            "range": _rng(dash_id, R_MONTH, R_MONTH + 1, 1, 2),
            "rule": {"condition": {"type": "ONE_OF_RANGE",
                                   "values": [{"userEnteredValue": f"='{MONTHLY}'!$A$2:$A${ROWS}"}]},
                     "strict": True, "showCustomUi": True}}},
        _list_rule(dash_id, R_CTRL, 1, CAT_SORT_OPTIONS),
        _list_rule(dash_id, R_CTRL, 3, ORDER_OPTIONS),
        _list_rule(dash_id, R_CTRL, 5, SHOW_OPTIONS),
        _list_rule(dash_id, R_CTRL, 9, TOP_SORT_OPTIONS),
        _list_rule(dash_id, R_CTRL, 11, ORDER_OPTIONS),
    ]


TREND_NOTE = ("Monthly amounts for this category, oldest to newest (the last bar may be a month still in progress). "
              "Green: the selected month is 5% or more below its 3-month average. Red: 5% or more above. "
              "Gray: about the same, or no history yet.")
VS_NOTE = "Change vs the 3-month average. Red text: 15% or more above it. Green text: 15% or more below it."
DELTA_NOTE = "Change from the month before. Green is better, red is worse."

KPI_NOTES = {
    "Total Income": "Salary + RSU/equity income + bonus + other income received this month. Card payments, transfers "
                    "and work-meal reimbursements don't count.",
    "Spending": "Everything spent this month, net of refunds. Transfers, card payments, investments and reimbursed "
                "work meals are left out.",
    "Net Cash Flow": "Total income minus spending. Negative means more went out than came in.",
    "Savings Rate": "Net cash flow divided by total income. The line below shows the change in percentage points.",
    "Run-rate Spending": "Spending minus one-off purchases (big buys from merchants not seen in the 2 months before). "
                         "The best gauge of a normal month.",
    "Fixed": "Mortgage, auto payment, student loan, insurance, utilities, internet and phone.",
    "Flexible": "Everything that isn't a fixed bill: food, shopping, entertainment and so on. This is where cuts "
                "are possible.",
}
CASH_NOTES = [
    "Checking balance, lowered by any pending debits.",
    "Current balances on all credit cards. They're paid in full each month, so this money is already spoken for.",
    "Spendable cash minus cards owed minus spending still expected this month (a normal month's run-rate spending "
    "minus what's been spent so far). The next paycheck isn't counted: it pays for next month.",
    "Estimated from the last paycheck. Pay lands near month-end, so this is the last weekday of the month.",
]
LEFT_HEAD_NOTES = [
    "Spending category, for the month picked in B3.",
    "The broader group the category belongs to.",
    "Spent in this category in the selected month.",
    "Average for this category over the 3 complete months before the selected one.",
    VS_NOTE,
    "Share of the month's total spending.",
    TREND_NOTE,
]
RIGHT_HEAD_NOTES = [
    "Flexible categories only: the spending you can actually change. Fixed bills are left out.",
    "Spent in this category in the selected month.",
    VS_NOTE,
    "Share of the month's total spending.",
    TREND_NOTE,
]


def _note(dash_id: int, row: int, col: int, text: str) -> dict:
    return {"updateCells": {"range": _rng(dash_id, row, row + 1, col, col + 1),
                            "rows": [{"values": [{"note": text}]}], "fields": "note"}}


def note_requests(dash_id: int) -> list[dict]:
    """Hover notes: the Sheets version of tooltips."""
    out = [_note(dash_id, R_MONTH, 1, "Pick a month. The 'This month' cards and both tables follow it. "
                                      "A partial month is still in progress.")]
    for i, (_label, header, _good, _pct) in enumerate(KPIS):
        out.append(_note(dash_id, R_KLABEL, i, KPI_NOTES[header]))
        out.append(_note(dash_id, R_KDELTA, i, DELTA_NOTE))
    for i, text in enumerate(CASH_NOTES):
        out.append(_note(dash_id, R_KLABEL, CASH_COL0 + i, text))
    for i, text in enumerate(LEFT_HEAD_NOTES):
        out.append(_note(dash_id, R_THEAD, i, text))
    for i, text in enumerate(RIGHT_HEAD_NOTES):
        out.append(_note(dash_id, R_THEAD, CASH_COL0 + i, text))
    out += [
        _note(dash_id, R_CTRL, 1, "Sort by amount, change vs the 3-month average, the average itself, "
                                  "category name, or group."),
        _note(dash_id, R_CTRL, 3, "High to low or low to high. Name and group sorts are always A to Z."),
        _note(dash_id, R_CTRL, 5, "All categories, flexible only, or fixed bills only."),
        _note(dash_id, R_CTRL, 9, "Sort the 10 largest flexible categories by amount, change vs average, or share."),
        _note(dash_id, R_CTRL, 11, "High to low or low to high."),
        _note(dash_id, R_TTITLE, CASH_COL0, "The 10 largest non-fixed categories this month: the best places to "
                                            "look for savings."),
    ]
    return out


def latest_complete_month(store) -> str:
    rows = store.records(MONTHLY)
    complete = [r["Month"] for r in rows if r.get("Month") and not r.get("Partial")]
    if complete:
        return complete[-1]
    return rows[-1]["Month"] if rows else ""


def all_requests(ids: dict[str, int], dash_id: int) -> list[dict]:
    return [*validation_requests(dash_id), *format_requests(dash_id), *conditional_requests(dash_id),
            *note_requests(dash_id), *chart_requests(ids, dash_id)]


def build(store, rebuild: bool = False) -> str:
    """Create the Dashboard tab on a GoogleSheetStore. Returns a status line."""
    from gspread.utils import ValueInputOption

    sh = store.sh
    names = {w.title: w for w in sh.worksheets()}
    if TAB in names:
        if not rebuild:
            return "Dashboard tab already exists (use --rebuild to recreate it)."
        sh.del_worksheet(names[TAB])
        names.pop(TAB)
    for needed in (MONTHLY, DETAIL, TRENDS, NETWORTH, ALLOC, CASH):
        if needed not in names:
            raise RuntimeError(f"Summary tab '{needed}' is missing. Run: finance-sync summarize")
    month = latest_complete_month(store)
    ws = sh.add_worksheet(title=TAB, rows=ROWS, cols=NCOLS, index=0)
    ws.update(values=cell_values(month), range_name="A1", value_input_option=ValueInputOption.user_entered)
    ids = {w.title: w.id for w in sh.worksheets()}
    sh.batch_update({"requests": all_requests(ids, ws.id)})
    store._ws.pop(TAB, None)
    store._names_loaded = False
    return f"Built the Dashboard tab: sortable tables, hover notes, cash cards and 6 charts (showing {month or 'no data yet'})."
