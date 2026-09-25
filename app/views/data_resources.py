"""Data Resources — the data the app works from, uploaded by the user, kept on disk.

Each of the six resources (Site Details Data (EP), KPI Data, KMZ Data,
Complaint Data, Coverage Data, History ticket) has ONE active dataset: the files
every page reads (`_resources`). There are no versions and no history.

Replace: select a file → it is stored and checked → its preview says what it
holds and which active files it replaces → Cancel, or Apply. Until Apply the
active data stays in use; on Apply the replaced files are deleted. KPI Data
takes any number of files: an upload adds them, a newer copy of an active
export replaces it, and the preview lets the user take a new file back out or
keep / remove each active file. The app never looks for files on the computer:
a dataset exists only once it is uploaded here.
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

import _resources as R
import _shared
from _ui import PALETTE, fmt_bytes, header, icon_img, title_html
from rfopt.resources import store as S

ss = st.session_state
R.ready()

CSS = """
<style>
.dr-intro { display: flex; align-items: center; gap: 14px; }
.dr-intro-ico { flex: 0 0 52px; height: 52px; border-radius: 12px; display: flex;
    align-items: center; justify-content: center; background: #0D2945; border: 1px solid #1E3A5F;
    box-shadow: 0 0 16px rgba(32, 191, 255, .25); }
