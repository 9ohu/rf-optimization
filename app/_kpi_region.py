"""KPI issues by Governorate, Sup District and City — where every site is.

The site health of `_kpi_health` — the same objects, the same states — grouped
by where the sites are. The KPI exports carry no location, so a site's position
comes from the EP tracker. Its Sup District is the official sub-district (OCHA
COD-AB admin3, `_kpi_bounds`) whose boundary holds it, or the nearest one within
2 km of a coastline or border line; its City is that sub-district's district
(admin2: Basrah, Zubair, Qurna …) and its Governorate the one that holds it
(Basrah, Dhi Qar, Maysan, Muthanna). Farther than 2 km from every sub-district a
site is "Outside R5 sub-districts", placed by the tracker's city and district.
Without the boundaries, the EP tracker's city, district and sub-district are
used. A site the tracker does not know takes its governorate from its ID prefix
(BAS, NAS, EMA, SAM) and is "Not in EP tracker" below that, with no place on
the map. `site_regions` is the one placement every page uses — KPI Analysis,
its Report Export and Complaints.

An area with a boundary is drawn as that boundary; one without (the outside
group, or every area when the boundaries are missing) as one marker at the
median position of its sites — never an invented outline.

Trend: nothing stores an earlier analysis beside the loaded exports, so the
comparison stays inside the loaded window — its second half against its first
half, each judged by `_kpi_health` on its own hours.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LEVELS = {"Governorate": "governorate", "Sup District": "sup_district", "City": "city"}
UNKNOWN = "Not in EP tracker"
# the four R5 governorates, in the order the pages list them
GOVERNORATES = ("Basrah", "Dhi Qar", "Maysan", "Muthanna")
_GOV_OF_AREA = {"Al-Basrah": "Basrah", "Thi Qar": "Dhi Qar", "Maysan": "Maysan",
                "Al-Muthanna": "Muthanna"}
MIN_TREND_HOURS = 4
COLUMNS = ["name", "governorate", "city", "sites", "affected", "rate", "issues", "critical",
           "warning", "top_issue", "worst", "latitude", "longitude", "trend"]

# the R5 cities as the page names them; the EP tracker spells them Basrah,
# Nassriya, Emarah and Samawa, and its site-ID prefixes match them one to one
CITIES = ("Basrah", "Nasiriyah", "Amarah", "Samawah")
_CITY = {"basrah": "Basrah", "basra": "Basrah", "al-basrah": "Basrah",
         "nassriya": "Nasiriyah", "nasriya": "Nasiriyah", "nasiriya": "Nasiriyah",
         "nasiriyah": "Nasiriyah", "nassiriya": "Nasiriyah", "thi qar": "Nasiriyah",
         "emarah": "Amarah", "amarah": "Amarah", "amara": "Amarah", "maysan": "Amarah",
         "missan": "Amarah", "samawa": "Samawah", "samawah": "Samawah",
         "al-muthanna": "Samawah", "muthanna": "Samawah"}
PREFIX_CITY = {"BAS": "Basrah", "NAS": "Nasiriyah", "EMA": "Amarah", "SAM": "Samawah"}
GOV_OF_CITY = {"Basrah": "Basrah", "Nasiriyah": "Dhi Qar", "Amarah": "Maysan",
               "Samawah": "Muthanna"}


def _clean(x) -> str:
    s = "" if x is None else str(x).strip()
    return "" if s.lower() in ("", "nan", "none", "nat") else s


def city_name(value, site_id: str = "") -> str:
    """A city in the page's spelling. A site the EP tracker gives no city takes
    the city of its ID's prefix (BAS, NAS, EMA, SAM); else it is unknown."""
    s = _clean(value)
    if s:
        return _CITY.get(s.lower(), s)
    return PREFIX_CITY.get(str(site_id)[:3].upper(), UNKNOWN)


def governorate_name(value, site_id: str = "") -> str:
    """One of the four R5 governorates, from a city or governorate as any file
    spells it, else from the site ID's prefix; "" when neither says."""
    s = _clean(value)
    if s in GOVERNORATES:
        return s
    if s in _GOV_OF_AREA:
        return _GOV_OF_AREA[s]
    low = s.lower()
    if low in _CITY:                       # the EP tracker's and the page's city spellings
        return GOV_OF_CITY[_CITY[low]]
    for key, gov in (("basra", "Basrah"), ("thi qar", "Dhi Qar"), ("dhi qar", "Dhi Qar"),
                     ("thiqar", "Dhi Qar"), ("nasir", "Dhi Qar"), ("nassir", "Dhi Qar"),
                     ("nassr", "Dhi Qar"), ("nasr", "Dhi Qar"),
                     ("maysan", "Maysan"), ("missan", "Maysan"), ("amara", "Maysan"),
                     ("emara", "Maysan"), ("muthanna", "Muthanna"), ("samaw", "Muthanna")):
        if key in low:
            return gov
    city = PREFIX_CITY.get(str(site_id)[:3].upper())
    return GOV_OF_CITY.get(city, "")


