"""The RF Optimization interface kit: one palette, one card, one header.

Every page builds its chrome from these so the product reads as one tool.
Presentation only — nothing here reads data or decides anything; pages pass
in the numbers they already compute.
"""

from __future__ import annotations

import base64
import datetime as dt
import html
from functools import lru_cache

import streamlit as st

# the one status palette. A status never gets a different colour elsewhere.
PALETTE = {
    "bg": "#071525", "panel": "#0B1F33", "panel2": "#0D2945", "line": "#1E3A5F",
    "text": "#E2E8F0", "muted": "#94A3B8", "blue": "#1597FF", "cyan": "#20BFFF",
    "excellent": "#22C55E", "good": "#4ADE80", "warning": "#FACC15",
    "poor": "#FB923C", "critical": "#EF4444", "nodata": "#94A3B8",
}
TONES = {"blue": PALETTE["cyan"], "green": PALETTE["excellent"],
         "amber": PALETTE["warning"], "orange": PALETTE["poor"],
         "red": PALETTE["critical"], "gray": PALETTE["nodata"]}

_S = ('viewBox="0 0 24 24" fill="none" stroke="currentColor" '
      'stroke-linecap="round" stroke-linejoin="round"')
_TOWER = ('<path d="M12 10.5V21"/><path d="M8.6 21 12 10.5 15.4 21"/>'
          '<path d="M9.7 17.3h4.6"/><circle cx="12" cy="8.3" r="1.7"/>'
          '<path d="M8.3 4.8a5 5 0 0 0 0 7"/><path d="M15.7 4.8a5 5 0 0 1 0 7"/>'
          '<path d="M5.7 2.6a8.6 8.6 0 0 0 0 11.4"/>'
          '<path d="M18.3 2.6a8.6 8.6 0 0 1 0 11.4"/>')
# raw SVG, for the map iframe. The Streamlit page strips inline <svg> from
# st.html, so the page uses `icon_img` instead.
ICONS = {
    "tower": f'<svg {_S} stroke-width="1.8">{_TOWER}</svg>',
    "check": f'<svg {_S} stroke-width="2.6"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    "alert": f'<svg {_S} stroke-width="2.8"><path d="M12 5.5v9"/><path d="M12 18.6h.01"/></svg>',
    "x": f'<svg {_S} stroke-width="2.6"><path d="M7 7l10 10M17 7 7 17"/></svg>',
    "info": f'<svg {_S} stroke-width="2"><circle cx="12" cy="12" r="9"/>'
            '<path d="M12 11v6"/><path d="M12 7.5h.01"/></svg>',
    "calendar": f'<svg {_S} stroke-width="1.9"><rect x="3.5" y="5" width="17" height="15" rx="2"/>'
                '<path d="M3.5 9.5h17M8 3v4M16 3v4"/></svg>',
    "pin": f'<svg {_S} stroke-width="1.9"><path d="M12 21s-6.5-5.4-6.5-11a6.5 6.5 0 0 1 13 0c0 5.6-6.5 11-6.5 11z"/>'
           '<circle cx="12" cy="10" r="2.4"/></svg>',
    "layers": f'<svg {_S} stroke-width="1.9"><path d="m12 3 9 5-9 5-9-5 9-5z"/>'
              '<path d="m3 13 9 5 9-5"/></svg>',
    "file": f'<svg {_S} stroke-width="1.9"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
            '<path d="M14 3v5h5"/></svg>',
    "chart": f'<svg {_S} stroke-width="1.9"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
    "ruler": f'<svg {_S} stroke-width="1.9"><path d="M4 16 16 4l4 4L8 20z"/>'
             '<path d="m7.5 12.5 2 2M10.5 9.5l2 2M13.5 6.5l2 2"/></svg>',
    "up": f'<svg {_S} stroke-width="2.2"><path d="m4 17 6-6 4 4 6-7"/><path d="M14 8h6v6"/></svg>',
    "down": f'<svg {_S} stroke-width="2.2"><path d="m4 7 6 6 4-4 6 7"/><path d="M14 16h6v-6"/></svg>',
    "flat": f'<svg {_S} stroke-width="2.2"><path d="M4 12h15"/><path d="m15 8 4 4-4 4"/></svg>',
    "target": f'<svg {_S} stroke-width="2"><circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="1.6"/>'
              '<path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>',
    "user": f'<svg {_S} stroke-width="1.9"><circle cx="12" cy="8" r="3.6"/>'
            '<path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/></svg>',
    "clock": f'<svg {_S} stroke-width="1.9"><circle cx="12" cy="12" r="8.5"/>'
             '<path d="M12 7.5V12l3 2"/></svg>',
    "reopen": f'<svg {_S} stroke-width="2"><path d="M20 11a8 8 0 1 0-2.3 5.7"/>'
              '<path d="M20 4.5V11h-6.5"/></svg>',
    "sleep": f'<svg {_S} stroke-width="1.9"><path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/></svg>',
    "signal": f'<svg {_S} stroke-width="2.2"><path d="M5 20v-3M10 20v-7M15 20V9M20 20V4"/></svg>',
    "ticket": f'<svg {_S} stroke-width="1.9"><path d="M3.5 8.5V6.5a1.5 1.5 0 0 1 1.5-1.5h14a1.5 1.5 0 0 1 '
              '1.5 1.5v2a2.5 2.5 0 0 0 0 5v2a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5v-2a2.5 2.5 0 0 0 0-5z"/>'
              '<path d="M14.5 5v14"/></svg>',
    "pulse": f'<svg {_S} stroke-width="2"><path d="M3 12h4l2.5-6 5 12 2.5-6H21"/></svg>',
    "database": f'<svg {_S} stroke-width="1.9"><ellipse cx="12" cy="5.5" rx="7.5" ry="3"/>'
                '<path d="M4.5 5.5v6.5c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V5.5"/>'
                '<path d="M4.5 12v6.5c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V12"/></svg>',
    "shield": f'<svg {_S} stroke-width="1.9"><path d="M12 3 4.5 6v5.5c0 4.6 3.2 8 7.5 9.5 '
              '4.3-1.5 7.5-4.9 7.5-9.5V6z"/><path d="m8.8 12 2.3 2.3 4.3-4.6"/></svg>',
    "history": f'<svg {_S} stroke-width="1.9"><circle cx="12" cy="12" r="8.5"/>'
               '<path d="M12 7.5V12l3 2"/></svg>',
}

