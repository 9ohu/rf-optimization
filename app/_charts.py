"""The KPI chart, in the shape the R5 report deck uses.

Kept out of the page so it can be built and looked at without running
Streamlit — a chart is the one thing a smoke test cannot tell you is right.
Copy Chart lives here too: Draw Data and Bulk Draw put a chart on the
clipboard with the very same script.
"""

from __future__ import annotations

import math

# one colour per compared cell, in the order the deck uses them
LINE_COLOURS = ["#20BFFF", "#A78BFA", "#FB923C", "#F472B6", "#4ADE80",
                "#FACC15", "#2DD4BF", "#E879F9", "#CBD5E1", "#F87171"]
LEGEND_PER_ROW = 3          # cell names under a half-width chart
LEGEND_ROW_PX = 20

# Copy Chart, in the page: the chart's own SVG layers drawn on a canvas under
# its title, on the card's navy so pasted text stays readable
COPY_JS = """
<script>
(function () {
  var W = window, D = document;
  if (W.rfChartPng) return;
  function load(src) {
    return new Promise(function (ok, bad) {
      var im = new Image();
      im.onload = function () { ok(im); };
      im.onerror = bad;
      im.src = src;
    });
  }
  W.rfChartPng = async function (cardKey) {
    var card = D.querySelector('.st-key-' + cardKey);
    var gd = card && card.querySelector('.js-plotly-plot');
    if (!gd) throw new Error('chart not found');
    var head = card.querySelector('.sm-chart-head');
    var title = head ? (head.querySelector('.t') || head).textContent.trim() : '';
    var verdict = head && head.querySelector('.sm-verdict')
      ? head.querySelector('.sm-verdict').textContent.trim() : '';
    var box = gd.getBoundingClientRect(), S = 2, H = 34;
    var cv = D.createElement('canvas');
    cv.width = Math.round(box.width * S);
    cv.height = Math.round((box.height + H) * S);
    var g = cv.getContext('2d');
    g.scale(S, S);
    g.fillStyle = '#0B1F33';
    g.fillRect(0, 0, box.width, box.height + H);
    g.textBaseline = 'middle';
    g.font = "700 13px 'Segoe UI', system-ui, sans-serif";
    g.fillStyle = '#F1F5F9';
    g.fillText(title, 10, H / 2, Math.max(60, box.width - 150));
    if (verdict) {
      g.textAlign = 'right';
      g.fillStyle = '#20BFFF';
      g.fillText(verdict, box.width - 10, H / 2);
      g.textAlign = 'left';
    }
    var svgs = gd.querySelectorAll('svg.main-svg');
    for (var k = 0; k < svgs.length; k++) {
      var s = svgs[k], r = s.getBoundingClientRect();
      var c = s.cloneNode(true);
      c.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
      c.setAttribute('width', r.width);
      c.setAttribute('height', r.height);
      c.querySelectorAll('.hoverlayer > *').forEach(function (n) { n.remove(); });
      var im = await load('data:image/svg+xml;charset=utf-8,'
                          + encodeURIComponent(new XMLSerializer().serializeToString(c)));
      g.drawImage(im, r.left - box.left, r.top - box.top + H, r.width, r.height);
    }
    return await new Promise(function (ok) { cv.toBlob(ok, 'image/png'); });
  };
  D.addEventListener('click', function (ev) {
    var b = ev.target && ev.target.closest && ev.target.closest('button.rf-copy');
    if (!b) return;
    ev.preventDefault();
    var label = b.querySelector('span'), idle = label.textContent;
    var png = W.rfChartPng(b.dataset.card);
    function show(msg) {
      label.textContent = msg;
      b.dataset.state = msg;
      setTimeout(function () { label.textContent = idle; delete b.dataset.state; }, 2200);
    }
    function save() {
      return png.then(function (blob) {
        var a = D.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = (b.dataset.file || 'chart') + '.png';
        D.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
        show('Saved PNG');
      }, function () { show('Not copied'); });
    }
    // the clipboard write starts inside the click, with the picture still being
    // drawn: browsers only accept an image write from a user gesture
    if (W.isSecureContext && W.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
      navigator.clipboard.write([new ClipboardItem({'image/png': png})])
        .then(function () { show('Copied'); }, save);
    } else {
      save();
    }
  }, true);
})();
</script>
"""