def town_name(district) -> str:
    """A district (admin2) as the page names the city: "Al-Zubair" -> "Zubair"."""
    s = _clean(district)
    return s[3:] if s.startswith("Al-") else s


COLUMNS_PLACE = ["governorate", "sup_district", "city", "latitude", "longitude", "snapped_m"]


def site_regions(sites, site_info: pd.DataFrame | None, areas=None) -> pd.DataFrame:
    """site_id -> governorate, sup_district, city, latitude, longitude, snapped_m.

    With `areas` (the official R5 boundaries) a site's Sup District is the
    sub-district that holds it, or the nearest one within 2 km (`snapped_m` says
    how far); its City is that sub-district's district and its Governorate the
    sub-district's governorate. Farther out it is counted as outside R5 and
    placed by the EP tracker's city and district. Without the boundaries, the EP
    tracker's city, district and sub-district."""
    idx = pd.Index([str(s) for s in sites], name="site_id")
    out = pd.DataFrame(index=idx)
    if site_info is None or site_info.empty:
        out["governorate"] = [governorate_name("", s) or UNKNOWN for s in idx]
        out["sup_district"] = UNKNOWN
        out["city"] = UNKNOWN
        out["latitude"], out["longitude"], out["snapped_m"] = np.nan, np.nan, np.nan
        return out[COLUMNS_PLACE]
    info = site_info.reindex(idx)

    def col(name: str) -> pd.Series:
        return info[name] if name in info.columns else pd.Series(np.nan, index=idx)

    ep_gov = [governorate_name(city_name(c, s), s) or UNKNOWN for c, s in zip(col("city"), idx)]
    ep_town = [(_clean(d) or UNKNOWN) for d in col("district")]
    known = info.index.isin(site_info.index)
    out["latitude"] = pd.to_numeric(col("latitude"), errors="coerce").to_numpy()
    out["longitude"] = pd.to_numeric(col("longitude"), errors="coerce").to_numpy()
    from _kpi_bounds import OUTSIDE, area_of, sub_districts
    subs = sub_districts(areas) if areas else []
    if subs:
        names, snapped = area_of(out["longitude"], out["latitude"], subs)
        placed = (out["latitude"].notna() & out["longitude"].notna()).to_numpy()
        of = {a.name: a for a in subs}
        out["sup_district"] = [n if n else (OUTSIDE if ok else UNKNOWN)
                               for n, ok in zip(names, placed)]
        out["city"] = [town_name(of[n].district) if n else (t if k else UNKNOWN)
                       for n, t, k in zip(names, ep_town, known)]
        out["governorate"] = [_GOV_OF_AREA.get(of[n].governorate, g) if n else g
                              for n, g in zip(names, ep_gov)]
        out["snapped_m"] = np.where(names != "", snapped, np.nan)
    else:
        out["sup_district"] = [(_clean(a) or _clean(b)) or UNKNOWN
                               for a, b in zip(col("sub_district"), col("district"))]
        out["city"] = [t if k else UNKNOWN for t, k in zip(ep_town, known)]
        out["governorate"] = ep_gov
        out["snapped_m"] = np.nan
    return out[COLUMNS_PLACE]


def _issues(objects: pd.DataFrame | None, regions: pd.DataFrame) -> pd.DataFrame:
    """The judged checks above threshold, on sites the regions know."""
    if objects is None or objects.empty:
        return pd.DataFrame(columns=["site_id", "label", "sev"])
    bad = objects[objects["judged"].astype(bool) & (objects["sev"] > 0)]
    return bad[bad["site_id"].isin(regions.index)]


def affected_by_area(objects: pd.DataFrame | None, regions: pd.DataFrame, level: str) -> pd.Series:
    """Sites with an issue, per area."""
    bad = _issues(objects, regions)
    if bad.empty:
        return pd.Series(dtype=int)
    area = regions.loc[bad["site_id"], LEVELS[level]].to_numpy()
    return bad.assign(_area=area).groupby("_area")["site_id"].nunique()


