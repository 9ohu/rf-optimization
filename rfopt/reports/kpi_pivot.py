"""The pivot workbook the R5 team emails.

One sheet per KPI, a row per cell, a column per hour — the layout the team
already reads, down to the banner text, the blue header and the colours:

* row 1  a merged dark-blue banner, "Average of <KPI> - <range> - Hourly ..."
* row 2  ``Row Labels | Cell FDD TDD Indication | <hour> | <hour> ...``
* body   the hourly means, heat-mapped

The colour rules are the ones the user settled on after two rounds of
corrections, and they matter:

* the gradient uses **fixed numeric anchors tied to the KPI's meaning**, not
  percentiles of whatever happens to be in the sheet. A cell sitting at 1.85%
  PRB is fine, and a relative scale painted it red for being the row minimum.
* on top of that, a KPI with a stated threshold gets a hard flag (dark red,
  white bold) so a real breach is unmistakable. The flag rule is added
  **before** the gradient — xlsxwriter gives earlier rules higher precedence,
  and with ``stop_if_true`` the gradient would otherwise repaint the breach.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

_BANNER_FILL = "#305496"
_HEADER_FILL = "#4472C4"
_FLAG_FILL = "#C00000"
_GREEN, _YELLOW, _RED = "#63BE7B", "#FFEB84", "#F8696B"


@dataclass(frozen=True)
class SheetSpec:
    """How one KPI is presented: sheet name, colour anchors, issue rule."""
    sheet: str
    anchors: tuple[float, str, float, str, float, str] | None = None
    issue: tuple[str, float] | None = None          # (criteria, value)

    @property
    def issue_text(self) -> str:
        if not self.issue:
            return ""
        crit, val = self.issue
        return f"{crit} {val:g}"


# the five the team's own workbook carries, with the colours and thresholds
# they dictated. Keyed by the operator's exact column name.
SPECS: dict[str, SheetSpec] = {
    "L.UL.Interference.Avg(dBm)": SheetSpec(
        "UL Inter", (-140, _GREEN, -100, _YELLOW, -60, _RED), (">", -105)),
    "4G Data Volume (GB)": SheetSpec("Data_Volume_GB"),
    "LTE_Availability(%)@AB": SheetSpec(
        "LTE_Availability", (0, _RED, 50, _YELLOW, 100, _GREEN), ("<", 100)),
    "HW_DL PRB Avg Utilization(%)": SheetSpec(
        "DL_PRB_Utilization", (0, _GREEN, 50, _YELLOW, 100, _RED), (">", 80)),
    "4G DL User Throughput mbps_Asiacell": SheetSpec("DL_Throughput"),
}

# a KPI the team has not pinned down yet: colour it by which way is better,
# read off the canonical schema rather than guessed from the name
_UP_RELATIVE = (_RED, _YELLOW, _GREEN)      # low is bad
_DOWN_RELATIVE = (_GREEN, _YELLOW, _RED)    # high is bad


def _direction(kpi: str) -> str:
    from rfopt.ingest.hourly_kpi import _KPI_MAP, _KPI_MAP_3G, _norm
    from rfopt.ingest.schema import kpi_def

    n = _norm(kpi)
    for table, tech in ((_KPI_MAP, "LTE"), (_KPI_MAP_3G, "UMTS")):
        if n in table:
            d = kpi_def(table[n], tech)
            if d is not None:
                return d.direction
    return "info"


def spec_for(kpi: str, used: set[str] | None = None) -> SheetSpec:
    spec = SPECS.get(kpi)
    if spec is not None:
        return spec
    return SheetSpec(_sheet_name(kpi, used or set()))


def _sheet_name(kpi: str, used: set[str]) -> str:
    """Excel allows 31 chars and forbids : \\ / ? * [ ]."""
    name = re.sub(r"[:\\/?*\[\]]", "_", str(kpi)).strip() or "KPI"
    name = re.sub(r"\s+", " ", name)[:31]
    base = name
    i = 2
    while name.lower() in {u.lower() for u in used}:
        tail = f"_{i}"
        name = base[:31 - len(tail)] + tail
        i += 1
    return name


def order_kpis(kpis: list[str]) -> list[str]:
    """The five the team already reads come first, in the order their own
    workbook has them; anything else follows in the order it was picked."""
    known = list(SPECS)
    return sorted(kpis, key=lambda k: (known.index(k) if k in known
                                       else len(known) + kpis.index(k)))


def pivot_one(df: pd.DataFrame, kpi: str) -> pd.DataFrame:
    """Row per (cell, duplex/RNC), column per hour, hourly mean, 2 dp."""
    if df.empty or kpi not in df.columns:
        return pd.DataFrame()
    work = df[df[kpi].notna()]
    if work.empty:
        return pd.DataFrame()
    # the second column has to come from the rows that carry *this* KPI. Taken
    # from the whole frame instead, a 4G export loaded next to a 3G one lends
    # its `duplex` to the 3G sheet, where it is empty — and pivot_table drops
    # rows whose index is NaN, so the sheet came out empty and was skipped.
    side = next((c for c in ("duplex", "parent")
                 if c in work.columns and work[c].notna().any()), None)
    index = ["object"] + ([side] if side else [])
    piv = pd.pivot_table(work, index=index, columns="datetime", values=kpi,
                         aggfunc="mean", observed=True)
    out = piv.round(2).reset_index()
    return out.rename(columns={side: _side_label(work, side)}) if side else out


def _side_label(work: pd.DataFrame, side: str) -> str:
    """What the operator calls the column next to the cell name."""
    if side == "duplex":
        return "Cell FDD TDD Indication"
    techs = set(work["tech"].dropna()) if "tech" in work.columns else set()
    if techs == {"2G"}:
        return "BSC"
    if techs and techs != {"3G"}:
        return "Group"
    return "RNC"


def write_pivot_workbook(df: pd.DataFrame, kpis: list[str],
                         out_path: str | Path, *,
                         max_sheets: int = 15
                         ) -> tuple[Path, list[str], list[str]]:
    """Write one sheet per KPI.

    Returns the path, the sheets written, and the KPIs left out — a KPI with
    no rows behind it gets no sheet, and the caller should be able to say so
    rather than let it vanish.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    skipped: list[str] = []

    with pd.ExcelWriter(out_path, engine="xlsxwriter") as xw:
        wb = xw.book
        wanted = order_kpis(kpis)
        skipped.extend(wanted[max_sheets:])
        for kpi in wanted[:max_sheets]:
            piv = pivot_one(df, kpi)
            if piv.empty:
                skipped.append(kpi)
                continue
            spec = spec_for(kpi, set(written))
            _write_sheet(wb, piv, kpi, spec)
            written.append(spec.sheet)
        if not written:                     # ExcelWriter needs a sheet to save
            wb.add_worksheet("no data")
    return out_path, written, skipped


