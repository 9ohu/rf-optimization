"""KPI Data holds as many files as the user uploads — and every page reads a
technology's files as one export.

Three KPI files, or ten: 4G exports with other KPI sets, other areas or other
periods are added beside the active ones; only a newer copy of an active
export (the same KPIs and cells, overlapping hours) replaces it, and the user
can keep any file or take a new one back out before Apply. The pages combine
the files of one technology into one row per object and hour, so an hour two
files share is judged, summed and drawn once. Every test runs on its own empty
store (conftest), never the user's.
"""

import io
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from rfopt.ingest.hourly_kpi import merge_hourly  # noqa: E402
from rfopt.resources import store as S  # noqa: E402

PRB, INTER, AVA = "DL PRB Utilization(%)", "L.UL.Interference.Avg(dBm)", "LTE_Availability(%)@AB"
THR, RRC, DROP = "DL User Throughput(Mbps)", "RRC Setup Success Rate(%)", "E-RAB Drop Rate(%)"
FLOW = "VS.RscGroup.FlowCtrol.DL.DropNum"


def _4g(kpis: dict, *, day: str = "2026-09-13", hours=range(6),
        cells=("Alpha_BAS0001-1",)) -> bytes:
    """A raw hourly 4G export: one row per cell and hour, each KPI at its value."""
    head = ("Time,eNodeB Name,Cell FDD TDD Indication,Cell Name,LocalCell Id,"
            + ",".join(kpis) + "\n")
    rows = [f"{day} {h:02d}:00,{c.rsplit('-', 1)[0]},CELL_FDD,L_{c},{c[-1]},"
            + ",".join(str(v) for v in kpis.values()) + "\n"
            for c in cells for h in hours]
    return (head + "".join(rows)).encode()


def _3g(hours=range(6), drops: int = 5, day: str = "2026-09-08") -> bytes:
    """A raw hourly 3G export (a zip, as NPM gives it): one NodeB, `drops` per hour."""
    csv = ("\n\n\nSHAMS-3G\nSave Time :2026-09-11 10:51:53\n\n"
           f"Time,RNC,NODEBNAME,NodeB ID,Integrity,{FLOW},VS.IPPM.Rtt.Means(ms),"
           "3G_Availability@AB\n"
           + "".join(f"{day} {h:02d}:00,RBASH01,Tannumah6_BAS0038,38,100%,{drops},3,100\n"
                     for h in hours))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("export(Subreport 1).csv", csv)
    return buf.getvalue()


A = {PRB: 30, INTER: -118, AVA: 100}          # the "update" query: PRB, interference
B = {THR: 12.5, RRC: 99.1}                    # another 4G query: throughput, RRC
C = {DROP: 0.4}                               # a third one: drops


def _stage(named):
    import _resources as R
    return R.stage_files("kpi", named)


def _names() -> list:
    return sorted(f.name for f in S.active_files("kpi"))


def _sha(name: str) -> str:
    res = S.resource("kpi")
    return next(f.sha1 for f in list(res.files) + list(res.pending) if f.name == name)


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #
def test_kpi_data_takes_many_files_of_one_technology():
    """The user's case: a third KPI file used to push one of the first two out."""
    res, bad = _stage([("4G update.csv", _4g(A)), ("SHAMS-3G.zip", _3g())])
    assert not bad
    S.apply("kpi")
    res, bad = _stage([("4G throughput.csv", _4g(B)), ("4G drops.csv", _4g(C))])
    assert not bad and not res.replaced()                    # nothing pushed out
    assert len(res.after_apply()) == 4
    S.apply("kpi")
    # another area (other cells) and another period of the same query are added too
    res, _ = _stage([("4G update NAS.csv", _4g(A, cells=("Beta_NAS0100-1", "Beta_NAS0100-2"))),
                     ("4G update last week.csv", _4g(A, day="2026-09-06"))])
    assert not res.replaced()
    S.apply("kpi")
    assert _names() == ["4G drops.csv", "4G throughput.csv", "4G update NAS.csv",
                        "4G update last week.csv", "4G update.csv", "SHAMS-3G.zip"]
    assert {f.role for f in S.active_files("kpi")} == {"4G KPI", "3G KPI"}