# the sidebar brand (st.logo takes an SVG string)
LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="210" height="36" '
    'viewBox="0 0 210 36"><g fill="none" stroke="#20BFFF" stroke-width="1.9" '
    'stroke-linecap="round" stroke-linejoin="round" '
    f'transform="translate(1 2) scale(1.33)">{_TOWER}</g>'
    '<text x="42" y="17" font-family="Segoe UI, system-ui, sans-serif" '
    'font-size="15.5" font-weight="700" fill="#F1F5F9">RF Optimization</text>'
    '<text x="42" y="31" font-family="Segoe UI, system-ui, sans-serif" '
    'font-size="10.5" fill="#94A3B8">Copyright &#169; Shamsaldin Ali</text></svg>')


@lru_cache(maxsize=256)
def icon_img(name: str, colour: str = "#20BFFF", size: int = 20) -> str:
    """An icon as an <img> data URI in one colour — what st.html lets through."""
    svg = (ICONS.get(name, ICONS["info"]).replace("currentColor", colour)
           .replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1))
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return (f'<img class="rf-ic" src="data:image/svg+xml;base64,{b64}" '
            f'width="{size}" height="{size}" alt="">')


_CSS = """
/* ---- page frame ------------------------------------------------------ */
/* Streamlit's toolbar strip is fixed over the top of the page. The content
   starts below it, and the strip lets clicks through everywhere but its own
   buttons — it used to sit on the header's search box and swallow every
   click, so a site or location could not be typed in. */
[data-testid="stMainBlockContainer"] { padding-top: 4rem; padding-bottom: 2.2rem; }
header[data-testid="stHeader"] { background: transparent; pointer-events: none; }
header[data-testid="stHeader"] button, header[data-testid="stHeader"] a {
    pointer-events: auto;
}
img.rf-ic { display: block; flex: 0 0 auto; }
/* one loading for the whole system: the startup screen. Streamlit's own
   running indicator (top-right, with Stop) is not shown, and the startup's
   message channel takes no room */
[data-testid="stStatusWidget"] { display: none !important; }
.st-key-rf_startup_bus { display: none !important; }

/* ---- top header bar -------------------------------------------------- */
.st-key-rf_header {
    background: linear-gradient(180deg, #0D2945 0%, #0B1F33 100%);
    border: 1px solid #1E3A5F; border-radius: 12px; padding: 9px 14px;
}
.st-key-rf_header [data-testid="stTextInputRootElement"] {
    background: #071525; border-color: #1E3A5F; border-radius: 10px;
}
.rf-brand { display: flex; align-items: center; gap: 11px; white-space: nowrap; }
.rf-brand-ico { display: flex; filter: drop-shadow(0 0 6px rgba(32, 191, 255, .45)); }
.rf-brand-t { font-size: 17px; font-weight: 700; color: #F1F5F9; line-height: 1.15; }
.rf-brand-s { font-size: 12px; color: #94A3B8; }
.rf-chips { display: flex; gap: 8px; white-space: nowrap; flex-wrap: wrap; }
.rf-chip {
    display: flex; align-items: center; gap: 7px; background: #071525;
    border: 1px solid #1E3A5F; border-radius: 10px; padding: 7px 11px;
    font-size: 12.5px; color: #CBD5E1;
}

/* ---- KPI summary cards ----------------------------------------------- */
[data-testid="stHtml"]:has(> .rf-kpi) { container-type: inline-size; }
.rf-kpi {
    --tone: #20BFFF; display: flex; gap: 12px; align-items: center;
    background: #0B1F33; border: 1px solid #1E3A5F; border-radius: 12px;
    padding: 12px 14px; min-height: 90px;
}
.rf-kpi-ico {
    flex: 0 0 44px; height: 44px; border-radius: 50%; display: flex;
    align-items: center; justify-content: center;
    background: color-mix(in srgb, var(--tone) 14%, transparent);
    box-shadow: 0 0 18px color-mix(in srgb, var(--tone) 26%, transparent);
}
.rf-kpi-body { flex: 1 1 auto; min-width: 0; }
.rf-kpi-title {
    font-size: 13px; color: #CBD5E1; font-weight: 600; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
}
.rf-kpi-row {
    display: flex; flex-wrap: wrap; align-items: baseline;
    justify-content: space-between; column-gap: 8px;
}
.rf-kpi-val {
    font-size: 26px; font-weight: 700; color: #F8FAFC; line-height: 1.2;
    font-variant-numeric: tabular-nums;
}
.rf-kpi-pct { font-size: 13px; font-weight: 700; color: var(--tone); }
.rf-kpi-bar { height: 5px; border-radius: 3px; background: #16324F; margin-top: 6px; overflow: hidden; }
.rf-kpi-bar i { display: block; height: 100%; background: var(--tone); border-radius: 3px; }
.rf-kpi-note {
    font-size: 11.5px; color: #94A3B8; margin-top: 4px; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
}
@container (max-width: 200px) {
    .rf-kpi-ico { display: none; }
    .rf-kpi-val { font-size: 22px; }
}

/* ---- cards: legend, info panels -------------------------------------- */
.rf-card {
    background: #0B1F33; border: 1px solid #1E3A5F; border-radius: 12px;
    padding: 12px 14px;
}
.rf-card-t {
    display: flex; align-items: center; gap: 8px; font-weight: 700;
    font-size: 13.5px; color: #F1F5F9; margin-bottom: 10px;
}
.rf-card-t span { min-width: 0; overflow-wrap: anywhere; }
.rf-card-t small { font-weight: 500; color: #94A3B8; font-size: 11.5px; }
.rf-rows { display: flex; flex-direction: column; gap: 7px; }
.rf-row { display: flex; align-items: center; gap: 9px; font-size: 12.8px; color: #E2E8F0; min-height: 22px; }
.rf-row .rf-sw { flex: 0 0 24px; display: flex; align-items: center; justify-content: center; }
.rf-row .rf-lbl { flex: 1 1 auto; min-width: 3.5em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rf-row .rf-val {
    flex: 0 1 auto; min-width: 0; color: #94A3B8; font-size: 12px;
    font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis;
}
.rf-note { font-size: 11.5px; color: #94A3B8; margin-top: 9px; line-height: 1.45; }
[class*="st-key-rf_card"] { background: #0B1F33; border-radius: 12px; }

/* ---- swatches: the same symbols the map draws ------------------------ */
.rf-dot { width: 13px; height: 13px; border-radius: 50%; background: var(--c);
          box-shadow: 0 0 8px color-mix(in srgb, var(--c) 60%, transparent); }
.rf-sq { width: 14px; height: 14px; border-radius: 3px; background: var(--c);
         border: 1px solid rgba(255, 255, 255, .15); }
.rf-tower {
    width: 22px; height: 22px; border-radius: 50%; border: 2px solid var(--c);
    background: #071525; display: flex; align-items: center;
    justify-content: center;
    box-shadow: 0 0 9px color-mix(in srgb, var(--c) 55%, transparent);
}
.rf-line { width: 22px; height: 0; border-top: 3px solid var(--c); }
.rf-line.dash { border-top-style: dashed; }
.rf-glyph { display: flex; }

/* ---- upload status --------------------------------------------------- */
.rf-file {
    display: flex; gap: 10px; align-items: center; background: #071525;
    border: 1px solid #1E3A5F; border-radius: 10px; padding: 8px 10px;
}
.rf-file-b { min-width: 0; }
.rf-file-n { font-size: 12.5px; color: #E2E8F0; font-weight: 600; overflow: hidden;
             text-overflow: ellipsis; white-space: nowrap; }
.rf-file-m { font-size: 11px; color: #94A3B8; }
.rf-file.off img { opacity: .5; }
.rf-file.off .rf-file-n { color: #94A3B8; font-weight: 500; }

/* ---- sidebar --------------------------------------------------------- */
[data-testid="stSidebar"] [data-testid="stExpander"] details {
    border-color: #1E3A5F; border-radius: 10px; background: #0D2945;
}
[data-testid="stExpander"] details summary p { font-weight: 600; }
.rf-side-stat { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.rf-side-stat div {
    background: #071525; border: 1px solid #1E3A5F; border-radius: 10px; padding: 7px 10px;
}
.rf-side-stat span { display: block; font-size: 11px; color: #94A3B8; }
.rf-side-stat b { font-size: 17px; color: #F8FAFC; font-variant-numeric: tabular-nums; }
"""