def region_table(objects: pd.DataFrame, regions: pd.DataFrame, level: str,
                 keep: tuple = ()) -> pd.DataFrame:
    """One row per area: its sites, the ones with an issue and their share, the
    checks above threshold (critical / warning), the KPI most of its affected
    sites share, its worst state and where its sites are. Worst areas first."""
    col = LEVELS[level]
    if regions.empty:
        return pd.DataFrame(columns=COLUMNS)
    g = regions.groupby(col)
    table = pd.DataFrame({"sites": g.size()})
    table["latitude"] = g["latitude"].median()
    table["longitude"] = g["longitude"].median()
    table["city"] = g["city"].agg(lambda s: s.value_counts().index[0])
    table["governorate"] = g["governorate"].agg(lambda s: s.value_counts().index[0])
    for c in ("affected", "issues", "critical", "warning", "worst"):
        table[c] = 0
    table["top_issue"] = ""

    bad = _issues(objects, regions)
    if len(bad):
        b = bad.assign(_area=regions.loc[bad["site_id"], col].to_numpy())
        gb = b.groupby("_area")
        table["affected"] = gb["site_id"].nunique().reindex(table.index).fillna(0)
        table["issues"] = gb.size().reindex(table.index).fillna(0)
        table["critical"] = b[b["sev"] == 2].groupby("_area").size().reindex(table.index).fillna(0)
        table["warning"] = b[b["sev"] == 1].groupby("_area").size().reindex(table.index).fillna(0)
        table["worst"] = gb["sev"].max().reindex(table.index).fillna(0)
        per_kpi = (b.groupby(["_area", "label"])["site_id"].nunique().reset_index()
                   .sort_values(["_area", "site_id", "label"], ascending=[True, False, True]))
        table["top_issue"] = (per_kpi.drop_duplicates("_area").set_index("_area")["label"]
                              .reindex(table.index).fillna(""))
    for name in keep:              # a picked area stays listed, even with no site here
        if name not in table.index:
            table.loc[name] = {"sites": 0, "latitude": np.nan, "longitude": np.nan,
                               "city": "", "governorate": "", "affected": 0, "issues": 0,
                               "critical": 0, "warning": 0, "worst": 0, "top_issue": ""}
    for c in ("sites", "affected", "issues", "critical", "warning", "worst"):
        table[c] = table[c].astype(int)
    table["trend"] = np.nan
    table["rate"] = np.where(table["sites"] > 0,
                             100.0 * table["affected"] / table["sites"].clip(lower=1), 0.0)
    table = table.rename_axis("name").reset_index()
    order = table.assign(_unknown=table["name"] == UNKNOWN).sort_values(
        ["_unknown", "affected", "issues", "rate", "name"],
        ascending=[True, False, False, False, True], kind="stable")
    return order[COLUMNS].reset_index(drop=True)


def add_trend(table: pd.DataFrame, prev_objects, last_objects, regions: pd.DataFrame,
              level: str) -> pd.DataFrame:
    """Sites with an issue in the window's second half minus its first half, per area."""
    if prev_objects is None or last_objects is None or table.empty:
        return table
    names = table["name"]
    prev = affected_by_area(prev_objects, regions, level).reindex(names).fillna(0)
    last = affected_by_area(last_objects, regions, level).reindex(names).fillna(0)
    return table.assign(trend=(last.to_numpy() - prev.to_numpy()).astype(float))


def area_counts(table: pd.DataFrame) -> tuple[int, int]:
    """(areas with an issue, areas) among the ones the EP tracker names."""
    known = table[table["name"] != UNKNOWN]
    return int((known["affected"] > 0).sum()), int(len(known))


def overall_trend(prev_objects, last_objects, regions: pd.DataFrame) -> dict:
    """Second half minus first half: sites with an issue, checks above threshold,
    and areas with an issue per level."""
    pb, lb = _issues(prev_objects, regions), _issues(last_objects, regions)
    out = {"affected": int(lb["site_id"].nunique() - pb["site_id"].nunique()),
           "issues": int(len(lb) - len(pb))}
    for level in LEVELS:
        a = affected_by_area(last_objects, regions, level)
        p = affected_by_area(prev_objects, regions, level)
        out[level] = int(len(a.drop(UNKNOWN, errors="ignore"))
                         - len(p.drop(UNKNOWN, errors="ignore")))
    return out


def halves(frames, start, end):
    """The loaded frames cut at the middle of the window, and the cut; None
    when the window is too short to compare."""
    if start is None or end is None or pd.isna(start) or pd.isna(end):
        return None
    if (end - start) < pd.Timedelta(hours=MIN_TREND_HOURS):
        return None
    mid = start + (end - start) / 2
    prev = [(k, df[df["datetime"] < mid], js) for k, df, js in frames]
    last = [(k, df[df["datetime"] >= mid], js) for k, df, js in frames]
    return prev, last, mid
