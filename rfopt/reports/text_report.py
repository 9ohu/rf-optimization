"""Markdown / plain-text optimisation report (engineer-readable)."""

from __future__ import annotations

from pathlib import Path


def build_markdown_report(result, narratives: list | None = None,
                          title: str = "RF Optimisation Report") -> str:
    s = result.summary()
    L = [f"# {title}", ""]
    L.append(f"*Generated from {result.load.n_rows:,} rows | "
             f"{s['sites']} sites, {s['cells']} cells | "
             f"{result.technology} | {result.load.granularity} granularity*")
    if result.load.date_range:
        L.append(f"*Period: {result.load.date_range[0]:%Y-%m-%d %H:%M} "
                 f"to {result.load.date_range[1]:%Y-%m-%d %H:%M}*")
    L += ["", "## At a glance", ""]
    L += [f"- **{s['diagnoses']}** diagnosed problems "
          f"({s['critical_diagnoses']} critical)",
          f"- **{s['recommendations']}** recommended actions",
          f"- **{s['kpi_breaches']}** KPI threshold breaches",
          f"- **{s['degrading_trends']}** degrading KPI trends"]
    if result.warnings:
        L += ["", "### Data notes"] + [f"- {w}" for w in result.warnings]

    # priority action plan
    L += ["", "## Priority action plan", ""]
    if not result.recommendations:
        L.append("_No actions generated._")
    for i, r in enumerate(result.recommendations, 1):
        L += [f"### {i}. [{r.priority}] {r.cell_id} - {r.problem}",
              "", "```", r.as_text(), "```", ""]

    # narratives
    if narratives:
        L += ["", "## Engineer narratives", ""]
        for n in narratives:
            L += [n.as_markdown(), "", "---", ""]

    # trend appendix
    degr = [t for t in result.trends if t.verdict in ("degrading", "step-down")]
    if degr:
        L += ["", "## Degrading trends (appendix)", "",
              "| Cell | KPI | Verdict | Change | First -> Last |",
              "|---|---|---|---|---|"]
        for t in degr[:40]:
            L.append(f"| {t.entity_id} | {t.label} | {t.verdict} | "
                     f"{t.pct_change:+.0f}% | {t.first:.2f} -> {t.last:.2f} |")
    return "\n".join(L)


def write_markdown_report(result, out_path: str | Path,
                          narratives: list | None = None) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_markdown_report(result, narratives),
                        encoding="utf-8")
    return out_path
