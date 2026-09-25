"""RF Optimizer - app entry point.

Dashboard (the executive overview), Daily Worklist (the daily complaint list),
Sites (the map), KPI Analysis — its
Overview of the 4G + 3G hourly exports and Draw Data for their charts —,
Complaints (Delay Tickets Analysis; History of Tickets, with Tickets Details as its
second tab), and Data Resources, where every dataset they read is uploaded,
reviewed, applied and kept on disk. The older KPI-engine pages are kept in ``app/_advanced/``
and are not registered here; re-add them to the nav list below if you want them
back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_APP = Path(__file__).resolve().parent
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

st.set_page_config(page_title="RF Optimization",
                   page_icon=":material/cell_tower:",
                   layout="wide", initial_sidebar_state="expanded")

from _ui import LOGO_SVG, inject_css  # noqa: E402  (needs the path above)
import _resources  # noqa: E402

st.logo(LOGO_SVG, size="large")
inject_css()
# the Data Resources store, read on every start; on the very first start it
# takes over the files the pages used to keep or find themselves
_resources.ready()

# only pages that really exist; the map keeps its `site_map` URL and the KPI
# Overview its `kpi_analysis` one. Streamlit lists the unlabelled pages first,
# so Complaint Analysis gets its own group to stay below KPI Analysis.
pages = {
    "": [
        st.Page("views/overview.py", title="Dashboard", icon=":material/dashboard:",
                url_path="home", default=True),
        st.Page("views/dashboard.py", title="Daily Worklist",
                icon=":material/fact_check:"),
        st.Page("views/site_map.py", title="Sites", icon=":material/cell_tower:"),
    ],
    "KPI Analysis": [
        st.Page("views/kpi_analysis.py", title="Overview",
                icon=":material/space_dashboard:"),
        st.Page("views/kpi_draw.py", title="Draw Data", icon=":material/insights:"),
    ],
    # Complaints: the Daily Target tickets, each one opened in place, and the
    # history of every R5 ticket (the History ticket resource) - its Tickets
    # Details are the page's second tab, not a page of their own
    "Complaints": [
        st.Page("views/complaint_analysis.py", title="Delay Tickets Analysis",
                icon=":material/report_problem:"),
        st.Page("views/ticket_history.py", title="History of Tickets",
                icon=":material/history:"),
        st.Page("views/sleep_analysis.py", title="Sleep Analysis",
                icon=":material/bedtime:"),
    ],
    # every dataset of the app: uploaded, reviewed and applied here, read by all
    "Data": [
        st.Page("views/data_resources.py", title="Data Resources", icon=":material/database:"),
    ],
}

# uncomment to expose the KPI-engine pages again:
# pages += [
#     st.Page("_advanced/kpi_analysis.py", title="KPI Analysis"),
#     st.Page("_advanced/diagnosis_and_actions.py", title="Diagnosis"),
#     st.Page("_advanced/cell_deep_dive.py", title="Cell Deep Dive"),
#     st.Page("_advanced/reports.py", title="Reports"),
#     st.Page("_advanced/complaint_analysis.py", title="Complaint Analysis"),
# ]

st.navigation(pages).run()