def inject_css() -> None:
    """The shell's styles, once per page (a style-only st.html takes no space)."""
    st.html(f"<style>{_CSS}</style>")


def _esc(x) -> str:
    return html.escape(str(x))


def fmt_bytes(n: int | None) -> str:
    if not n:
        return ""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return ""


def header(title: str = "RF Optimization",
           subtitle: str = "Network Performance Dashboard", *,
           search_key: str | None = None,
           placeholder: str = "Search site, sector, or location…",
           project: str = "R5 · Asiacell") -> str | None:
    """The top bar. Returns the search text when the page has a search."""
    now = dt.datetime.now().strftime("%b %d, %Y  %H:%M")
    q = None
    with st.container(key="rf_header", horizontal=True, gap="medium",
                      vertical_alignment="center"):
        with st.container(key="rf_hdr_brand", width="content"):
            st.html(f'<div class="rf-brand"><span class="rf-brand-ico">'
                    f'{icon_img("tower", PALETTE["cyan"], 34)}</span><div>'
                    f'<div class="rf-brand-t">{_esc(title)}</div>'
                    f'<div class="rf-brand-s">{_esc(subtitle)}</div></div></div>')
        with st.container(key="rf_hdr_search", width="stretch"):
            if search_key:
                q = st.text_input("Search", key=search_key, placeholder=placeholder,
                                  label_visibility="collapsed")
            else:
                st.html("<div>&nbsp;</div>")
        with st.container(key="rf_hdr_chips", width="content"):
            st.html(f'<div class="rf-chips">'
                    f'<span class="rf-chip">{icon_img("calendar", PALETTE["cyan"], 15)}'
                    f'{_esc(now)}</span>'
                    f'<span class="rf-chip">{icon_img("pin", PALETTE["cyan"], 15)}'
                    f'Project: {_esc(project)}</span></div>')
    return q