def test_a_newer_copy_of_an_export_replaces_it_unless_the_user_keeps_it():
    _stage([("4G update 13-Sep.csv", _4g(A)), ("4G throughput.csv", _4g(B))])
    S.apply("kpi")
    # the same query exported again: same KPIs and cell, hours overlapping and later
    res, _ = _stage([("4G update 14-Sep.csv", _4g(A, hours=range(3, 10)))])
    assert [f.name for f in res.replaced()] == ["4G update 13-Sep.csv"]
    assert [f.name for f in res.kept()] == ["4G throughput.csv"]

    old, thr = _sha("4G update 13-Sep.csv"), _sha("4G throughput.csv")
    res = S.set_drop("kpi", old, False)                      # Keep ticked
    assert not res.replaced()
    res = S.set_drop("kpi", thr, True)                       # Keep unticked
    assert [f.name for f in res.replaced()] == ["4G throughput.csv"]
    S.apply("kpi")
    assert _names() == ["4G update 13-Sep.csv", "4G update 14-Sep.csv"]
    blobs = {p.name for d in (S.root() / "files").iterdir() for p in d.iterdir()}
    assert "4G throughput.csv" not in blobs                  # deleted on Apply
    assert not S.resource("kpi").drop and not S.resource("kpi").pending


def test_an_older_export_does_not_replace_a_newer_one():
    _stage([("4G update 14-Sep.csv", _4g(A, hours=range(3, 10)))])
    S.apply("kpi")
    res, _ = _stage([("4G update 13-Sep.csv", _4g(A))])      # ends before the active one
    assert not res.replaced() and len(res.after_apply()) == 2


def test_a_new_file_can_be_taken_back_out_before_apply():
    _stage([("4G update.csv", _4g(A))])
    S.apply("kpi")
    res, _ = _stage([("4G update newer.csv", _4g(A, hours=range(2, 8))),
                     ("4G throughput.csv", _4g(B))])
    assert [f.name for f in res.replaced()] == ["4G update.csv"]
    res = S.unstage("kpi", _sha("4G update newer.csv"))
    assert [f.name for f in res.pending] == ["4G throughput.csv"]
    assert not res.replaced()                                # its copy stays after all
    res = S.unstage("kpi", _sha("4G throughput.csv"))
    assert not res.pending and not res.drop and res.pending_at is None
    with pytest.raises(S.ResourceError, match="no upload"):
        S.apply("kpi")
    assert _names() == ["4G update.csv"]


def test_the_same_file_twice_is_not_added_again():
    _stage([("4G update.csv", _4g(A))])
    S.apply("kpi")
    res, bad = _stage([("4G update (1).csv", _4g(A))])
    assert res is None and "already active" in bad[0][1]
    assert _names() == ["4G update.csv"]


def test_no_file_is_removed_on_a_guess():
    """A file whose summary does not list its KPIs and cells is never taken for
    an older copy — and the upload fills that summary in first, so the files
    stored before this release are judged on what they really hold."""
    old = S.put_file("4G old.csv", _4g(A))
    old.role, old.summary = "4G KPI", {}
    new = S.put_file("4G new.csv", _4g(A, hours=range(2, 9)))
    new.role, new.summary = "4G KPI", {"start": "2026-09-13 02:00", "end": "2026-09-13 08:00"}
    assert not S.same_export(old, new)

    S.stage("kpi", [old])
    S.apply("kpi")
    assert S.resource("kpi").files[0].summary == {}
    res, _ = _stage([("4G new.csv", _4g(A, hours=range(2, 9)))])
    assert S.resource("kpi").files[0].summary["kpi_set"] == [PRB, INTER, AVA]
    assert [f.name for f in res.replaced()] == ["4G old.csv"]


def _summary(kpis, cells, start="2026-09-13 00:00", end="2026-09-13 23:00") -> dict:
    return {"kpi_set": list(kpis), "object_sketch": S.object_sketch(cells),
            "objects": len(cells), "start": start, "end": end}


def _file(summary: dict) -> S.StoredFile:
    return S.StoredFile("f.csv", "0" * 40, 1, "files/x/f.csv", "4G KPI", summary)


