"""Site health for the KPI Analysis page, judged the way the app already judges.

Presentation model only. Nothing here defines a KPI rule:

* a sector's value (a 3G NodeB's, or a 4G cell that names no sector) is the
  KPI over the export window, aggregated with `agg_how` and grouped exactly as
  the Sites map groups it (`views/site_map._sector_kpis`);
* a KPI is judged on the operator's thresholds through `_kpi_map.threshold_rule`
  (the Sites map's rule) or, for the indicators Complaint Analysis reads — 3G
  RTWP, 3G DL flow-control drops (a 24 h total), 4G S1 failures — through
  `rfopt.complaints.noc.indicator_columns`;
* `rfopt.complaints.correlate.severity` turns a value into normal / warning /
  critical, and a site takes its worst object's state, like a tower on the map.

S1 failures have no threshold: they are counted and shown, never an issue.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from _kpi_map import canonical_name, threshold_rule
from rfopt.complaints.correlate import severity
from rfopt.complaints.noc import fmt, indicator_columns
from rfopt.kpi.trends import agg_how

STATE = {2: "Critical", 1: "Warning", 0: "Normal", -1: "No data"}
PRIMARY = ("AVA", "PRB", "INTER", "FLOW")
SECONDARY = ("IPL", "RTWP", "S1", "CSSR")
NAMES = {"AVA": "Availability", "PRB": "PRB utilisation", "INTER": "UL interference",
         "FLOW": "DL flow-control drops", "IPL": "IP path RTT (IPPM)", "RTWP": "RTWP",
         "S1": "S1 signalling failures", "CSSR": "Call setup success"}
TDD = "CELL_TDD"

_KEY = {"cell_avail_pct": "AVA", "dl_prb_util": "PRB", "ul_prb_util": "PRB",
        "ul_rssi_dbm": "INTER", "call_setup_sr": "CSSR", "ipmm_rtt_ms": "IPL"}
_NAME = {"cell_avail_pct": "Availability", "dl_prb_util": "DL PRB", "ul_prb_util": "UL PRB",
         "ul_rssi_dbm": "UL interference", "call_setup_sr": "CSSR",
         "ipmm_rtt_ms": "IP path RTT"}
_UNIT = {"cell_avail_pct": "%", "dl_prb_util": "%", "ul_prb_util": "%",
         "ul_rssi_dbm": " dBm", "call_setup_sr": "%", "ipmm_rtt_ms": " ms"}

COLUMNS = ["site_id", "object_id", "object_type", "kind", "key", "label", "column", "unit",
           "judged", "low_is_bad", "value", "sev", "state", "threshold", "peak", "peak_time"]


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
    how: str            # window: over the export window · day_sum: worst 24 h total · count
    unit: str

    @property
    def threshold(self) -> str:
        if self.rule is None:
            return "no threshold (count)"
        op = "<" if self.rule.direction == "up" else ">"
        per = " per 24 h" if self.how == "day_sum" else ""
        return (f"⚠ {op} {self.rule.warning:,g}{self.unit} · "
                f"● {op} {self.rule.critical:,g}{self.unit}{per}")


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
    for col, d, rule in indicator_columns(kpis, kind):
        out.append(Judged(col, d.key, d.label, kind, rule,
                          "window" if d.how == "worst" else d.how, d.unit))
    return out


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def object_values(df: pd.DataFrame, j: Judged) -> pd.DataFrame:
    """One row per object for one KPI: its window value, state and worst hour."""
    col = j.column
    if df is None or df.empty or col not in df.columns:
        return _empty()
    d = df.loc[df[col].notna() & df["site_id"].notna(), ["datetime", "site_id", "sector_id", col]]
    if d.empty:
        return _empty()
    site = d["site_id"].astype(str)
    sector = d["sector_id"].astype(str)
    no_sector = sector.str.endswith("-S0")
    d = d.assign(site_id=site, object_id=np.where(no_sector, site, sector))
    up = j.rule is not None and j.rule.direction == "up"

    if j.how == "day_sum":
        hourly = (d.groupby(["object_id", "datetime"], observed=True)[col].sum()
                  .reset_index().sort_values(["object_id", "datetime"]))
        rolled = (hourly.set_index("datetime").groupby("object_id")[col]
                  .rolling("24h").sum().reset_index())
        best = rolled.loc[rolled.groupby("object_id")[col].idxmax()].set_index("object_id")
        value, peak, peak_time = best[col], best[col], best["datetime"]
    else:
        how = "sum" if j.how == "count" else agg_how(col)
        value = d.groupby("object_id", observed=True)[col].agg(how)
        hourly = d.groupby(["object_id", "datetime"], observed=True)[col].agg(how).reset_index()
        g = hourly.groupby("object_id")[col]
        best = hourly.loc[g.idxmin() if up else g.idxmax()].set_index("object_id")
        peak, peak_time = best[col], best["datetime"]

    ids = value.index.astype(str)
    sites = d.drop_duplicates("object_id").set_index("object_id")["site_id"].reindex(ids)
    vals = value.to_numpy(dtype=float)
    if j.rule is None:
        sev = (vals > 0).astype(int)
        state = np.where(sev > 0, "Detected", "Normal")
    else:
        sev = np.array([severity(j.rule, v) for v in vals], dtype=int)
        state = np.array([STATE[s] for s in sev], dtype=object)
    is_site = ids == sites.to_numpy()
    return pd.DataFrame({
        "site_id": sites.to_numpy(), "object_id": ids,
        "object_type": np.where(is_site, "NodeB" if j.kind == "3G" else "Site", "Sector"),
        "kind": j.kind, "key": j.key, "label": j.label, "column": col, "unit": j.unit,
        "judged": j.rule is not None, "low_is_bad": up, "value": vals, "sev": sev,
        "state": state, "threshold": j.threshold,
        "peak": peak.reindex(value.index).to_numpy(dtype=float),
        "peak_time": pd.to_datetime(peak_time.reindex(value.index).to_numpy()),
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
    """Per site: its worst judged state, the KPI behind it, how many checks breach."""
    out = pd.DataFrame(index=pd.Index(sorted(sites), name="site_id"))
    j = objects[objects["judged"].astype(bool)] if len(objects) else objects
    if j.empty:
        out["sev"], out["label"], out["issues"], out["bad_kpis"] = -1, "", 0, ""
    else:
        worst = (j.sort_values("sev", ascending=False, kind="stable")
                 .drop_duplicates("site_id").set_index("site_id"))
        bad = j[j["sev"] > 0]
        out = out.join(worst[["sev", "label", "key", "object_id"]])
        out["sev"] = out["sev"].fillna(-1).astype(int)
        out["label"] = out["label"].where(out["sev"] >= 0, "")
        out["issues"] = bad.groupby("site_id").size().reindex(out.index).fillna(0).astype(int)
        out["bad_kpis"] = (bad.groupby("site_id")["label"]
                           .agg(lambda s: ", ".join(sorted(set(s))))
                           .reindex(out.index).fillna(""))
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
    """Worst first: critical before warning, then per KPI the worst value in the
    threshold's own direction — the Sites map's "worst sectors" order."""
    if frame.empty:
        return frame
    key = np.where(frame["low_is_bad"].astype(bool), frame["value"], -frame["value"])
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
        return {"key": key, "name": r["label"], "value": value_text(r["value"], r["unit"], r["judged"]),
                "sev": sev, "state": word, "judged": bool(r["judged"]),
                "threshold": r["threshold"], "object": r["object_id"],
                "peak_time": r["peak_time"], "n": int(len(f)),
                "bad": int((f["sev"] > 0).sum()),
                "note": f"{int((f['sev'] > 0).sum())} of {len(f)} objects above threshold"
                if r["judged"] else f"total over the window on {r['object_id']}"}

    extra = [k for k in rows["key"].unique() if k not in PRIMARY + SECONDARY]
    return ([tile(k) for k in PRIMARY],
            [tile(k) for k in SECONDARY if (rows["key"] == k).any()] + [tile(k) for k in extra])


def site_hours(frames, site_id: str) -> pd.DataFrame:
    """Hour by hour, the site's worst object on every window-judged KPI, and its state."""
    rows = []
    for _kind, df, judged in frames:
        if df is None or df.empty:
            continue
        d = df[df["site_id"].astype(str) == site_id]
        if d.empty:
            continue
        for j in judged:
            if j.rule is None or j.how != "window" or j.column not in d.columns:
                continue
            x = d[d[j.column].notna()]
            if x.empty:
                continue
            sector = x["sector_id"].astype(str)
            x = x.assign(object_id=np.where(sector.str.endswith("-S0"), site_id, sector))
            h = (x.groupby(["datetime", "object_id"], observed=True)[j.column]
                 .agg(agg_how(j.column)).reset_index())
            g = h.groupby("datetime")[j.column]
            w = h.loc[g.idxmin() if j.rule.direction == "up" else g.idxmax()]
            for t, oid, v in zip(w["datetime"], w["object_id"], w[j.column]):
                rows.append((t, j.key, j.label, oid, float(v), j.unit,
                             severity(j.rule, v), j.rule.direction == "up", j.threshold))
    return pd.DataFrame(rows, columns=["hour", "key", "label", "object_id", "value", "unit",
                                       "sev", "low", "threshold"]).sort_values(
        ["hour", "label"], kind="stable")