def kpi_card(title: str, value, *, icon: str = "tower", tone: str = "blue",
             pct: float | None = None, note: str = "", data: str = "") -> None:
    """`data` tags the card (data-rf-card) so the map's time slider can keep
    its count on the hour shown."""
    colour = TONES.get(tone, tone)
    bar = (f'<div class="rf-kpi-bar"><i style="width:{max(0.0, min(100.0, pct)):.1f}%">'
           f'</i></div>') if pct is not None else ""
    pct_txt = f'<span class="rf-kpi-pct">{pct:.0f}%</span>' if pct is not None else ""
    tag = f' data-rf-card="{_esc(data)}"' if data else ""
    st.html(f'<div class="rf-kpi" style="--tone:{colour}"{tag}>'
            f'<div class="rf-kpi-ico">{icon_img(icon, colour, 23)}</div>'
            f'<div class="rf-kpi-body"><div class="rf-kpi-title" title="{_esc(title)}">'
            f'{_esc(title)}</div>'
            f'<div class="rf-kpi-row"><span class="rf-kpi-val">{_esc(value)}</span>'
            f'{pct_txt}</div>{bar}'
            f'<div class="rf-kpi-note" title="{_esc(note)}">{_esc(note)}</div></div></div>')


def kpi_cards(cards: list[dict]) -> None:
    for col, spec in zip(st.columns(len(cards), gap="small"), cards):
        with col:
            kpi_card(**spec)