def test_a_newer_copy_loses_nothing_the_active_file_held():
    """Only a file with every KPI, nearly every cell and most hours of the
    active one replaces it — whatever would lose data is added beside."""
    cells = [f"L_Alpha_BAS{i:04d}-{s}" for i in range(300) for s in (1, 2, 3)]
    old = _file(_summary(A, cells))
    same = _summary(A, cells, "2026-09-13 06:00", "2026-09-14 06:00")
    assert S.same_export(old, _file(same))
    assert S.same_export(old, _file({**same, "kpi_set": [*A, THR]}))       # more KPIs
    assert S.same_export(old, _file(_summary(A, cells[:-9], same["start"], same["end"])))
    # would lose a KPI, the cells of another area, or most of the hours: not a copy
    assert not S.same_export(old, _file({**same, "kpi_set": [PRB, INTER]}))
    assert not S.same_export(old, _file(_summary(A, cells[:450], same["start"], same["end"])))
    assert not S.same_export(old, _file(_summary(A, cells, "2026-09-13 20:00",
                                                 "2026-09-14 20:00")))
    assert not S.same_export(old, _file(_summary(A, cells, "2026-09-12 00:00",
                                                 "2026-09-13 20:00")))   # stops earlier
    assert not S.same_export(old, S.StoredFile("f.csv", "1" * 40, 1, "files/y/f.csv",
                                               "3G KPI", same))           # other technology
    assert 0.45 < S.cell_cover(_summary(A, cells), _summary(A, cells[:450])) < 0.56


def test_the_object_sketch_tells_areas_apart():
    cells = [f"L_Alpha_BAS{i:04d}-{s}" for i in range(400) for s in (1, 2, 3)]
    other = [f"L_Beta_NAS{i:04d}-{s}" for i in range(400) for s in (1, 2, 3)]
    a, b = S.object_sketch(cells), S.object_sketch(other)
    assert len(a) == S.SKETCH
    assert S.sketch_overlap(a, S.object_sketch(cells[:-12])) > 0.9   # a few cells fewer
    assert S.sketch_overlap(a, b) == 0.0
    half = S.sketch_overlap(a, S.object_sketch(cells[:600] + other[:600]))
    assert 0.15 < half < 0.55                                         # about a third


# --------------------------------------------------------------------------- #
# one export per technology
# --------------------------------------------------------------------------- #
def test_merge_hourly_counts_a_shared_hour_once():
    t = pd.Timestamp("2026-09-13")
    old = pd.DataFrame({"datetime": [t, t + pd.Timedelta(hours=1)], "object": ["c1", "c1"],
                        "site_id": ["BAS0001"] * 2, PRB: [30.0, 31.0]})
    new = pd.DataFrame({"datetime": [t + pd.Timedelta(hours=1), t + pd.Timedelta(hours=2)],
                        "object": ["c1", "c1"], "site_id": ["BAS0001"] * 2,
                        PRB: [41.0, 42.0], THR: [9.0, 8.0]})
    m = merge_hourly([old, new])
    assert list(m["datetime"]) == [t, t + pd.Timedelta(hours=1), t + pd.Timedelta(hours=2)]
    assert list(m[PRB]) == [30.0, 41.0, 42.0]                # the later file's value
    assert m[THR].isna().tolist() == [True, False, False]    # a KPI only one file has
    assert merge_hourly([old]) is old
    assert merge_hourly([]).empty


def _ws(tech: str):
    import _kpi_workspace as W
    import _resources as R
    files = W.combine(R.kpi_groups())
    usable = [(b, i) for b, i in files if i.kind == tech]
    index = pd.concat([W.index_of(b).assign(tech=i.kind) for b, i in usable],
                      ignore_index=True)
    return W, W.Workspace(files, usable, tech, "Cell", index, "Network", None, [], set(), [],
                          None, None)


