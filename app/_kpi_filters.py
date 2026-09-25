"""One filter for the KPI Analysis Overview.

Every panel of the Overview — the KPI rings, the Governorate, Sup District and
City tables, the map, the KPI issues table and the site details — reads the same
rows through the same filter, so they always agree:

* `enrich` joins onto the `_kpi_health` object rows, once per loaded
  judgement, what the filters and the table need: the site's governorate, Sup
  District, city and name, the object's cells, the issue, and the values as the
  page shows them;
* `Filters` is what the user set: technology, KPI, governorate, Sup Districts
  (several at once), site, and in the table cell, search, issue and status, and
  the KPIs ticked under KPI selection. City is where a site is, never a filter;
* `apply` keeps the sites the filters keep (what Total Sites counts) and the
  object × KPI rows they keep (what every issue count is made of);
* `counts` is the rings' numbers, on any filtered rows — the window, or either
  of its halves for the trend.

A panel that picks an area leaves its own pick out (`Filters.without`), so the
Sup District table still lists the districts to switch to.

Nothing here judges a KPI: states, thresholds and values come from `_kpi_health`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace

import numpy as np
import pandas as pd

from _kpi_health import value_text
from _kpi_region import UNKNOWN

STATUSES = ("Critical", "Warning", "Normal", "Detected")
RINGS = {"prb": "PRB", "flow": "FLOW", "rtwp": "RTWP"}


def _clean(x) -> str:
    s = "" if x is None else str(x).strip()
    return "" if s.lower() in ("", "nan", "none", "nat") else s


def _text(values, n: int, fill: str = "") -> np.ndarray:
    if values is None:
        return np.full(n, fill, dtype=object)
    return np.array([_clean(v) or fill for v in values], dtype=object)


def issue_of(label: str, sev: int, judged: bool, low_is_bad: bool) -> str:
    """"High DL PRB", "Low Availability"; a counted KPI "S1 failures detected"."""
    if sev <= 0:
        return ""
    name = str(label).split(" ", 1)[-1]
    if not judged:
        return f"{name} detected"
    return f"{'Low' if low_is_bad else 'High'} {name}"


def _id_text(x) -> str:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return _clean(x)
    return str(int(f)) if np.isfinite(f) and f.is_integer() else _clean(x)


def cell_table(index: pd.DataFrame | None, cell_ids: pd.Series | None = None) -> pd.DataFrame:
    """Per technology and object (a sector, or the site / NodeB when the export
    names no sector): the cells the exports list under it, and their cell IDs
    from the EP tracker (cell name -> ID). Keyed "4G|BAS0001-S1"."""
    if index is None or index.empty or "tech" not in index.columns:
        return pd.DataFrame(columns=["cell_name", "cell_id"])
    d = (index[["tech", "object", "site_id", "sector_id"]].dropna(subset=["site_id"])
         .drop_duplicates(["tech", "object"]))
    sector = d["sector_id"].astype(str)
    oid = np.where(sector.str.endswith("-S0"), d["site_id"].astype(str), sector)
    ids = (d["object"].astype(str).str.strip().str.upper().map(cell_ids)
           if cell_ids is not None and len(cell_ids) else pd.Series(np.nan, index=d.index))
    d = d.assign(_key=d["tech"].astype(str) + "|" + oid, _id=ids.map(_id_text)).sort_values(
        "object", kind="stable")
    g = d.groupby("_key", sort=False)
    return pd.DataFrame({
        "cell_name": g["object"].agg(lambda s: ", ".join(s.astype(str))),
        "cell_id": g["_id"].agg(lambda s: ", ".join(x for x in s if x)),
    })


def enrich(objects: pd.DataFrame | None, regions: pd.DataFrame, site_names: pd.Series | None = None,
           cells: pd.DataFrame | None = None, *, display: bool = True) -> pd.DataFrame | None:
    """The object rows with what the page filters and shows. `display` adds the
    values as text (the table's); the window's halves need only the filters."""
    if objects is None:
        return None
    rows = objects.reset_index(drop=True)
    n = len(rows)
    sid = rows["site_id"].astype(str)
    reg = regions.reindex(sid) if len(regions) else pd.DataFrame(index=sid)
    names = site_names.reindex(sid) if site_names is not None and len(site_names) else None
    key = rows["kind"].astype(str) + "|" + rows["object_id"].astype(str)
    have_cells = cells is not None and len(cells)
    out = rows.assign(
        site_id=sid.to_numpy(),
        governorate=_text(reg["governorate"] if "governorate" in reg.columns else None, n,
                          UNKNOWN),
        city=_text(reg["city"] if "city" in reg.columns else None, n, UNKNOWN),
        sup_district=_text(reg["sup_district"] if "sup_district" in reg.columns else None, n,
                           UNKNOWN),
        site_name=_text(names, n),
        cell_name=_text(cells["cell_name"].reindex(key) if have_cells else None, n),
        cell_id=_text(cells["cell_id"].reindex(key) if have_cells else None, n),
        issue=[issue_of(lab, int(s), bool(j), bool(low)) for lab, s, j, low in
               zip(rows["label"], rows["sev"], rows["judged"], rows["low_is_bad"])],
    )
    if display:
        out["value_text"] = [value_text(v, u, j) for v, u, j in
                             zip(rows["value"], rows["unit"], rows["judged"])]
        out["worst_text"] = [value_text(v, u, j) for v, u, j in
                             zip(rows["peak"], rows["unit"], rows["judged"])]
    out["_site_q"] = (out["site_id"] + " " + out["site_name"]).str.lower()
    out["_cell_q"] = (rows["object_id"].astype(str) + " " + out["cell_name"] + " "
                      + out["cell_id"]).str.lower()
    # the table's search: everything a row shows
    out["_q"] = (out["_site_q"] + " " + out["_cell_q"] + " " + out["governorate"] + " "
                 + out["sup_district"] + " " + out["city"] + " " + rows["kind"].astype(str)
                 + " " + rows["label"].astype(str) + " " + rows["state"].astype(str) + " "
                 + out["issue"].astype(str)).str.lower()
    return out


def site_frame(sites, regions: pd.DataFrame, site_names: pd.Series | None = None,
               kinds: dict | None = None) -> pd.DataFrame:
    """One row per loaded site: where it is, its name and technologies."""
    idx = pd.Index([str(s) for s in sites], name="site_id")
    n = len(idx)
    reg = regions.reindex(idx) if len(regions) else pd.DataFrame(index=idx)
    names = site_names.reindex(idx) if site_names is not None and len(site_names) else None
    out = pd.DataFrame({
        "governorate": _text(reg["governorate"] if "governorate" in reg.columns else None, n,
                             UNKNOWN),
        "city": _text(reg["city"] if "city" in reg.columns else None, n, UNKNOWN),
        "sup_district": _text(reg["sup_district"] if "sup_district" in reg.columns else None,
                              n, UNKNOWN),
        "site_name": _text(names, n),
        "kinds": [" ".join((kinds or {}).get(s, [])) for s in idx],
    }, index=idx)
    out["_site_q"] = (pd.Series(idx, index=idx) + " " + out["site_name"]).str.lower()
    return out


def terms(text: str) -> list[str]:
    """Search words: several IDs or names at once, split on commas, semicolons or spaces."""
    return [t for t in re.split(r"[,;\s]+", str(text or "").strip().lower()) if t]


def _any_of(series: pd.Series, words: list[str]) -> pd.Series:
    hit = pd.Series(False, index=series.index)
    for w in words:
        hit |= series.str.contains(w, regex=False, na=False)
    return hit


@dataclass(frozen=True)
class Filters:
    site: str = ""               # one site ID
    cell: str = ""               # objects, cell names or cell IDs, contains
    q: str = ""                  # the table's search: site, cell, area, technology, KPI, status
    governorate: str = ""
    sup_districts: tuple = ()    # several at once
    techs: tuple = ()
    kpis: tuple = ()             # KPI labels ("4G DL PRB")
    issues: tuple = ()           # "High DL PRB"
    states: tuple = ()
    columns: tuple = ()          # the export columns ticked under KPI selection

    def without(self, *names: str) -> "Filters":
        return replace(self, **{n: type(getattr(self, n))() for n in names})

    def active(self) -> list[tuple[str, str]]:
        """(filter, value) for each filter the user set, KPI selection aside."""
        label = {"site": "Site", "cell": "Cell", "q": "Search", "governorate": "Governorate",
                 "sup_districts": "Sup District", "techs": "Technology", "kpis": "KPI",
                 "issues": "Issue", "states": "Status"}
        out = []
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name != "columns" and v:
                out.append((label[f.name], ", ".join(v) if isinstance(v, tuple) else str(v)))
        return out


def apply(rows: pd.DataFrame | None, sites: pd.DataFrame, f: Filters):
    """(the sites kept, the rows kept). A site is kept by where it is (its
    governorate, one of the Sup Districts), its ID, its technology and — with a
    cell filter or a search — by holding a matching row; KPI, issue and status
    narrow only the rows."""
    keep = pd.Series(True, index=sites.index)
    if f.governorate:
        keep &= sites["governorate"] == f.governorate
    if f.sup_districts:
        keep &= sites["sup_district"].isin(list(f.sup_districts))
    if f.techs:
        keep &= _any_of(sites["kinds"].str.lower(), [t.lower() for t in f.techs])
    if f.site:
        keep &= sites.index == f.site
    universe = sites.index[keep.to_numpy()]
    if rows is None:
        return universe, None
    r = rows[rows["site_id"].isin(universe)]
    if f.techs:
        r = r[r["kind"].isin(f.techs)]
    if terms(f.cell):
        r = r[_any_of(r["_cell_q"], terms(f.cell))]
        universe = universe[universe.isin(r["site_id"].unique())]
    if terms(f.q):
        hit = pd.Series(True, index=r.index)
        for w in terms(f.q):           # every word must match somewhere in the row
            hit &= r["_q"].str.contains(w, regex=False, na=False)
        r = r[hit]
        universe = universe[universe.isin(r["site_id"].unique())]
    if f.columns:
        r = r[r["column"].isin(f.columns)]
    if f.kpis:
        r = r[r["label"].isin(f.kpis)]
    if f.issues:
        r = r[r["issue"].isin(f.issues)]
    if f.states:
        r = r[r["state"].isin(f.states)]
    return universe, r


def _judged(rows: pd.DataFrame | None, key: str | None):
    if rows is None or rows.empty:
        return None
    j = rows[rows["judged"].astype(bool)]
    return j if key is None else j[j["key"] == key]


def _breach(rows: pd.DataFrame | None, key: str | None = None,
            measured: pd.DataFrame | None = None):
    """(sites above threshold among `rows`, sites measured among `measured`, else
    `rows`) on the judged rows of one KPI; None when nothing of it is measured."""
    m = _judged(rows if measured is None else measured, key)
    if m is None or m.empty:
        return None
    j = _judged(rows, key)
    n = 0 if j is None or j.empty else int(j.loc[j["sev"] > 0, "site_id"].nunique())
    return n, int(m["site_id"].nunique())


def counts(rows: pd.DataFrame | None, tdd: pd.DataFrame | None,
           measured: pd.DataFrame | None = None, measured_tdd: pd.DataFrame | None = None) -> dict:
    """The rings' numbers: sites with an issue, checks above threshold, and per
    ring KPI (breaching sites, measured sites) or None when that KPI has no row
    here. `measured` is the rows before the status and issue filters, so a share
    is out of every site the KPI was measured on. TDD interference is the UL
    interference judgement over the TDD cells alone."""
    bad = (rows[rows["judged"].astype(bool) & (rows["sev"] > 0)]
           if rows is not None and len(rows) else None)
    out = {"issues": 0 if bad is None else int(bad["site_id"].nunique()),
           "checks": 0 if bad is None else int(len(bad))}
    for name, key in RINGS.items():
        out[name] = _breach(rows, key, measured)
    out["tdd"] = _breach(tdd, "INTER", measured_tdd)
    return out


def worst_sites(rows: pd.DataFrame | None, regions: pd.DataFrame) -> pd.DataFrame:
    """The sites with an issue among the rows, each with its worst check and its
    position: the map's affected-site markers."""
    cols = ["site_id", "sev", "label", "issues", "latitude", "longitude"]
    if rows is None or rows.empty:
        return pd.DataFrame(columns=cols)
    bad = rows[rows["judged"].astype(bool) & (rows["sev"] > 0)]
    if bad.empty:
        return pd.DataFrame(columns=cols)
    worst = (bad.sort_values("sev", ascending=False, kind="stable")
             .drop_duplicates("site_id").set_index("site_id"))
    out = pd.DataFrame({"sev": worst["sev"].astype(int), "label": worst["label"],
                        "issues": bad.groupby("site_id").size().reindex(worst.index)})
    pos = regions.reindex(out.index)
    out["latitude"] = pos["latitude"] if "latitude" in pos.columns else np.nan
    out["longitude"] = pos["longitude"] if "longitude" in pos.columns else np.nan
    out = out[out["latitude"].notna() & out["longitude"].notna()]
    return out.rename_axis("site_id").reset_index()[cols]