def swatch(kind: str, colour: str) -> str:
    """The symbol a legend row shows — drawn the way the map draws it."""
    if kind == "tower":
        return (f'<span class="rf-tower" style="--c:{colour}">'
                f'{icon_img("tower", colour, 13)}</span>')
    if kind in ("line", "dash"):
        return f'<span class="rf-line{" dash" if kind == "dash" else ""}" style="--c:{colour}"></span>'
    if kind == "square":
        return f'<span class="rf-sq" style="--c:{colour}"></span>'
    if kind == "beam":
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="22" height="18" '
               f'viewBox="0 0 22 18"><path d="M2 16 20 3a13 13 0 0 1 0 13z" '
               f'fill="{colour}" fill-opacity=".55" stroke="{colour}" stroke-width="1.2"/></svg>')
        b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        return (f'<img class="rf-ic" src="data:image/svg+xml;base64,{b64}" '
                f'width="22" height="18" alt="">')
    if kind in ICONS:
        return f'<span class="rf-glyph">{icon_img(kind, colour, 20)}</span>'
    return f'<span class="rf-dot" style="--c:{colour}"></span>'


def title_html(title: str, icon: str = "layers", subtitle: str = "") -> str:
    sub = f" <small>{_esc(subtitle)}</small>" if subtitle else ""
    ico = icon_img(icon, PALETTE["cyan"], 18) if icon else ""
    return f'<div class="rf-card-t">{ico}<span>{_esc(title)}{sub}</span></div>'


def rows_html(rows: list[tuple]) -> str:
    """rows: (swatch_html, label, value) — value may be empty."""
    return '<div class="rf-rows">' + "".join(
        f'<div class="rf-row"><span class="rf-sw">{sw}</span>'
        f'<span class="rf-lbl" title="{_esc(lbl)}">{_esc(lbl)}</span>'
        f'<span class="rf-val" title="{_esc(val)}">{_esc(val)}</span></div>'
        for sw, lbl, val in rows) + "</div>"


def card(title: str, rows: list[tuple], *, icon: str = "layers",
         subtitle: str = "", note: str = "") -> None:
    """A whole information card — title, symbol rows, an optional note."""
    st.html(f'<div class="rf-card">{title_html(title, icon, subtitle)}{rows_html(rows)}'
            + (f'<div class="rf-note">{_esc(note)}</div>' if note else "")
            + "</div>")


def file_status(name: str | None, *, size: int | None = None,
                source: str = "", empty: str = "No file loaded") -> None:
    """What an upload field is actually using right now."""
    if not name:
        st.html(f'<div class="rf-file off">{icon_img("file", "#64748B", 18)}'
                f'<div class="rf-file-b"><div class="rf-file-n">{_esc(empty)}</div>'
                f'</div></div>')
        return
    meta = " · ".join(x for x in (fmt_bytes(size), source) if x)
    st.html(f'<div class="rf-file">{icon_img("file", PALETTE["cyan"], 18)}'
            f'<div class="rf-file-b">'
            f'<div class="rf-file-n" title="{_esc(name)}">{_esc(name)}</div>'
            f'<div class="rf-file-m">{_esc(meta)}</div></div></div>')


def side_stat(items: list[tuple[str, str]]) -> None:
    """Small counters at the foot of the sidebar."""
    st.html('<div class="rf-side-stat">' + "".join(
        f"<div><span>{_esc(k)}</span><b>{_esc(v)}</b></div>" for k, v in items)
        + "</div>")