def _write_sheet(wb, piv: pd.DataFrame, kpi: str, spec: SheetSpec) -> None:
    ws = wb.add_worksheet(spec.sheet)
    # the hour columns are timestamps; whatever comes before them identifies
    # the row (the cell, and the group it belongs to)
    n_id = sum(1 for c in piv.columns if not isinstance(c, pd.Timestamp))
    hours = list(piv.columns[n_id:])
    n_rows, n_cols = len(piv), n_id + len(hours)

    arial = {"font_name": "Arial", "font_size": 10}
    banner_f = wb.add_format({"font_name": "Arial", "font_size": 13,
                              "bold": True, "font_color": "white",
                              "bg_color": _BANNER_FILL, "align": "center",
                              "valign": "vcenter"})
    head_f = wb.add_format({**arial, "bold": True, "font_color": "white",
                            "bg_color": _HEADER_FILL, "border": 1,
                            "align": "center", "valign": "vcenter",
                            "text_wrap": True})
    id_f = wb.add_format({**arial, "border": 1})
    val_f = wb.add_format({**arial, "border": 1, "num_format": "0.00"})
    flag_f = wb.add_format({**arial, "border": 1, "num_format": "0.00",
                            "bold": True, "font_color": "white",
                            "bg_color": _FLAG_FILL})

    first, last = pd.Timestamp(hours[0]), pd.Timestamp(hours[-1])
    span = (first.strftime("%Y-%m-%d") if first.date() == last.date()
            else f"{first:%Y-%m-%d} to {last:%Y-%m-%d}")
    title = (f"Average of {kpi} - {span} - Hourly {first:%I:%M %p} to "
             f"{last:%I:%M %p}")
    if spec.issue:
        title += f"  |  Issue threshold: {spec.issue_text}"
    ws.merge_range(0, 0, 0, max(n_cols - 1, 1), title, banner_f)
    ws.set_row(0, 22)

    headers = ["Row Labels"] + [str(c) for c in piv.columns[1:n_id]] + \
              [pd.Timestamp(h).strftime("%m/%d %I:%M %p") for h in hours]
    for j, text in enumerate(headers):
        ws.write(1, j, text, head_f)
    ws.set_row(1, 28)

    for i, row in enumerate(piv.itertuples(index=False), start=2):
        for j in range(n_id):
            ws.write(i, j, str(row[j]), id_f)
        for j, v in enumerate(row[n_id:], start=n_id):
            if v is None or v != v:
                ws.write_blank(i, j, None, val_f)
            else:
                ws.write_number(i, j, float(v), val_f)

    ws.freeze_panes(2, n_id)
    ws.autofilter(1, 0, n_rows + 1, n_cols - 1)
    ws.set_column(0, 0, 30)
    if n_id == 2:
        ws.set_column(1, 1, 22)
    ws.set_column(n_id, n_cols - 1, 11)

    if not n_rows:
        return
    r1, r2, c1, c2 = 2, n_rows + 1, n_id, n_cols - 1
    # the flag goes on first: earlier rules win, and stop_if_true keeps the
    # gradient from repainting a real breach
    if spec.issue:
        crit, val = spec.issue
        ws.conditional_format(r1, c1, r2, c2, {
            "type": "cell", "criteria": crit, "value": val,
            "format": flag_f, "stop_if_true": True})
    ws.conditional_format(r1, c1, r2, c2, _colour_rule(spec, kpi))


def _colour_rule(spec: SheetSpec, kpi: str) -> dict:
    if spec.anchors:
        lo_v, lo_c, mid_v, mid_c, hi_v, hi_c = spec.anchors
        return {"type": "3_color_scale",
                "min_type": "num", "min_value": lo_v, "min_color": lo_c,
                "mid_type": "num", "mid_value": mid_v, "mid_color": mid_c,
                "max_type": "num", "max_value": hi_v, "max_color": hi_c}
    lo_c, mid_c, hi_c = (_DOWN_RELATIVE if _direction(kpi) == "down"
                         else _UP_RELATIVE)
    return {"type": "3_color_scale", "min_color": lo_c, "mid_color": mid_c,
            "max_color": hi_c}
