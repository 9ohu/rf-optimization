"""Site health for the KPI Analysis page — two levels, judged hour by hour.

* **Cell level.** Each object is analysed on its own: a cell where the export
  measures cells, the site (NodeB / eNodeB) where it measures sites — never
  converted from one to the other (`load_hourly_raw`'s `level`). DL flow
  control is the exception the rules name: it is judged per **site** and hour
  (the cells' drops of the hour added up).
* **Every hourly value is judged on its own** against its line
  (`rfopt.kpi.thresholds.hourly_severity`; a TDD cell's UL interference on the
  TDD line). An object has an Issue when any hour breaches; its Worst Hour and
  Worst Value are its worst hour. The window average is kept for display only
  — it never decides an issue.
* **Site level.** A site is judged on all of its cells: its state is the worst
  of them, with how many cells and how many cell × KPI checks breach.

Only a line with a documented source judges (`_kpi_map.threshold_rule`,
`rfopt.complaints.noc.indicator_columns`; config/THRESHOLD_SOURCES.md). S1
failures have no threshold: they are counted and shown, never an issue.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from _kpi_map import canonical_name, threshold_rule
from rfopt.complaints.noc import fmt, indicator_columns
from rfopt.kpi.thresholds import hourly_severity
from rfopt.kpi.trends import agg_how

STATE = {2: "Critical", 1: "Warning", 0: "Normal", -1: "No data"}
PRIMARY = ("AVA", "PRB", "INTER", "FLOW")
SECONDARY = ("IPL", "RTWP", "S1", "CSSR")
NAMES = {"AVA": "Availability", "PRB": "PRB utilisation", "INTER": "UL interference",
         "FLOW": "DL flow-control drops", "IPL": "IP path RTT (IPPM)", "RTWP": "RTWP",
         "S1": "S1 signalling failures", "CSSR": "Call setup success"}
TDD = "CELL_TDD"

_KEY = {"cell_avail_pct": "AVA", "dl_prb_util": "PRB", "ul_prb_util": "PRB",
        "ul_rssi_dbm": "INTER", "call_setup_sr": "CSSR", "ipmm_rtt_ms": "IPL",
        "ul_rtwp_dbm": "RTWP"}
_NAME = {"cell_avail_pct": "Availability", "dl_prb_util": "DL PRB", "ul_prb_util": "UL PRB",
         "ul_rssi_dbm": "UL interference", "call_setup_sr": "CSSR",
         "ipmm_rtt_ms": "IP path RTT", "ul_rtwp_dbm": "RTWP"}
_UNIT = {"cell_avail_pct": "%", "dl_prb_util": "%", "ul_prb_util": "%",
         "ul_rssi_dbm": " dBm", "call_setup_sr": "%", "ipmm_rtt_ms": " ms",
         "ul_rtwp_dbm": " dBm"}
# the main KPIs: they always lead (4G High PRB / Availability / Interference,
# 3G Flow Control / Availability / RTWP)
MAIN = ("PRB", "AVA", "INTER", "FLOW", "RTWP")

COLUMNS = ["site_id", "object_id", "object_type", "kind", "key", "label", "column", "unit",
           "judged", "low_is_bad", "value", "sev", "state", "threshold", "peak", "peak_time",
           "hours", "issue_hours"]


def value_text(value, unit: str = "", judged: bool = True) -> str:
    """A value as the page shows it; a count (S1 failures) as a whole number."""
    v = float(value)
    if not np.isfinite(v):
        return "—"
    return fmt(v, unit) if judged else f"{v:,.0f}"


@dataclass(frozen=True)
class Judged:
    column: str
    key: str            # AVA / PRB / INTER / FLOW / IPL / RTWP / S1 / CSSR, else the column
    label: str          # "4G DL PRB"
    kind: str           # 4G / 3G
    rule: object        # the threshold rule; None for a count that is never judged
    how: str            # window: every hour of the object · site_hour: every hour of the site · count
    unit: str

    @property
    def threshold(self) -> str:
        if self.rule is None:
            return "no threshold (count)"
        return threshold_text(self.rule, self.unit, site=self.how == "site_hour")


def threshold_text(rule, unit: str = "", *, site: bool = False) -> str:
    """The line as the pages quote it: one Issue line, judged per hour."""
    op = "<" if rule.direction == "up" else ">"
    if rule.warning == rule.critical:
        text = f"Issue {op} {rule.critical:,g}{unit} per hour"
    else:
        text = f"⚠ {op} {rule.warning:,g}{unit} · ● {op} {rule.critical:,g}{unit} per hour"
    if rule.kpi == "ul_rssi_dbm":
        text += " (FDD; TDD > -100 dBm)"
    return text + (" at site level" if site else "")


def judged_columns(kpis, kind: str) -> list[Judged]:
    """The KPIs of one export the page can judge (or, for S1, count)."""
    out = []
    for k in kpis:
        rule = threshold_rule(k)
        if rule is None:
            continue
        canon = canonical_name(k) or ""
        out.append(Judged(k, _KEY.get(canon, k), f"{kind} {_NAME.get(canon, k)}", kind,
                          rule, "window", _UNIT.get(canon, "")))
    have = {j.key for j in out}
    for col, d, rule in indicator_columns(kpis, kind):
        if d.key in have:               # RTWP is judged through its threshold already
            continue
        out.append(Judged(col, d.key, d.label, kind, rule,
                          "window" if d.how == "worst" else d.how, d.unit))
    # the main KPIs lead
    return sorted(out, key=lambda j: (j.key not in MAIN,))


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def object_ids(d: pd.DataFrame) -> np.ndarray:
    """The object each row is analysed as: its cell where the export measures
    cells, its site where it measures sites (a NodeB / eNodeB)."""
    site = d["site_id"].astype(str).to_numpy()
    if "level" not in d.columns:
        return d["object"].astype(str).to_numpy()
    return np.where(d["level"].astype(str).eq("site").to_numpy(), site,
                    d["object"].astype(str).to_numpy())


def object_values(df: pd.DataFrame, j: Judged) -> pd.DataFrame:
    """One row per object for one KPI: every hour judged on its own — its state
    (the worst hour's), how many hours breach, its Worst Hour and Worst Value,
    and the window average (display only)."""
    col = j.column
    if df is None or df.empty or col not in df.columns:
        return _empty()
    extra = [c for c in ("object", "level", "duplex") if c in df.columns]
    d = df.loc[df[col].notna() & df["site_id"].notna(), ["datetime", "site_id", col] + extra]
    if d.empty:
        return _empty()
    d = d.assign(site_id=d["site_id"].astype(str))
    up = j.rule is not None and j.rule.direction == "up"
    if j.how == "site_hour":            # flow control: the site's drops of each hour
        d = d.assign(object_id=d["site_id"].to_numpy())
        how = "sum"
    else:
        d = d.assign(object_id=object_ids(d))
        how = "sum" if j.how == "count" else agg_how(col)
    keys = ["object_id", "datetime"]
    hourly = d.groupby(keys, observed=True, sort=True).agg(
        site_id=("site_id", "first"), v=(col, how),
        **({"duplex": ("duplex", "first")} if "duplex" in d.columns else {})).reset_index()
    if j.rule is None:
        hourly["sev"] = (hourly["v"] > 0).astype(int)
    else:
        hourly["sev"] = hourly_severity(j.rule, hourly["v"].to_numpy(),
                                        hourly["duplex"] if "duplex" in hourly.columns else None)
    g = hourly.groupby("object_id", sort=True)
    if j.how == "count":
        value = g["v"].sum()
    else:                               # the window average: display only
        value = g["v"].mean()
    # the worst hour: the most severe, then the most extreme in the bad direction
    rank = hourly["v"] if up else -hourly["v"]
    worst = (hourly.assign(_r=rank).sort_values(["object_id", "sev", "_r"],
                                                ascending=[True, False, True], kind="stable")
             .drop_duplicates("object_id").set_index("object_id"))
    ids = value.index.astype(str)
    sites = worst["site_id"].reindex(value.index)
    sev = g["sev"].max().reindex(value.index).to_numpy(dtype=int)
    if j.rule is None:
        state = np.where(sev > 0, "Detected", "Normal")
    else:
        state = np.array([STATE[x] for x in sev], dtype=object)
    level = (d.drop_duplicates("object_id").set_index("object_id")["level"].reindex(value.index)
             if "level" in d.columns else pd.Series("cell", index=value.index))
    is_site = (ids == sites.astype(str).to_numpy()) | level.eq("site").to_numpy()
    return pd.DataFrame({
        "site_id": sites.astype(str).to_numpy(), "object_id": ids,
        "object_type": np.where(is_site, "NodeB" if j.kind == "3G" else "Site", "Cell"),
        "kind": j.kind, "key": j.key, "label": j.label, "column": col, "unit": j.unit,
        "judged": j.rule is not None, "low_is_bad": up,
        "value": value.to_numpy(dtype=float), "sev": sev, "state": state,
        "threshold": j.threshold,
        "peak": worst["v"].reindex(value.index).to_numpy(dtype=float),
        "peak_time": pd.to_datetime(worst["datetime"].reindex(value.index).to_numpy()),
        "hours": g.size().reindex(value.index).to_numpy(dtype=int),
        "issue_hours": g["sev"].apply(lambda s: int((s > 0).sum()))
        .reindex(value.index).to_numpy(dtype=int),
    })


@dataclass
class Health:
    objects: pd.DataFrame                  # object × KPI rows, S1 counts included
    tdd: pd.DataFrame | None               # UL interference over TDD cells only
    sites: list = field(default_factory=list)
    kinds: dict = field(default_factory=dict)
    start: pd.Timestamp | None = None
    end: pd.Timestamp | None = None


def build_health(frames) -> Health:
    """frames: (technology, frame from load_hourly_raw, judged_columns)."""
    parts, tdd_parts, kinds = [], [], {}
    start = end = None
    for kind, df, judged in frames:
        if df is None or df.empty:
            continue
        for s in df["site_id"].dropna().astype(str).unique():
            kinds.setdefault(s, set()).add(kind)
        t0, t1 = df["datetime"].min(), df["datetime"].max()
        start = t0 if start is None else min(start, t0)
        end = t1 if end is None else max(end, t1)
        for j in judged:
            part = object_values(df, j)
            if len(part):
                parts.append(part)
            if j.key == "INTER" and "duplex" in df.columns:
                # the same judgement over the TDD cells alone
                rows = df[df["duplex"].astype(str).str.upper() == TDD]
                sub = object_values(rows, j) if len(rows) else _empty()
                if len(sub):
                    tdd_parts.append(sub)
    objects = pd.concat(parts, ignore_index=True) if parts else _empty()
    tdd = pd.concat(tdd_parts, ignore_index=True) if tdd_parts else None
    return Health(objects, tdd, sorted(kinds), {k: sorted(v) for k, v in kinds.items()},
                  start, end)


def scope_sites(index: pd.DataFrame, level: str, obj: str | None, cells,
                areas: pd.Series | None = None) -> set | None:
    """The sites the page's object selection covers; None is the whole network.
    `areas` (site_id -> governorate) places the sites for the Governorate level."""
    if cells:
        return set(index.loc[index["object"].isin(list(cells)), "site_id"].dropna().astype(str))
    if level in ("Governorate", "City") and obj:
        if areas is None:
            return set()
        loaded = set(index["site_id"].dropna().astype(str))
        return {s for s in areas.index[areas == obj] if s in loaded}
    if level == "Site" and obj:
        return {str(obj)}
    return None


def in_scope(frame: pd.DataFrame | None, scope: set | None):
    if frame is None or scope is None:
        return frame
    return frame[frame["site_id"].isin(scope)]


def site_status(objects: pd.DataFrame, sites) -> pd.DataFrame:
    """Per site, judged on all of its cells: its worst state and the KPI behind
    it (a main KPI first), how many cell × KPI checks breach, and how many of
    its cells have an issue out of how many were measured."""
    out = pd.DataFrame(index=pd.Index(sorted(sites), name="site_id"))
    j = objects[objects["judged"].astype(bool)] if len(objects) else objects
    if j.empty:
        out["sev"], out["label"], out["issues"], out["bad_kpis"] = -1, "", 0, ""
        out["cells"], out["cells_issue"] = 0, 0
    else:
        worst = (j.assign(_main=~j["key"].isin(MAIN))
                 .sort_values(["sev", "_main"], ascending=[False, True], kind="stable")
                 .drop_duplicates("site_id").set_index("site_id"))
        bad = j[j["sev"] > 0]
        out = out.join(worst[["sev", "label", "key", "object_id"]])
        out["sev"] = out["sev"].fillna(-1).astype(int)
        out["label"] = out["label"].where(out["sev"] >= 0, "")
        out["issues"] = bad.groupby("site_id").size().reindex(out.index).fillna(0).astype(int)
        out["bad_kpis"] = (bad.groupby("site_id")["label"]
                           .agg(lambda s: ", ".join(sorted(set(s))))
                           .reindex(out.index).fillna(""))
        out["cells"] = (j.groupby("site_id")["object_id"].nunique()
                        .reindex(out.index).fillna(0).astype(int))
        out["cells_issue"] = (bad.groupby("site_id")["object_id"].nunique()
                              .reindex(out.index).fillna(0).astype(int))
    out["state"] = out["sev"].map(STATE)
    return out


def summary(objects: pd.DataFrame, tdd: pd.DataFrame | None, sites, scope: set | None) -> dict:
    """The numbers on the cards, the donut and the ranking, for the selected area."""
    in_sites = [s for s in sites if scope is None or s in scope]
    obj = in_scope(objects, scope)
    status = site_status(obj, in_sites)

    def breaching(key: str):
        f = obj[(obj["key"] == key) & obj["judged"].astype(bool)]
        return None if f.empty else int(f.loc[f["sev"] > 0, "site_id"].nunique())

    t = in_scope(tdd, scope)
    return {
        "total": len(in_sites),
        "issues": int((status["sev"] > 0).sum()),
        "prb": breaching("PRB"), "flow": breaching("FLOW"), "rtwp": breaching("RTWP"),
        "tdd": None if t is None else int(t.loc[t["sev"] > 0, "site_id"].nunique()),
        "status": status,
        "distribution": {s: int((status["sev"] == k).sum()) for k, s in STATE.items()},
    }


def problem_ranking(objects: pd.DataFrame) -> pd.DataFrame:
    """KPIs by how many sites breach them, with how many of those are critical."""
    j = objects[objects["judged"].astype(bool) & (objects["sev"] > 0)] if len(objects) else objects
    if j.empty:
        return pd.DataFrame(columns=["label", "key", "sites", "critical"])
    g = j.groupby("label").agg(key=("key", "first"), sites=("site_id", "nunique"))
    g["critical"] = (j[j["sev"] == 2].groupby("label")["site_id"].nunique()
                     .reindex(g.index).fillna(0).astype(int))
    return g.reset_index().sort_values(["sites", "critical"], ascending=False, kind="stable")


def issue_order(frame: pd.DataFrame) -> pd.DataFrame:
    """Worst first: an issue before normal, then per KPI the worst hour's value
    in the threshold's own direction."""
    if frame.empty:
        return frame
    key = np.where(frame["low_is_bad"].astype(bool), frame["peak"], -frame["peak"])
    return (frame.assign(_k=key)
            .sort_values(["sev", "label", "_k"], ascending=[False, True, True], kind="stable")
            .drop(columns="_k"))


def site_tiles(objects: pd.DataFrame, site_id: str) -> tuple[list[dict], list[dict]]:
    """The drawer's tiles: per KPI key, the site's worst object."""
    rows = objects[objects["site_id"] == site_id]

    def tile(key: str) -> dict:
        f = issue_order(rows[rows["key"] == key])
        if f.empty:
            return {"key": key, "name": NAMES.get(key, key), "value": "—", "sev": -1,
                    "state": "No data", "note": "not in the loaded exports", "judged": True,
                    "threshold": "", "object": "", "peak_time": None, "n": 0, "bad": 0}
        r = f.iloc[0]
        sev = int(r["sev"])
        word = r["state"]
        if r["judged"] and sev == 1:
            word = "Low" if r["low_is_bad"] else "High"
        # a judged KPI shows its worst hour — the hour that decided its state
        shown = r["peak"] if r["judged"] else r["value"]
        return {"key": key, "name": r["label"], "value": value_text(shown, r["unit"], r["judged"]),
                "sev": sev, "state": word, "judged": bool(r["judged"]),
                "threshold": r["threshold"], "object": r["object_id"],
                "peak_time": r["peak_time"], "n": int(len(f)),
                "bad": int((f["sev"] > 0).sum()),
                "note": f"{int((f['sev'] > 0).sum())} of {len(f)} objects with an issue hour"
                if r["judged"] else f"total over the window on {r['object_id']}"}

    extra = [k for k in rows["key"].unique() if k not in PRIMARY + SECONDARY]
    return ([tile(k) for k in PRIMARY],
            [tile(k) for k in SECONDARY if (rows["key"] == k).any()] + [tile(k) for k in extra])


def site_hours(frames, site_id: str) -> pd.DataFrame:
    """Hour by hour, on every hourly-judged KPI of the site: the most severe of
    its objects that hour (then the most extreme value), and its state."""
    rows = []
    for kind, df, judged in frames:
        if df is None or df.empty:
            continue
        d = df[df["site_id"].astype(str) == site_id]
        if d.empty:
            continue
        for j in judged:
            if j.rule is None or j.how == "count" or j.column not in d.columns:
                continue
            o = object_values(d, j)
            if o.empty:
                continue
            x = d[d[j.column].notna()]
            ids = x["site_id"].astype(str).to_numpy() if j.how == "site_hour" else object_ids(x)
            h = (x.assign(object_id=ids)
                 .groupby(["datetime", "object_id"], observed=True)
                 .agg(v=(j.column, "sum" if j.how == "site_hour" else agg_how(j.column)),
                      **({"duplex": ("duplex", "first")} if "duplex" in x.columns else {}))
                 .reset_index())
            h["sev"] = hourly_severity(j.rule, h["v"].to_numpy(),
                                       h["duplex"] if "duplex" in h.columns else None)
            up = j.rule.direction == "up"
            h = (h.assign(_r=h["v"] if up else -h["v"])
                 .sort_values(["datetime", "sev", "_r"], ascending=[True, False, True],
                              kind="stable").drop_duplicates("datetime"))
            for t, oid, v, sv in zip(h["datetime"], h["object_id"], h["v"], h["sev"]):
                rows.append((t, j.key, j.label, oid, float(v), j.unit, int(sv), up, j.threshold))
    return pd.DataFrame(rows, columns=["hour", "key", "label", "object_id", "value", "unit",
                                       "sev", "low", "threshold"]).sort_values(
        ["hour", "label"], kind="stable")
