"""Whether a site is on air now — the EP tracker together with the KMZ.

The KMZ is the R5 site list as drawn in Google Earth, and it is not always
updated when a site goes on air. The EP (Engineering Parameter) tracker is
the operator's live cell list: a site that has cells in its active sheets,
with a position and no inactive status, is on air — whatever the KMZ still
says. So wherever the app asks whether a site is on air, a site the EP
lists as active is On Air; for every other site the KMZ's own status stands.

Only the EP's Region 5 rows are read for this — chosen by the EP's own Region
field, never by Site ID prefix — so a site outside Region 5 keeps its KMZ
status. Sites are matched on their Site ID.
"""

from __future__ import annotations

import pandas as pd

ON_AIR_STATUS, ON_AIR_STYLE = "On Air", "onair"
REGION_5 = "Region 5"                  # the EP's Region value for R5

# an EP status that says the cell is not carrying traffic; anything else on an
# active sheet (Activated, On Air, blank) is an active cell
_INACTIVE = ("deact", "inact", "not ", "off", "plan", "lock", "dismantl", "remov",
             "block", "down")


def _norm_region(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.split().str.join(" ").str.lower()


def ep_on_air_sites(ep: pd.DataFrame | None, region: str | None = REGION_5) -> frozenset:
    """The Site IDs the EP tracker lists as active: at least one cell on an
    active (not "Deactive") sheet, with a valid position and no inactive status.

    Only the rows whose Region is `region` ("Region 5"; spacing and case
    aside) are read; an EP without a Region field gives no site."""
    if ep is None or len(ep) == 0 or "site_id" not in ep.columns:
        return frozenset()
    d = ep
    if region is not None:
        if "region" not in d.columns:
            return frozenset()
        d = d[_norm_region(d["region"]).eq(" ".join(region.split()).lower())]
    if "_sheet" in d.columns:
        d = d[~d["_sheet"].astype(str).str.lower().str.contains("deactive")]
    sid = d["site_id"].fillna("").astype(str).str.strip().str.upper()
    ok = sid.ne("") & sid.ne("NAN")
    for c in ("latitude", "longitude"):
        if c in d.columns:
            v = pd.to_numeric(d[c], errors="coerce")
            ok &= v.notna() & v.ne(0)
    if "status" in d.columns:
        st = d["status"].fillna("").astype(str).str.strip().str.lower()
        ok &= ~st.str.startswith(_INACTIVE)
    return frozenset(sid[ok])


def apply_ep_status(frame: pd.DataFrame | None, on_air, *, status_col: str = "status",
                    air_col: str = "air") -> pd.DataFrame | None:
    """`frame` (KMZ sites, sectors or cells) with every site the EP lists as
    active set to On Air — its status and its air style both, so the analysis
    and the map read the same thing. Other rows keep the KMZ's status."""
    if frame is None or not on_air or len(frame) == 0 or "site_id" not in frame.columns:
        return frame
    out = frame.copy()
    hit = out["site_id"].astype(str).str.strip().str.upper().isin(on_air)
    if status_col in out.columns:
        out.loc[hit, status_col] = ON_AIR_STATUS
    if air_col in out.columns:
        out.loc[hit, air_col] = ON_AIR_STYLE
    return out


__all__ = ["ON_AIR_STATUS", "ON_AIR_STYLE", "REGION_5", "apply_ep_status", "ep_on_air_sites"]
