import json

from finance_sync import sheet_dashboard as D
from finance_sync.summary import ALLOC, CASH_HEADERS, MONTHLY, MONTHLY_HEADERS, NETWORTH, TRENDS

IDS = {MONTHLY: 11, TRENDS: 12, NETWORTH: 13, ALLOC: 14}


def test_cells_reference_right_columns():
    g = D.cell_values("2026-09")
    assert g[2][1] == "'2026-09"  # month picker in B3
    labels = g[D.R_KLABEL][:len(D.KPIS)]
    assert labels == [k[0] for k in D.KPIS] and labels[-1] == "Flexible"
    spend = g[D.R_KVALUE][1]
    assert D._mcol("Spending") == "G" and f"'{MONTHLY}'!G:G" in spend and "$B$3" in spend
    assert MONTHLY_HEADERS[ord(D._mcol("Savings Rate")) - 65] == "Savings Rate"
    assert "* 100" in g[D.R_KDELTA][3] and "- 1)" in g[D.R_KDELTA][3]  # savings rate change in points
    for c in range(D.CASH_COL0, D.CASH_COL0 + 4):
        assert "'Cash Position'" in g[D.R_KVALUE][c]
    for h in ("Spendable Cash", "Card Balances", "Free Cash After Month", "Next Paycheck (est.)", "As Of",
              "Expected Rest of Month", "Last Paycheck", "Last Paycheck Date"):
        assert h in CASH_HEADERS


def test_tables_follow_controls():
    g = D.cell_values("2026-09")
    cat, top = g[D.R_TFIRST][0], g[D.R_TFIRST][8]
    assert "'Month Detail'!A2:A=$B$3" in cat and "SORT(FILTER(" in cat
    assert "$B$11" in cat and "$D$11" in cat and "$F$11" in cat
    for opt in D.CAT_SORT_OPTIONS + D.SHOW_OPTIONS[1:]:
        assert f'"{opt}"' in cat, opt
    # right table: flexible categories only, top 10 by amount, then the chosen sort
    assert "'Month Detail'!A2:A=$B$3" in top and "'Month Detail'!I2:I<>\"yes\"" in top
    assert f", {D.TOP_ROWS}, 4)" in top and "$J$11" in top and "$L$11" in top
    for opt in D.TOP_SORT_OPTIONS:
        assert f'"{opt}"' in top
    assert "Merchants" not in top
    assert g[D.R_TTITLE][8] == "Top 10 flexible categories"
    assert g[D.R_CTRL][1] in D.CAT_SORT_OPTIONS and g[D.R_CTRL][3] in D.ORDER_OPTIONS
    assert g[D.R_CTRL][5] in D.SHOW_OPTIONS and g[D.R_CTRL][9] in D.TOP_SORT_OPTIONS and g[D.R_CTRL][11] in D.ORDER_OPTIONS
    spark = g[D.R_TFIRST + 3][6]
    assert "SPARKLINE" in spark and f"$A{D.R_TFIRST + 4}" in spark and f"$E{D.R_TFIRST + 4}" in spark
    assert all(row[6].startswith("=IF(") for row in g[D.R_TFIRST:D.R_TFIRST + D.CAT_ROWS])
    rspark = g[D.R_TFIRST][12]
    assert f"$I{D.R_TFIRST + 1}" in rspark and f"$K{D.R_TFIRST + 1}" in rspark
    assert all(row[12].startswith("=IF(") for row in g[D.R_TFIRST:D.R_TFIRST + D.TOP_ROWS])
    for f in (cat, top, spark, rspark):
        assert f.count("(") == f.count(")") and f.count("{") == f.count("}")


def test_sparkline_colors_follow_change_vs_average():
    f = D.sparkline_formula(12)
    assert D.GREEN_HEX in f and D.RED_HEX in f and D.GRAY_HEX in f
    assert "ISNUMBER($E13)" in f  # no average yet -> gray, not red
    assert "$E13<=-0.05" in f and "$E13>=0.05" in f


def test_notes_cover_labels_and_headers():
    notes = D.note_requests(99)
    cells = {(n["updateCells"]["range"]["startRowIndex"], n["updateCells"]["range"]["startColumnIndex"])
             for n in notes}
    for i in range(len(D.KPIS)):
        assert (D.R_KLABEL, i) in cells and (D.R_KDELTA, i) in cells
    for c in range(D.CASH_COL0, D.CASH_COL0 + 4):
        assert (D.R_KLABEL, c) in cells
    for c in list(range(7)) + list(range(D.CASH_COL0, D.CASH_COL0 + 5)):
        assert (D.R_THEAD, c) in cells
    assert (D.R_MONTH, 1) in cells
    assert all(n["updateCells"]["fields"] == "note" and n["updateCells"]["rows"][0]["values"][0]["note"] for n in notes)


def test_requests_are_valid_json_and_point_at_tabs():
    reqs = D.all_requests(IDS, 99)
    json.dumps(reqs)
    charts = [r["addChart"]["chart"] for r in reqs if "addChart" in r]
    assert len(charts) == 6
    combo = charts[0]["spec"]["basicChart"]
    assert combo["chartType"] == "COMBO" and combo["series"][2]["type"] == "LINE"
    assert combo["domains"][0]["domain"]["sourceRange"]["sources"][0]["sheetId"] == 11
    spend = combo["series"][1]["series"]["sourceRange"]["sources"][0]
    assert MONTHLY_HEADERS[spend["startColumnIndex"]] == "Spending"
    salary = charts[1]["spec"]["basicChart"]["series"][1]["series"]["sourceRange"]["sources"][0]
    assert MONTHLY_HEADERS[salary["startColumnIndex"]] == "Salary"
    assert charts[5]["spec"]["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"][0]["sheetId"] == 14
    validations = [r for r in reqs if "setDataValidation" in r]
    assert len(validations) == 6
    bands = [r for r in reqs if "addBanding" in r]
    assert len(bands) == 2
    conds = [r for r in reqs if "addConditionalFormatRule" in r]
    assert len(conds) == 2 * len(D.KPIS) + 6
    assert len([r for r in reqs if "updateCells" in r]) == len(D.note_requests(99))


def test_bar_chart_targets_bottom_axis():
    for r in D.chart_requests(IDS, 99):
        b = r["addChart"]["chart"]["spec"]["basicChart"]
        want = "BOTTOM_AXIS" if b["chartType"] == "BAR" else "LEFT_AXIS"
        assert all(s["targetAxis"] == want for s in b["series"]), b["chartType"]


def test_charts_fit_their_columns():
    widths = [r["updateDimensionProperties"]["properties"]["pixelSize"] for r in D._widths(1)]
    assert D.LEFT_W <= sum(widths[:7]) and D.RIGHT_W <= sum(widths[D.CASH_COL0:])