def rgba(hex_colour: str, alpha: float) -> str:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def legend_rows(panel) -> int:
    return math.ceil(len(panel.lines) / LEGEND_PER_ROW) if panel.compared else 0


def kpi_figure(panel, *, height: int = 300, legend_title: str = "Cell Name"):
    """One KPI, one line per compared cell, filled under the curve.

    Hovering reads the time once and, per line, its colour, its complete name
    and its value: plotly cuts trace names at 15 characters unless
    `hoverlabel.namelength` says otherwise, which turned "L_Tannoma8_BAS0001-1"
    into a name nobody could match to a cell. The legend sits under the plot,
    where a long cell name has the whole width instead of a narrow margin."""
    import plotly.graph_objects as go

    fig = go.Figure()
    # filling to zero only reads as "area" when zero is the floor; on a dBm
    # axis it would paint 115 dB of nothing
    fill = "tozeroy" if panel.all_positive else None
    for i, (name, s) in enumerate(panel.lines.items()):
        colour = LINE_COLOURS[i % len(LINE_COLOURS)]
        fig.add_scatter(x=s.index, y=s.values, mode="lines", name=str(name),
                        line=dict(width=1.6, color=colour), fill=fill,
                        fillcolor=rgba(colour, 0.18 if panel.compared else 0.28),
                        hovertemplate="<b>%{fullData.name}</b>: %{y:,.2f}<extra></extra>")
    if not panel.compared and panel.overall is not None:
        fig.add_scatter(x=panel.overall.fit.index, y=panel.overall.fit.values,
                        mode="lines", name="trend", showlegend=False, hoverinfo="skip",
                        line=dict(width=1.4, color="#e5484d", dash="dash"))
    rows = legend_rows(panel)
    fig.update_layout(
        height=height + rows * LEGEND_ROW_PX,
        margin=dict(l=6, r=6, t=8, b=6),
        font=dict(size=10, color="#CBD5E1"), hovermode="x unified",
        hoverlabel=dict(namelength=-1, align="left", bgcolor="#0B1F33",
                        bordercolor="#1E3A5F", font=dict(size=11, color="#E2E8F0")),
        showlegend=panel.compared, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(title=dict(text=f"{legend_title}:", font=dict(size=10)),
                    font=dict(size=10), orientation="h", x=0, xanchor="left",
                    yref="container", y=0, yanchor="bottom", bgcolor="rgba(0,0,0,0)"))
    fig.update_xaxes(showgrid=False, linecolor="#2A4A6F", tickangle=0,
                     automargin=True, hoverformat="%d %b %Y %H:%M", **_time_ticks(panel),
                     title=dict(text="Time", font=dict(size=9)))
    # automargin, or a rotated axis title gets clipped at half width
    fig.update_yaxes(gridcolor="#16324F", linecolor="#2A4A6F", automargin=True,
                     title=dict(text=f"Average of {panel.kpi}"[:34],
                                font=dict(size=9)))
    return fig


def _time_ticks(panel) -> dict:
    """Readable date ticks: plotly's own spacing crams a day's hours into a
    half-width chart, so the step is chosen from the window instead."""
    spans = [s.index for s in panel.lines.values() if len(s)]
    if not spans:
        return {}
    hours = (max(ix.max() for ix in spans)
             - min(ix.min() for ix in spans)).total_seconds() / 3600
    hour_ms = 3_600_000
    if hours <= 50:
        return {"dtick": 6 * hour_ms, "tickformat": "%b %d<br>%H:%M"}
    if hours <= 24 * 9:
        return {"dtick": 24 * hour_ms, "tickformat": "%b %d"}
    return {"tickformat": "%b %d"}