def test_the_kpi_pages_judge_an_hour_two_files_share_once(put_resource):
    # two 3G exports over overlapping hours (0-5 and 3-8) and a 4G query of its own
    put_resource("kpi", "SHAMS-3G day 1.zip", _3g(range(0, 6)), "3G KPI")
    put_resource("kpi", "SHAMS-3G day 2.zip", _3g(range(3, 9)), "3G KPI")
    put_resource("kpi", "4G update.csv", _4g(A), "4G KPI")
    put_resource("kpi", "4G throughput.csv", _4g(B, cells=("Alpha_BAS0001-1",
                                                           "Alpha_BAS0001-2")), "4G KPI")
    W, ws = _ws("3G")
    assert len(ws.usable) == 1 and isinstance(ws.usable[0][0], W.Combined)
    assert ws.index.duplicated(["datetime", "object"]).sum() == 0
    _, _, health = W.site_health(ws)
    flow = health.objects[health.objects["column"] == FLOW]
    assert len(flow) == 1                                     # one NodeB, once
    assert flow["value"].iloc[0] == 9 * 5                     # 9 distinct hours × 5 drops

    W, ws = _ws("4G")
    src, info = ws.usable[0]
    assert info.all_kpis == [PRB, INTER, AVA, THR, RRC]       # both queries' KPIs
    both = W.raw(src, (PRB, THR))
    assert both.duplicated(["datetime", "object"]).sum() == 0
    assert len(both) == 12                                    # 2 cells × 6 hours
    _, _, health = W.site_health(ws)
    assert not health.objects.duplicated(["object_id", "column"]).any()

    # Report Export reads the same combined frame
    import _kpi_report as X
    _, ws3 = _ws("3G")
    ch = next(c for c in X.kpi_choices(ws3) if c.kind == "3G")
    frame = X._frame(ws3, ch, None)
    assert len(frame) and not frame.duplicated(["datetime", "object"]).any()


def test_the_complaint_evidence_reads_a_technology_once(put_resource):
    put_resource("kpi", "SHAMS-3G day 1.zip", _3g(range(0, 6)), "3G KPI")
    put_resource("kpi", "SHAMS-3G day 2.zip", _3g(range(3, 9)), "3G KPI")
    import _complaints as CA
    import _resources as R
    sources = {f.sha1: (f.name, path) for _, g in R.kpi_groups() for path, _, f in g}
    tracks, _ = CA._load_tracks(tuple(sorted(sources)), sources)
    cols = [t.column for t in tracks]
    assert len(cols) == len(set(cols))                        # one track per KPI
    for t in tracks:
        for times, _, _ in t.by_site.values():
            assert len(times) == len(set(times)) == 9


def test_the_dashboard_reads_every_4g_file(put_resource):
    import _resources as R
    from _shared import load_kpi_files
    put_resource("kpi", "4G update.csv", _4g(A), "4G KPI")
    put_resource("kpi", "4G update later.csv", _4g(A, hours=range(4, 10)), "4G KPI")
    # a query with none of the worklist's KPIs, on another day: it adds no empty hours
    put_resource("kpi", "4G custom.csv", _4g({"Custom Counter X": 5}, day="2026-09-20"),
                 "4G KPI")
    paths = next(tuple(p for p, _, _ in g) for kind, g in R.kpi_groups() if kind == "4G")
    kl = load_kpi_files(paths)
    assert len(paths) == 3 and kl.df["datetime"].nunique() == 10
    assert kl.df["datetime"].max() < pd.Timestamp("2026-09-14")
    assert not kl.df.duplicated(["datetime", "cell_id"]).any()
    assert any("4G custom.csv" in n for n in kl.notes)


# --------------------------------------------------------------------------- #
# the pages
# --------------------------------------------------------------------------- #
def _html(at) -> str:
    return " ".join(e.proto.body for e in at.get("html"))


