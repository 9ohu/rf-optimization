"""The R5 ticket history (the CC Process export) as History of Tickets and
Tickets Details read it.

One row per ticket. The columns are found by their header, and where a header
is not there by the column the R5 team named for it (the letter): Group (A),
Ticket Status (B, Running / Completed), City (H), Site ID (I, the SD-check site
id), User (M, who closed the ticket), Created At (N), Sub District (Z, shown as
Sup District), HPSM Incident ID (BF, what a ticket is counted by), Closure Time
(DG) and Status (ER: Close, Sleep, Resolve, Pending, Reopen, Reject ...). For a
ticket in full also the Planned Site ID (BP), the Closure Code (BQ), the
Diagnostic Comment (BS), its Create Time (BT), the Sector Serving (FB), the
Longitude (FC), Latitude (FD) and RF Analysis (FE); and, by header only, the
Service Ticket ID (Ticket ID), SLA Target Time, SLA Status, IS CMC, Reopen
Count, Affected Services, the diagnostic Submit Time and the Expected
Resolution Time.

Nothing is invented: a value the file leaves empty stays empty, and the only
changes are to how a value reads — "Maysan / Emarah" is the city Amarah,
"hw.shams.aldin.ali" the user Shams Aldin Ali.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# field -> (the headers it goes by, the column letter the team named, or None
# when only the header may say)
FIELDS = {
    "group": (("group",), "A"),
    "ticket_status": (("ticket status",), "B"),
    "city": (("city",), "H"),
    "site_id": (("site id(sd check_site_id)",), "I"),
    "user": (("user",), "M"),
    "create_time": (("created at", "create time"), "N"),
    "sup_district": (("sub district", "sup district"), "Z"),
    "hpsm_id": (("hpsm incident id",), "BF"),
    "closure_time": (("closure time",), "DG"),
    "status": (("status",), "ER"),
    # the ticket in full (Tickets Details)
    "planned_site": (("site id(sd check)",), "BP"),
    "closure_code": (("closure code(incident diagnostic)",), "BQ"),
    "comment": (("diagnostic comment(incident diagnostic)",), "BS"),
    "diag_create": (("createtime(incident diagnostic)",), "BT"),
    "sector": (("sector serving",), "FB"),
    "longitude": (("longitude",), "FC"),
    "latitude": (("latitude",), "FD"),
    "rf_analysis": (("rf analysis",), "FE"),
    "service_ticket_id": (("ticket id",), None),
    "sla_target": (("sla target time",), None),
    "sla_status": (("sla status",), None),
    "is_cmc": (("is cmc",), None),
    "reopen": (("reopen count",), None),
    "affected": (("affected services", "affected"), None),
    "diag_submit": (("submittime(incident diagnostic)",), None),
    "expected": (("expected resolution time",), None),
}
TIMES = ("create_time", "closure_time", "diag_create", "diag_submit", "sla_target", "expected")
REQUIRED = ("hpsm_id", "status", "city", "site_id", "user")
CLOSED, PENDING, IN_PROGRESS, SLEEP = "Closed", "Pending", "In Progress", "Sleep"
STATES = (CLOSED, PENDING, IN_PROGRESS, SLEEP)

# the R5 cities as the export spells them (the part after "Governorate /")
_CITY = {"basrah": "Basrah", "basra": "Basrah", "emarah": "Amarah", "amarah": "Amarah",
         "nassriya": "Nasiriyah", "nasiriya": "Nasiriyah", "nasiriyah": "Nasiriyah",
         "nassiriya": "Nasiriyah", "samawa": "Samawah", "samawah": "Samawah"}


class HistoryFormatError(ValueError):
    """The file is not a ticket history (the message says what is missing)."""


def _letter_index(letter: str) -> int:
    n = 0
    for ch in letter.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip().lower()


def columns_of(headers: list) -> dict:
    """field -> the header it is read from: by name, else by the team's letter.
    The letters only stand in on an export that is recognisably the CC Process
    one (at least three of its headers found by name) — never on any sheet."""
    names: dict = {}
    for h in headers:
        names.setdefault(_norm(h), h)
    out = {}
    for field, (aliases, _) in FIELDS.items():
        hit = next((names[a] for a in aliases if a in names), None)
        if hit is not None:
            out[field] = hit
    if len(out) < 3:
        return out
    taken = set(out.values())
    for field, (_, letter) in FIELDS.items():
        if letter is None:
            continue
        j = _letter_index(letter)
        if field not in out and j < len(headers) and headers[j] not in taken:
            out[field] = headers[j]
            taken.add(headers[j])
    return out


def city_name(raw) -> str:
    """"Maysan / Emarah" -> "Amarah", "Thaiqar / Nassriya" -> "Nasiriyah"."""
    text = str(raw or "").replace("\xa0", " ").strip()
    if not text or text.lower() in ("nan", "none"):
        return ""
    part = text.split("/")[-1].strip()
    return _CITY.get(part.lower(), part)


def user_name(raw) -> str:
    """"hw.shams.aldin.ali" -> "Shams Aldin Ali" (the login, read as a name)."""
    text = str(raw or "").replace("\xa0", " ").strip()
    if not text or text.lower() in ("nan", "none"):
        return ""
    text = re.sub(r"^(hw|user)[.:]", "", text, flags=re.I)
    return " ".join(w[:1].upper() + w[1:] for w in re.split(r"[._\s]+", text) if w)


def _clean(s: pd.Series) -> pd.Series:
    s = s.fillna("").astype(str).str.replace("\xa0", " ", regex=False).str.strip()
    return s.mask(s.str.lower().isin(["", "nan", "none", "nat"]), "")


def state_of(ticket_status: pd.Series, status: pd.Series) -> pd.Series:
    """Closed (the ticket is Completed), Sleep, Pending, or In Progress (running
    in any other status: resolved and waiting, reopened, rejected ...)."""
    ts, st = ticket_status.str.lower(), status.str.lower()
    out = pd.Series(IN_PROGRESS, index=status.index)
    out[ts.eq("completed") | (ts.eq("") & st.isin(["close", "closed"]))] = CLOSED
    out[~out.eq(CLOSED) & st.eq("sleep")] = SLEEP
    out[~out.eq(CLOSED) & st.eq("pending")] = PENDING
    return out


def parse_history(raw: pd.DataFrame) -> pd.DataFrame:
    """The ticket frame the page reads, or HistoryFormatError."""
    raw = raw.copy()
    raw.columns = [str(c) for c in raw.columns]
    cols = columns_of(list(raw.columns))
    missing = [f for f in REQUIRED if f not in cols]
    if missing:
        names = {"hpsm_id": "HPSM Incident ID (BF)", "status": "Status (ER)",
                 "city": "City (H)", "site_id": "Site ID (I)", "user": "User (M)"}
        raise HistoryFormatError("no column for " + ", ".join(names[m] for m in missing))
    out = pd.DataFrame(index=raw.index)
    for field in FIELDS:
        out[field] = _clean(raw[cols[field]]) if field in cols else ""
    out = out[out["hpsm_id"].ne("")]
    if out.empty:
        raise HistoryFormatError("no ticket with an HPSM Incident ID")
    out["site_id"] = out["site_id"].str.upper()
    out["city"] = out["city"].map(city_name)
    out["user"] = out["user"].map(user_name)
    for t in TIMES:
        out[t] = pd.to_datetime(out[t].where(out[t].ne("")), errors="coerce")
    out["state"] = state_of(out["ticket_status"], out["status"])
    return out.reset_index(drop=True)


def read_history(path: str | Path) -> pd.DataFrame:
    """Read the export (xlsx / xls / csv) and parse it."""
    p = Path(path)
    try:
        if p.suffix.lower() == ".csv":
            raw = pd.read_csv(p, dtype=str, encoding="utf-8-sig")
        else:
            try:
                raw = pd.read_excel(p, sheet_name=0, dtype=str, engine="calamine")
            except (ImportError, ValueError):
                raw = pd.read_excel(p, sheet_name=0, dtype=str)
    except Exception as exc:                          # unreadable workbook / CSV
        raise HistoryFormatError(f"could not read the file ({exc})") from exc
    return parse_history(raw)


def summary(df: pd.DataFrame) -> dict:
    """What the Data Resources card says about the file."""
    t = df["create_time"].dropna()
    return {"tickets": int(df["hpsm_id"].nunique()),
            "closed": int(df.loc[df["state"].eq(CLOSED), "hpsm_id"].nunique()),
            "users": int(df["user"].replace("", pd.NA).dropna().nunique()),
            "sites": int(df.loc[~df["site_id"].isin(["", "0"]), "site_id"].nunique()),
            "start": f"{t.min():%Y-%m-%d}" if len(t) else "",
            "end": f"{t.max():%Y-%m-%d}" if len(t) else ""}