.dr-intro-t { font-size: 21px; font-weight: 700; color: #F8FAFC; line-height: 1.2; }
.dr-intro-s { font-size: 12.5px; color: #CBD5E1; margin-top: 3px; }
.dr-save { display: flex; gap: 11px; align-items: flex-start; border: 1px solid var(--c);
    background: color-mix(in srgb, var(--c) 7%, #0B1F33); border-radius: 12px; padding: 10px 12px; }
.dr-save-t { color: var(--c); font-weight: 700; font-size: 13px; }
.dr-save-d { color: #CBD5E1; font-size: 11.5px; line-height: 1.45; margin-top: 2px; }
.dr-save-f { color: #64748B; font-size: 10.5px; margin-top: 3px; overflow-wrap: anywhere; }
.dr-top { display: flex; gap: 13px; align-items: flex-start; min-height: 66px; }
.dr-ico { flex: 0 0 54px; height: 54px; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; background: color-mix(in srgb, var(--c) 20%, #071525);
    box-shadow: 0 0 20px color-mix(in srgb, var(--c) 32%, transparent); }
.dr-t { font-size: 15px; font-weight: 700; color: #F1F5F9; }
.dr-d { font-size: 12.3px; color: #CBD5E1; line-height: 1.45; margin-top: 3px; }
.dr-pill { display: inline-flex; align-items: center; gap: 5px; padding: 2px 10px;
    border-radius: 999px; font-size: 12px; font-weight: 600; color: var(--c);
    background: color-mix(in srgb, var(--c) 15%, transparent);
    border: 1px solid color-mix(in srgb, var(--c) 45%, transparent); white-space: nowrap; }
.dr-pills { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 7px; }
.dr-meta { font-size: 12.5px; color: #CBD5E1; line-height: 1.6; }
.dr-meta b { color: #F1F5F9; font-weight: 600; }
.dr-cur { font-size: 12px; color: #E2E8F0; line-height: 1.5; margin-top: 2px; }
.dr-cur span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dr-cur small { color: #94A3B8; }
.dr-steps { display: flex; gap: 8px; flex-wrap: wrap; margin: 2px 0 10px; }
.dr-step { display: flex; align-items: center; gap: 7px; padding: 4px 11px; border-radius: 999px;
    border: 1px solid #1E3A5F; color: #64748B; font-size: 12px; font-weight: 600; }
.dr-step i { font-style: normal; width: 18px; height: 18px; border-radius: 50%; display: flex;
    align-items: center; justify-content: center; background: #16324F; color: #94A3B8; font-size: 11px; }
.dr-step.on { color: #F1F5F9; border-color: #1597FF; background: rgba(21, 151, 255, .12); }
.dr-step.on i { background: #1597FF; color: #fff; }
.dr-step.done { color: #22C55E; border-color: rgba(34, 197, 94, .45); }
.dr-step.done i { background: #22C55E; color: #071525; }
.dr-sec { font-size: 12px; font-weight: 700; color: #94A3B8; text-transform: uppercase;
    letter-spacing: .04em; margin: 10px 0 5px; }
.dr-files { display: flex; flex-direction: column; gap: 6px; }
.dr-file { display: grid; grid-template-columns: 24px minmax(0, 2.2fr) minmax(0, 1.1fr) 80px
    minmax(0, 3fr); gap: 10px; align-items: center; background: #071525; border: 1px solid #1E3A5F;
    border-left: 3px solid var(--c); border-radius: 10px; padding: 7px 10px; font-size: 12.3px;
    color: #E2E8F0; }
.dr-file span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dr-file small { color: #94A3B8; }
.dr-file .dr-role { color: var(--c); font-weight: 600; }
.dr-h { display: grid; grid-template-columns: var(--cols); gap: 10px; padding: 7px 10px;
    font-size: 12px; font-weight: 700; color: #CBD5E1; background: #0D2945;
    border: 1px solid #1E3A5F; border-radius: 10px; }
.dr-cell { font-size: 12.5px; color: #E2E8F0; display: flex; align-items: center; gap: 8px;
    min-height: 34px; overflow: hidden; }
.dr-cell span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dr-cell small { color: #64748B; font-size: 10.5px; display: block; }
.dr-type { padding: 3px 10px; border-radius: 999px; font-size: 11.5px; font-weight: 600;
    white-space: nowrap; color: #F1F5F9; background: color-mix(in srgb, var(--c) 30%, #071525);
    border: 1px solid color-mix(in srgb, var(--c) 55%, transparent); }
.dr-store { font-size: 12.3px; color: #CBD5E1; line-height: 1.7; }
.dr-store b { color: #F1F5F9; }
.dr-store code { font-size: 11px; color: #94A3B8; background: #071525; padding: 1px 5px;
    border-radius: 5px; overflow-wrap: anywhere; }
[class*="st-key-dr_row_"] { border-bottom: 1px solid #16324F; padding-bottom: 2px; }
[class*="st-key-dr_row_"] button { min-height: 30px; padding: 2px 9px; }
.st-key-rf_card_dr_confirm { border: 1px solid rgba(239, 68, 68, .6) !important; }
[class*="st-key-dr_krow_"] { margin-bottom: -8px; }
[class*="st-key-dr_krow_"] button { min-height: 32px; padding: 2px 9px; }
.dr-more { color: #94A3B8; font-size: 11.5px; }
.st-key-dr_confirm_yes button { background: #EF4444; border-color: #EF4444; color: #fff; }
.st-key-dr_confirm_yes button:hover { background: #DC2626; border-color: #DC2626; }
</style>
"""
GREEN, AMBER, GREY = PALETTE["excellent"], PALETTE["warning"], PALETTE["nodata"]
TABLE_COLS = [2.7, 1.5, 1.35, 0.9, 1.0, 2.2]
TABLE_GRID = "2.7fr 1.5fr 1.35fr 0.9fr 1fr 2.2fr"


def esc(x) -> str:
    return html.escape(str(x))


def summary_text(kind: str, f: S.StoredFile) -> str:
    s = f.summary or {}
    if not s:
        return "details on View"
    if kind == "ep":
        tech = " · ".join(f"{k} {v:,}" for k, v in s.get("by_tech", {}).items())
        return f"{s.get('sites', 0):,} sites · {s.get('cells', 0):,} cells" + (
            f" ({tech})" if tech else "")
    if kind == "kpi":
        return (f"{s.get('tech', '')} · {s.get('rows', 0):,} records · {s.get('kpis', 0)} KPIs · "
                f"{s.get('objects', 0):,} objects · {s.get('start', '')} → {s.get('end', '')}")
    if kind == "kmz":
        return f"{s.get('sites', 0):,} sites · {s.get('sectors', 0):,} sectors"
    if kind == "complaints":
        if "tickets" in s:
            return f"{s['tickets']:,} tickets"
        return f"{s.get('complaints', 0):,} complaints · {s.get('sites', 0):,} sites"
    if kind == "coverage":
        if "grids" in s:
            return f"{s['grids']:,} grids · {s.get('mrs', 0):,} MRs"
        return s.get("note", "")
    if kind == "history":
        return (f"{s.get('tickets', 0):,} tickets · {s.get('closed', 0):,} closed · "
                f"{s.get('start', '')} → {s.get('end', '')}")
    return ""


def open_panel(mode: str, kind: str) -> None:
    ss["dr_panel"] = (mode, kind)
    ss["dr_scroll"] = True
    ss.pop("dr_confirm", None)


def close_panel() -> None:
    ss.pop("dr_panel", None)


def flash(kind: str, text: str) -> None:
    ss["dr_flash"] = (kind, text)


def _drop_page_caches() -> None:
    """Whatever a page keeps of the data it read before is dropped, so every
    page reads the newly applied data on its next run."""
    for key in ("kpi_drawn", "kpi_trend_xlsx", "ka_sel", "ka_sel_obj", "ka_sel_kpi",
                "rx_out_pptx", "rx_out_xlsx", "wl_df", "ca_sel_tid"):
        ss.pop(key, None)


# --------------------------------------------------------------------------- #
# actions (callbacks: they run before the page is drawn again)
# --------------------------------------------------------------------------- #
def do_apply(kind: str) -> None:
    title = S.SPECS[kind].title
    try:
        res = S.apply(kind)
    except S.ResourceError as exc:
        flash("err", f"Not applied: {exc}")
        return
    _drop_page_caches()
    hl = S.health()
    names = ", ".join(f.name for f in res.files)
    if hl.state == "saved":
        flash("ok", f"{title} applied — active now: {names}. Used by "
                    f"{', '.join(S.SPECS[kind].used_by)}.")
    else:
        flash("err", f"{title} applied, but the store reports: {hl.detail}")
    ss.pop("dr_rejected", None)
    close_panel()


def do_cancel(kind: str) -> None:
    S.cancel(kind)
    flash("ok", f"The upload was cancelled; the active {S.SPECS[kind].title} is unchanged.")
    ss.pop("dr_rejected", None)
    close_panel()


def do_delete(kind: str, sha1: str) -> None:
    res = S.resource(kind)
    f = next((g for g in res.files if g.sha1 == sha1), None)
    try:
        S.remove(kind, sha1)
    except S.ResourceError as exc:
        flash("err", str(exc))
    else:
        _drop_page_caches()
        flash("ok", f"{f.name if f else 'The file'} was deleted from {S.SPECS[kind].title}.")
    ss.pop("dr_confirm", None)


def ask_delete(kind: str, sha1: str) -> None:
    ss["dr_confirm"] = (kind, sha1)


def do_unstage(kind: str, sha1: str) -> None:
    """Take one new file back out of the upload (KPI Data)."""
    S.unstage(kind, sha1)


def set_keep(kind: str, sha1: str, key: str) -> None:
    """Keep an active KPI file after Apply, or have Apply delete it."""
    S.set_drop(kind, sha1, not ss.get(key, True))


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
st.html(CSS)
header("Data Resources", "The data the app works from — uploaded by you, kept on disk")
try:
    REG = S.load()
except S.ResourceError as exc:
    st.error(f"The Data Resources store could not be read: {exc}")
    st.stop()
HL = S.health()

msg = ss.pop("dr_flash", None)
if msg:
    (st.success if msg[0] == "ok" else st.error)(msg[1])

c_intro, c_save = st.columns([2.5, 1.25], gap="medium", vertical_alignment="center")
c_intro.html('<div class="dr-intro"><span class="dr-intro-ico">'
             f'{icon_img("database", PALETTE["cyan"], 28)}</span><div>'
             '<div class="dr-intro-t">Data Resources</div>'
             '<div class="dr-intro-s">Upload each dataset here. Each resource has one active '
             "dataset: Sites, KPI Analysis, Draw Data, Report Export, Complaints and the "
             "Dashboard read it. A replacement only becomes active when you press Apply. The "
             "app never looks for files on your computer.</div></div></div>")
save_colour = {"saved": GREEN, "empty": GREY}.get(HL.state, PALETTE["critical"])
c_save.html(f'<div class="dr-save" style="--c:{save_colour}">'
            f'{icon_img("shield" if HL.state != "error" else "alert", save_colour, 26)}<div>'
            f'<div class="dr-save-t">{esc(HL.message)}</div>'
            f'<div class="dr-save-d">{esc(HL.detail)}</div>'
            f'<div class="dr-save-f">{HL.saved} of {len(S.KINDS)} resources active · '
            f'{esc(fmt_bytes(HL.total_bytes) or "0 B")} · {esc(HL.folder)}</div></div></div>')


# --------------------------------------------------------------------------- #
# the resource cards
# --------------------------------------------------------------------------- #
def card(kind: str) -> None:
    spec = S.SPECS[kind]
    res = REG.res(kind)
    missing = [f.name for f in res.files if not f.path.is_file()]
    pills = []
    if res.files and not missing:
        pills.append(("● Active", GREEN))
    elif res.files:
        pills.append(("● File missing", PALETTE["critical"]))
    else:
        pills.append(("Not loaded", GREY))
    if res.pending:
        pills.append(("● Upload awaiting Apply", AMBER))
    if res.files:
        shown = res.files if len(res.files) <= 4 else res.files[:3]
        cur = "".join(f'<span title="{esc(f.name)}">{esc(f.name)} <small>· {esc(fmt_bytes(f.size))}'
                      + (f" · {esc(f.role)}" if kind in ("kpi", "complaints") else "")
                      + "</small></span>" for f in shown)
        if len(shown) < len(res.files):
            cur += (f'<span class="dr-more">+ {len(res.files) - len(shown)} more files '
                    "— see Active Data</span>")
        meta = (f'<div class="dr-cur">{cur}</div>'
                f'<div class="dr-meta">Uploaded: <b>{esc(R.when(max(f.uploaded_at for f in res.files)))}</b>'
                f"</div>")
    else:
        meta = '<div class="dr-meta">No file uploaded yet.</div>'
    with st.container(key=f"rf_card_dr_{kind}", border=True):
        st.html(f'<div class="dr-top" style="--c:{spec.colour}"><span class="dr-ico">'
                f'{icon_img(spec.icon, spec.colour, 27)}</span><div>'
                f'<div class="dr-t">{esc(spec.title)}</div>'
                f'<div class="dr-d">{esc(spec.description)}</div></div></div>')
        left, right = st.columns([1.7, 1], gap="small", vertical_alignment="center")
        left.html('<div class="dr-pills">' + "".join(
            f'<span class="dr-pill" style="--c:{c}">{esc(t)}</span>' for t, c in pills)
            + "</div>" + meta)
        with right:
            if res.pending:
                st.button("Review", icon=":material/fact_check:", key=f"dr_rev_{kind}",
                          type="primary", width="stretch", on_click=open_panel,
                          args=("replace", kind))
            else:
                many = kind in S.MANY and res.files
                st.button("Add / Replace" if many else "Replace" if res.files else "Upload",
                          icon=":material/add:" if many else ":material/sync:" if res.files
                          else ":material/upload:",
                          key=f"dr_rep_{kind}", width="stretch",
                          type="secondary" if res.files else "primary", on_click=open_panel,
                          args=("replace", kind))
            st.button("View", icon=":material/visibility:", key=f"dr_view_{kind}",
                      width="stretch", disabled=not res.files, on_click=open_panel,
                      args=("view", kind))


def storage_card() -> None:
    n_files = sum(len(REG.res(k).files) for k in S.KINDS)
    with st.container(key="rf_card_dr_storage", border=True):
        st.html(f'<div class="dr-top" style="--c:{PALETTE["cyan"]}"><span class="dr-ico">'
                f'{icon_img("database", PALETTE["cyan"], 27)}</span><div>'
                '<div class="dr-t">Storage</div><div class="dr-d">The app\'s own folder: the '
                "active data is kept here and read again every time the app starts.</div>"
                "</div></div>"
                f'<div class="dr-store"><b>{n_files}</b> active file{"s" if n_files != 1 else ""}'
                f' · <b>{esc(fmt_bytes(HL.total_bytes) or "0 B")}</b> on disk<br>'
                f'Folder: <code>{esc(HL.folder)}</code><br>'
                f'Saved: <b>{esc(R.when(REG.saved_at))}</b></div>')


row1 = st.columns(3, gap="small")
for col, kind in zip(row1, ("ep", "kpi", "kmz")):
    with col:
        card(kind)
row2 = st.columns(3, gap="small")
for col, kind in zip(row2, ("complaints", "coverage", "history")):
    with col:
        card(kind)
row3 = st.columns(3, gap="small")
with row3[0]:
    storage_card()


# --------------------------------------------------------------------------- #
# the panel: Replace (select → validate → preview → apply), or View
# --------------------------------------------------------------------------- #
def steps_html(step: int) -> str:
    names = ["Select File", "Validate", "Preview", "Apply"]
    return '<div class="dr-steps">' + "".join(
        f'<span class="dr-step {"done" if k < step else "on" if k == step else ""}">'
        f'<i>{"✓" if k < step else k + 1}</i>{n}</span>' for k, n in enumerate(names)) + "</div>"


def file_rows(kind: str, fs: list, colour: str) -> str:
    return '<div class="dr-files">' + "".join(
        f'<div class="dr-file" style="--c:{colour}">{icon_img("file", colour, 18)}'
        f'<span title="{esc(f.name)}">{esc(f.name)}</span>'
        f'<span class="dr-role">{esc(f.role or "—")}</span>'
        f'<span>{esc(fmt_bytes(f.size))}</span>'
        f'<span title="{esc(summary_text(kind, f))}"><small>{esc(summary_text(kind, f))}'
        f'</small></span></div>' for f in fs) + "</div>"


def replace_panel(kind: str) -> None:
    spec = S.SPECS[kind]
    res = REG.res(kind)
    with st.container(key="rf_card_dr_panel", border=True):
        h1, h2 = st.columns([6, 1], vertical_alignment="center")
        verb = ("Add or replace " if kind in S.MANY else "Replace ") if res.files else "Upload "
        h1.html(title_html(verb + spec.title, spec.icon,
                           subtitle="the active data stays in use until you press Apply"))
        h2.button("Close", icon=":material/close:", key="dr_close", width="stretch",
                  on_click=close_panel)
        st.html(steps_html(2 if res.pending else 0))
        up_col, rev_col = st.columns([1, 1.55], gap="medium")
        with up_col:
            gen = ss.get("dr_gen", 0)
            label = (f"Select {'files' if spec.multi else 'a file'} "
                     f"({', '.join('.' + t for t in spec.types)})")
            ups = st.file_uploader(label, type=list(spec.types),
                                   accept_multiple_files=spec.multi, key=f"dr_up_{kind}_{gen}")
            ups = [u for u in (ups if isinstance(ups, list) else [ups]) if u is not None]
            if ups:
                with st.spinner(f"Validating {len(ups)} file(s)…"):
                    _, bad = R.stage_files(kind, [(u.name, u.getvalue()) for u in ups])
                ss.setdefault("dr_rejected", {})[kind] = bad
                ss["dr_gen"] = gen + 1
                st.rerun()
            if kind == "kpi":
                st.caption("Add as many KPI files as you need: 2G, 3G and 4G, other KPI "
                           "sets, other areas or periods. They are added to the active ones, "
                           "and the files of one technology are read together as one export. "
                           "A newer copy of an active export (all of its KPIs and cells, "
                           "most of its hours again and later ones) replaces it — tick or "
                           "untick Keep on any active file before Apply.")
            elif kind == "complaints":
                st.caption("The Daily Target (Ticket ID, Site ID, Problem Time) and, "
                           "optionally, a CC Process complaint history — each replaces its own.")
            elif kind == "coverage":
                st.caption("Coverage grids (Latitude, Longitude, RSRP) with LAT and log files, "
                           "as one dataset: Apply replaces the whole active Coverage Data.")
            else:
                st.caption(f"One file: Apply replaces the active {spec.title}.")
        with rev_col:
            for name, why in (ss.get("dr_rejected") or {}).get(kind) or []:
                if why.startswith("it is already active"):
                    st.info(f"**{name}** was not added again: {why}.",
                            icon=":material/info:")
                else:
                    st.error(f"**{name}** is not valid {spec.title}: {why}",
                             icon=":material/block:")
            if not res.pending:
                st.html('<div class="rf-note">Select a file: it is validated and previewed '
                        "here. Nothing changes for the other pages until you press Apply."
                        "</div>")
                return
            if kind in S.MANY:
                many_review(kind, res)
            else:
                st.html('<div class="dr-sec">New file' + ("s" if len(res.pending) > 1 else "")
                        + " — validated</div>" + file_rows(kind, res.pending, AMBER))
                gone, stay = res.replaced(), res.kept()
                if gone:
                    st.html('<div class="dr-sec">Replaced — deleted on Apply</div>'
                            + file_rows(kind, gone, PALETTE["critical"]))
                if stay:
                    st.html('<div class="dr-sec">Stays active</div>'
                            + file_rows(kind, stay, GREEN))
            if not res.files:
                st.html('<div class="rf-note">Nothing is active yet: this becomes the '
                        f"{spec.title}.</div>")
            b1, b2 = st.columns(2)
            b1.button("Cancel", icon=":material/close:", key=f"dr_cancel_{kind}",
                      width="stretch", on_click=do_cancel, args=(kind,),
                      help="Throw the upload away; the active data stays")
            b2.button("Apply", icon=":material/check_circle:", type="primary",
                      key=f"dr_apply_{kind}", width="stretch", on_click=do_apply,
                      args=(kind,), help="Make it the active data for every page")


def many_review(kind: str, res: S.Resource) -> None:
    """KPI Data: each new file can be taken back out, each active file kept or
    deleted on Apply (a newer copy of it comes in ticked for deletion)."""
    st.html(f'<div class="dr-sec">New file{"s" if len(res.pending) > 1 else ""} — validated '
            f"({len(res.pending)})</div>")
    for f in res.pending:
        with st.container(key=f"dr_krow_new_{f.sha1[:12]}"):
            a, b = st.columns([6.2, 1.3], gap="small", vertical_alignment="center")
            a.html(file_rows(kind, [f], AMBER))
            b.button("Remove", icon=":material/close:", key=f"dr_unstage_{f.sha1[:12]}",
                     width="stretch", on_click=do_unstage, args=(kind, f.sha1),
                     help="Take this file out of the upload")
    gone = {f.sha1 for f in res.replaced()}
    for title, fs, colour in (
            ("Replaced — deleted on Apply", [f for f in res.files if f.sha1 in gone],
             PALETTE["critical"]),
            ("Stays active", [f for f in res.files if f.sha1 not in gone], GREEN)):
        if not fs:
            continue
        st.html(f'<div class="dr-sec">{title}</div>')
        for f in fs:
            key = f"dr_keep_{f.sha1[:12]}"
            ss[key] = f.sha1 not in gone          # the store decides what the box shows
            with st.container(key=f"dr_krow_{f.sha1[:12]}"):
                a, b = st.columns([6.2, 1.3], gap="small", vertical_alignment="center")
                a.html(file_rows(kind, [f], colour))
                b.checkbox("Keep", key=key, on_change=set_keep, args=(kind, f.sha1, key),
                           help="Ticked: the file stays active after Apply. Unticked: Apply "
                                "deletes it.")
    after = res.after_apply()
    n_gone = len(res.replaced())
    st.html(f'<div class="dr-meta" style="margin:6px 0">After Apply: <b>{len(after)}</b> '
            f'{esc(S.SPECS[kind].title)} file{"s" if len(after) != 1 else ""} active — '
            f"{len(res.pending)} new, {len(res.kept())} kept"
            + (f", <b>{n_gone}</b> deleted" if n_gone else "") + "</div>")


def view_panel(kind: str) -> None:
    spec = S.SPECS[kind]
    res = REG.res(kind)
    if not res.files:
        close_panel()
        return
    with st.container(key="rf_card_dr_panel", border=True):
        h1, h2 = st.columns([6, 1], vertical_alignment="center")
        h1.html(title_html(spec.title, spec.icon,
                           subtitle=f"active · applied {R.when(res.applied_at)}"))
        h2.button("Close", icon=":material/close:", key="dr_close", width="stretch",
                  on_click=close_panel)
        if any(not f.summary for f in res.files):
            with st.spinner("Reading what the file holds (once)…"):
                for f in res.files:
                    R.summary_of(kind, f)
            res = S.resource(kind)
        st.html(file_rows(kind, res.files, spec.colour))
        st.html(f'<div class="dr-meta" style="margin-top:8px">{len(res.files)} file(s) · '
                f'{esc(fmt_bytes(res.size))} · used by: {esc(", ".join(spec.used_by))}</div>')
        cols = st.columns(min(len(res.files), 4) or 1)
        for k, f in enumerate(res.files):
            cols[k % len(cols)].download_button(
                f"Download {f.name}", data=lambda p=f.path: p.read_bytes(), file_name=f.name,
                icon=":material/download:", key=f"dr_dl_view_{kind}_{k}",
                on_click="ignore", width="stretch", disabled=not f.path.is_file())
        if st.toggle("Show a data preview", key=f"dr_prev_{kind}"):
            preview(kind, res.files)


def preview(kind: str, fs: list) -> None:
    for f in fs:
        if not f.path.is_file():
            continue
        try:
            if kind == "ep":
                df = _shared.load_ep_all(str(f.path))
                cols = [c for c in ("site_id", "enodeb_name", "technology", "cell_name",
                                    "latitude", "longitude", "city", "sub_district",
                                    "azimuth_deg", "status") if c in df.columns]
                df = df[cols]
            elif kind == "kpi":
                df = R.kpi_index(str(f.path)).drop(columns=["prefix"], errors="ignore")
            elif kind == "kmz":
                from rfopt.ingest.kmz_sites import load_kmz_sites
                df = load_kmz_sites(str(f.path), region=None).sectors
            elif kind == "complaints":
                from rfopt.complaints.worklist import read_source
                df = read_source(str(f.path))
            elif kind == "history":
                df = R.history()
            else:
                cov = R.coverage().get(f.sha1)
                if cov is None:
                    st.caption(f"{f.name}: {f.role} — kept with Coverage Data, no preview.")
                    continue
                df = pd.DataFrame({"latitude": cov.lat[:500], "longitude": cov.lon[:500],
                                   "rsrp_dbm": cov.rsrp[:500], "mr": cov.mr[:500]})
        except Exception as exc:
            st.warning(f"{f.name}: no preview ({exc})")
            continue
        st.caption(f"{f.name} · {len(df):,} rows · first 200 shown")
        st.dataframe(df.head(200), hide_index=True, width="stretch", height=260)


panel = ss.get("dr_panel")
if panel and len(panel) == 2:
    mode, pkind = panel
    if mode == "replace":
        replace_panel(pkind)
    else:
        view_panel(pkind)
    if ss.pop("dr_scroll", False):
        st.html("<script>setTimeout(function () { var el = window.parent.document.querySelector("
                "'.st-key-rf_card_dr_panel') || document.querySelector('.st-key-rf_card_dr_panel');"
                " if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'}); }, 250);"
                "</script>", unsafe_allow_javascript=True)
elif panel:
    close_panel()


# --------------------------------------------------------------------------- #
# Active Data: what the pages use right now
# --------------------------------------------------------------------------- #
rows = [(kind, f) for kind in S.KINDS for f in REG.res(kind).files]
with st.container(key="rf_card_dr_active", border=True):
    st.html(title_html("Active Data", "database",
                       subtitle="the files every page uses right now — one active dataset "
                                "per resource"))
    if not rows:
        st.html('<div class="rf-note">No data yet: upload each resource above.</div>')
    else:
        st.html(f'<div class="dr-h" style="--cols:{TABLE_GRID}"><span>File Name</span>'
                "<span>Data Type</span><span>Upload Date</span><span>File Size</span>"
                "<span>Status</span><span>Actions</span></div>")
    confirm = ss.get("dr_confirm")
    for i, (kind, f) in enumerate(rows):
        spec = S.SPECS[kind]
        present = f.path.is_file()
        with st.container(key=f"dr_row_{i}"):
            c = st.columns(TABLE_COLS, gap="small", vertical_alignment="center")
            c[0].html(f'<div class="dr-cell">{icon_img("file", spec.colour, 18)}<div>'
                      f'<span title="{esc(f.name)}">{esc(f.name)}</span>'
                      f"<small>{esc(f.role)}</small></div></div>")
            c[1].html(f'<div class="dr-cell"><span class="dr-type" style="--c:{spec.colour}">'
                      f"{esc(spec.short)}</span></div>")
            c[2].html(f'<div class="dr-cell">{esc(R.when(f.uploaded_at))}</div>')
            c[3].html(f'<div class="dr-cell">{esc(fmt_bytes(f.size))}</div>')
            status, colour = ("Active", GREEN) if present else ("File missing", PALETTE["critical"])
            c[4].html(f'<div class="dr-cell"><span class="dr-pill" style="--c:{colour}">'
                      f"{status}</span></div>")
            with c[5], st.container(horizontal=True, gap="small"):
                st.button(":material/visibility:", key=f"dr_rv_{i}", help="View",
                          on_click=open_panel, args=("view", kind))
                st.button(":material/sync:", key=f"dr_rr_{i}",
                          help=(f"Add or replace {spec.title} files" if kind in S.MANY
                                else f"Replace {spec.title}"),
                          on_click=open_panel, args=("replace", kind))
                st.download_button(":material/download:", data=lambda p=f.path: p.read_bytes(),
                                   file_name=f.name, key=f"dr_rd_{i}", on_click="ignore",
                                   help=f"Download {f.name}", disabled=not present)
                st.button(":material/delete:", key=f"dr_rx_{i}", help="Delete this file",
                          on_click=ask_delete, args=(kind, f.sha1))
        if confirm == (kind, f.sha1):
            with st.container(key="rf_card_dr_confirm", border=True):
                st.html(f'<div class="dr-meta">Delete <b>{esc(f.name)}</b> from '
                        f"<b>{esc(spec.title)}</b>? The pages lose this data until you upload "
                        "it again. This cannot be undone.</div>")
                k1, k2, _ = st.columns([1.3, 1, 3])
                k1.button("Delete", icon=":material/delete_forever:", key="dr_confirm_yes",
                          type="primary", width="stretch", on_click=do_delete,
                          args=(kind, f.sha1))
                k2.button("Cancel", key="dr_confirm_no", width="stretch",
                          on_click=lambda: ss.pop("dr_confirm", None))