def test_the_data_resources_page_adds_keeps_and_removes_kpi_files():
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    _stage([("4G update.csv", _4g(A)), ("SHAMS-3G.zip", _3g())])
    S.apply("kpi")
    at = AppTest.from_file(str(APP / "views/data_resources.py"), default_timeout=120)
    at.run()
    assert at.button(key="dr_rep_kpi").label == "Add / Replace"
    at.button(key="dr_rep_kpi").click().run()
    assert "Add or replace KPI Data" in _html(at)

    # two more KPI files, as the upload field hands them over: added, nothing replaced
    _stage([("4G throughput.csv", _4g(B)), ("4G drops.csv", _4g(C))])
    at.run()
    assert not at.exception, at.exception
    text = _html(at)
    assert "New files — validated (2)" in text and "Stays active" in text
    assert "Replaced — deleted on Apply" not in text
    assert "After Apply: <b>4</b> KPI Data files active — 2 new, 2 kept" in text
    g3 = _sha("SHAMS-3G.zip")[:12]
    assert at.checkbox(key=f"dr_keep_{g3}").value is True

    # untick Keep: Apply would delete it; tick it again: it stays
    at.checkbox(key=f"dr_keep_{g3}").uncheck().run()
    assert [f.name for f in S.resource("kpi").replaced()] == ["SHAMS-3G.zip"]
    assert "Replaced — deleted on Apply" in _html(at)
    at.checkbox(key=f"dr_keep_{g3}").check().run()
    assert not S.resource("kpi").replaced()

    # take one new file back out, then Apply: three KPI files active
    at.button(key=f"dr_unstage_{_sha('4G drops.csv')[:12]}").click().run()
    assert [f.name for f in S.resource("kpi").pending] == ["4G throughput.csv"]
    at.button(key="dr_apply_kpi").click().run()
    assert not at.exception, at.exception
    assert _names() == ["4G throughput.csv", "4G update.csv", "SHAMS-3G.zip"]

    # a newer copy of the 4G query comes in ticked for deletion of the old one
    at.button(key="dr_rep_kpi").click().run()
    _stage([("4G update newer.csv", _4g(A, hours=range(3, 9)))])
    at.run()
    old = _sha("4G update.csv")[:12]
    assert at.checkbox(key=f"dr_keep_{old}").value is False
    assert "Replaced — deleted on Apply" in _html(at)
    at.button(key="dr_apply_kpi").click().run()
    assert _names() == ["4G throughput.csv", "4G update newer.csv", "SHAMS-3G.zip"]
    text = _html(at)
    assert "4G update.csv" not in text                        # gone from Active Data

    # the same file again is said so, and not added
    at.button(key="dr_rep_kpi").click().run()
    import _resources as R
    _, bad = R.stage_files("kpi", [("4G throughput copy.csv", _4g(B))])
    at.session_state["dr_rejected"] = {"kpi": bad}
    at.run()
    assert any("was not added again" in i.value for i in at.info)


def test_the_map_offers_a_kpi_split_over_two_files_once(put_resource):
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    one = put_resource("kpi", "4G update BAS.csv", _4g(A), "4G KPI")
    two = put_resource("kpi", "4G update NAS.csv", _4g(A, cells=("Beta_NAS0100-1",)),
                       "4G KPI")
    import hashlib
    gid = hashlib.sha1("".join(sorted([one.sha1, two.sha1])).encode()).hexdigest()[:12]
    at = AppTest.from_file(str(APP / "views/site_map.py"), default_timeout=240)
    at.session_state["sm_kpi_pick_keep"] = f"{gid}::{PRB}"
    at.run()
    assert not at.exception, at.exception
    pick = at.radio(key="sm_kpi_pick")
    assert pick.value == f"{gid}::{PRB}"
    assert [o for o in pick.options if PRB in o] == [PRB]     # once, not once per file
    text = _html(at)
    assert "4G update BAS.csv" not in text and "4G update NAS.csv" not in text


def test_the_kpi_analysis_page_offers_every_kpi_of_every_file(put_resource):
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    put_resource("kpi", "4G update.csv", _4g(A), "4G KPI")
    put_resource("kpi", "4G throughput.csv", _4g(B), "4G KPI")
    put_resource("kpi", "4G drops.csv", _4g(C), "4G KPI")
    at = AppTest.from_file(str(APP / "views/kpi_analysis.py"), default_timeout=240)
    at.run()
    assert not at.exception, at.exception
    text = " ".join(e.proto.body for e in at.sidebar.get("html"))
    for name in ("4G update.csv", "4G throughput.csv", "4G drops.csv"):
        assert name not in text, name             # the files are read, not listed
    boxes = {c.label for c in at.sidebar.checkbox}
    assert {PRB, INTER, AVA, THR, RRC, DROP} <= boxes
