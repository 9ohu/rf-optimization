"""Data Resources: one active dataset per resource, and the page over it.

Upload → validate → preview → Apply (or Cancel); the active data untouched
until Apply; the replaced files gone after it; no versions, no history; the
store read again after a restart; the "Auto-Saved" status only when the data
really is on disk; the earlier versioned registry converted; nothing ever
picked up from the computer's folders; pages that upload nothing themselves.
Every test runs on its own empty store (conftest), never the user's.
"""

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from rfopt.resources import store as S  # noqa: E402

_4G_HEAD = ("Time,eNodeB Name,Cell FDD TDD Indication,Cell Name,LocalCell Id,"
            "DL PRB Utilization(%),L.UL.Interference.Avg(dBm),LTE_Availability(%)@AB\n")
WORDS_GONE = ("Version", "Previous", "Archived", "Data History", "Restore", "v1", "v2")


def _4g(prb: int = 30, day: str = "2026-09-13") -> bytes:
    rows = [_4G_HEAD] + [f"{day} {h:02d}:00,Alpha_BAS0001,CELL_FDD,L_Alpha_BAS0001-1,1,"
                         f"{prb},-118,100\n" for h in range(6)]
    return "".join(rows).encode()


def _3g() -> bytes:
    csv = ("\n\n\nSHAMS-3G\nSave Time :2026-09-11 10:51:53\n\n"
           "Time,RNC,NODEBNAME,NodeB ID,Integrity,"
           "VS.RscGroup.FlowCtrol.DL.DropNum,VS.IPPM.Rtt.Means(ms),3G_Availability@AB\n"
           "2026-09-08 00:00,RBASH01,Tannumah6_BAS0038,38,100%,0,0,100\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("export(Subreport 1).csv", csv)
    return buf.getvalue()


def _grid_csv(n: int = 40) -> bytes:
    lines = ["Latitude,Longitude,RSRP(All MRs) (dBm),MR Count"] + [
        f"{30.5 + i * 0.0005:.6f},{47.8 + i * 0.0005:.6f},{-120 + i},5" for i in range(n)]
    return ("\n".join(lines) + "\n").encode()


def _target() -> bytes:
    from test_complaint_analysis import _target_bytes
    return _target_bytes()


def _stage(kind, named):
    import _resources as R
    return R.stage_files(kind, named)


def _blobs() -> set:
    folder = S.root() / "files"
    return {p.name for d in folder.iterdir() for p in d.iterdir()} if folder.is_dir() else set()


def _names(kind) -> list:
    return sorted(f.name for f in S.active_files(kind))


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #
def test_an_upload_waits_for_apply_and_apply_replaces_the_old_file():
    res, bad = _stage("kpi", [("old 4G.csv", _4g(30))])
    assert not bad and [f.name for f in res.pending] == ["old 4G.csv"]
    assert _names("kpi") == []                               # nothing active before Apply
    S.apply("kpi")
    assert _names("kpi") == ["old 4G.csv"]

    res, _ = _stage("kpi", [("new 4G.csv", _4g(95))])
    assert _names("kpi") == ["old 4G.csv"]                   # uploaded only: still the old one
    assert [f.name for f in res.replaced()] == ["old 4G.csv"]
    S.apply("kpi")
    assert _names("kpi") == ["new 4G.csv"]
    assert _blobs() == {"new 4G.csv"}                        # the old file is gone from disk
    reg = json.loads((S.root() / "registry.json").read_text(encoding="utf-8"))
    text = json.dumps(reg)
    assert reg["schema"] == 2 and "versions" not in text and '"number"' not in text
    assert S.resource("kpi").files[0].role == "4G KPI"
    assert S.resource("kpi").files[0].summary["objects"] == 1


def test_a_new_export_replaces_its_technology_and_leaves_the_other():
    _stage("kpi", [("4G a.csv", _4g(30)), ("SHAMS-3G.zip", _3g())])
    S.apply("kpi")
    res, _ = _stage("kpi", [("4G b.csv", _4g(95))])
    assert [f.name for f in res.replaced()] == ["4G a.csv"]
    assert [f.name for f in res.kept()] == ["SHAMS-3G.zip"]
    S.apply("kpi")
    assert _names("kpi") == ["4G b.csv", "SHAMS-3G.zip"]
    assert _blobs() == {"4G b.csv", "SHAMS-3G.zip"}


def test_cancel_throws_the_upload_away_and_keeps_the_active_data():
    _stage("kpi", [("a.csv", _4g(30))])
    S.apply("kpi")
    _stage("kpi", [("b.csv", _4g(95))])
    S.cancel("kpi")
    assert _names("kpi") == ["a.csv"] and not S.resource("kpi").pending
    assert _blobs() == {"a.csv"}
    with pytest.raises(S.ResourceError, match="no upload"):
        S.apply("kpi")                                       # nothing to apply


def test_site_details_kmz_and_coverage_are_replaced_as_a_whole():
    _stage("coverage", [("BAS_grid.csv", _grid_csv(40)), ("NAS_grid.csv", _grid_csv(30))])
    S.apply("coverage")
    assert _names("coverage") == ["BAS_grid.csv", "NAS_grid.csv"]
    res, _ = _stage("coverage", [("BAS_grid_new.csv", _grid_csv(20))])
    assert sorted(f.name for f in res.replaced()) == ["BAS_grid.csv", "NAS_grid.csv"]
    S.apply("coverage")
    assert _names("coverage") == ["BAS_grid_new.csv"]
    # a single-file resource keeps the newest upload only
    one = S.put_file("a.kmz", b"a")
    two = S.put_file("b.kmz", b"b")
    S.stage("kmz", [one])
    res = S.stage("kmz", [two])
    assert [f.name for f in res.pending] == ["b.kmz"]


def test_delete_needs_an_explicit_action_and_removes_the_file():
    _stage("kpi", [("4G.csv", _4g(30)), ("SHAMS-3G.zip", _3g())])
    S.apply("kpi")
    three_g = next(f for f in S.active_files("kpi") if f.role == "3G KPI")
    S.remove("kpi", three_g.sha1)
    assert _names("kpi") == ["4G.csv"] and _blobs() == {"4G.csv"}


def test_the_store_is_read_again_after_a_restart():
    """A new Python process — the app opened again — finds the applied data."""
    from rfopt.complaints.target_store import save_target
    _stage("kpi", [("R5 4G Monitoring Hourly KPI.csv", _4g(30))])
    S.apply("kpi")
    save_target(_target(), "Target 13-Sep.xlsx")
    code = ("from rfopt.resources import store as S\n"
            "from rfopt.complaints.target_store import load_active\n"
            "t = load_active()\n"
            "print(S.active_files('kpi')[0].name, '|', t.name, t.records, S.health().state)\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, env={**os.environ}, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "R5 4G Monitoring Hourly KPI.csv | Target 13-Sep.xlsx 2 saved"


def test_auto_saved_is_shown_only_when_the_data_is_really_on_disk():
    assert S.health().state == "empty"
    _stage("kpi", [("a.csv", _4g(30))])
    assert S.health().state == "empty"                         # an upload is not active data
    S.apply("kpi")
    hl = S.health()
    assert hl.state == "saved" and hl.message == "Data Auto-Saved" and hl.saved == 1

    S.active_files("kpi")[0].path.unlink()                    # the file vanishes
    hl = S.health()
    assert hl.state == "error" and "a.csv" in hl.detail

    _stage("kpi", [("b.csv", _4g(95))])
    S.apply("kpi")
    assert S.health().state == "saved"
    (S.root() / "registry.json").write_text("{ broken", encoding="utf-8")
    S._MEMO.clear()
    hl = S.health()
    assert hl.state == "error" and hl.message == "Store recovered"
    assert _names("kpi") == ["b.csv"]                          # the verified copy is used


def test_the_earlier_versioned_registry_becomes_active_data():
    """Current files stay active, a pending upload stays pending; Previous and
    Archived versions — and anything the earlier release took from the PC's
    folders by itself — are dropped, their files deleted."""
    cur = S.put_file("4G current.csv", _4g(30))
    old = S.put_file("4G previous.csv", _4g(50))
    new = S.put_file("4G pending.csv", _4g(95))
    ep = S.put_file("EP found.xlsx", b"ep")
    tgt = S.put_file("Target.xlsx", b"t")
    hist = S.put_file("CC Process.xlsx", b"h")

    def f(sf, role, kept=None):
        d = sf.to_json()
        d.update(role=role, kept_from=kept)
        return d

    reg = {"schema": 1, "bootstrapped": True, "saved_at": "2026-09-18 10:00:00", "resources": {
        "kpi": {"active": 2, "next": 4, "versions": [
            {"number": 1, "files": [f(old, "4G KPI")], "uploaded_at": "2026-09-17 09:00:00",
             "source": "upload", "pending": False, "applied_at": "2026-09-17 09:01:00",
             "retired_at": "2026-09-18 09:00:00"},
            {"number": 2, "files": [f(cur, "4G KPI")], "uploaded_at": "2026-09-18 08:59:00",
             "source": "upload", "pending": False, "applied_at": "2026-09-18 09:00:00"},
            {"number": 3, "files": [f(new, "4G KPI")], "uploaded_at": "2026-09-18 09:30:00",
             "source": "upload", "pending": True}]},
        "ep": {"active": 1, "next": 2, "versions": [
            {"number": 1, "files": [f(ep, "EP tracker")], "uploaded_at": "2026-09-18 08:00:00",
             "source": "imported on first start · found in ReceiveFiles", "pending": False,
             "applied_at": "2026-09-18 08:00:00"}]},
        "complaints": {"active": 1, "next": 2, "versions": [
            {"number": 1, "files": [f(tgt, "Daily Target"), f(hist, "Complaint history")],
             "uploaded_at": "2026-09-18 08:00:00",
             "source": "imported on first start · the Complaints page's saved upload · "
                       "history found in Audit", "pending": False,
             "applied_at": "2026-09-18 08:00:00"}]}}}
    (S.root() / "registry.json").write_text(json.dumps(reg), encoding="utf-8")
    S._MEMO.clear()

    kpi = S.resource("kpi")
    assert [x.name for x in kpi.files] == ["4G current.csv"]
    assert [x.name for x in kpi.pending] == ["4G pending.csv"]
    assert S.active_files("ep") == []                          # found on the PC: dropped
    assert _names("complaints") == ["Target.xlsx"]             # the user's upload stays
    assert _blobs() == {"4G current.csv", "4G pending.csv", "Target.xlsx"}
    assert json.loads((S.root() / "registry.json").read_text(encoding="utf-8"))["schema"] == 2


def test_first_start_moves_only_the_apps_own_earlier_uploads(monkeypatch, tmp_path):
    """The uploads the Sites and Complaints pages once kept in the app's own
    folder become active data. The computer's folders are never looked at:
    matching exports on a Desktop and in Downloads stay unknown to the app."""
    import _resources as R
    home = tmp_path / "home"
    for folder, names in (("Desktop", ["R5 4G Monitoring Hourly KPI.zip", "Target 18-Sep.xlsx",
                                       "WK38 Engineering Parameter Tracker.xlsx"]),
                          ("Downloads", ["R5_Sites.kmz", "SHAMS-3G_Query_Result.zip"])):
        (home / folder).mkdir(parents=True)
        for n in names:
            (home / folder / n).write_bytes(b"x")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    base = S.root().parent
    base.mkdir(parents=True, exist_ok=True)
    (base / "sm_uploaded_R5_Sites.kmz").write_bytes(b"kmz-bytes")
    comp = base / "complaints"
    comp.mkdir()
    (comp / "daily_target_abc.xlsx").write_bytes(_target())
    (comp / "daily_target.json").write_text(json.dumps({
        "name": "Target 13-Sep.xlsx", "uploaded_at": "2026-09-13 10:00", "records": 2,
        "size": 1, "sha1": "abc", "file": "daily_target_abc.xlsx", "columns": []}),
        encoding="utf-8")

    assert R._move_earlier_uploads() == []
    assert _names("kmz") == ["sm_uploaded_R5_Sites.kmz"]
    assert _names("complaints") == ["Target 13-Sep.xlsx"]
    assert _names("kpi") == [] and _names("ep") == [] and _names("coverage") == []
    assert (base / "sm_uploaded_R5_Sites.kmz").is_file()      # the old files are left alone


def test_no_code_of_the_app_searches_the_computers_folders():
    """No glob, walk or listing of a folder outside the app's own storage, and
    no automatic file finder anywhere in the app or the engine."""
    allowed = {"rfopt/resources/store.py", "rfopt/ingest/_io.py"}      # their own cache folders
    hits = []
    for base in ("app", "rfopt"):
        for p in (ROOT / base).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if rel in allowed:
                continue
            src = p.read_text(encoding="utf-8")
            for needle in (".glob(", "rglob(", "os.walk(", "os.listdir(", "find_file(",
                           "iterdir("):
                if needle in src:
                    hits.append((rel, needle))
    assert hits == []


def test_files_are_checked_before_they_can_be_applied():
    res, bad = _stage("kpi", [("notes.csv", b"a,b\n1,2\n")])
    assert res is None and bad and "not a raw hourly KPI export" in bad[0][1]
    assert _blobs() == set()

    res, bad = _stage("complaints", [("Target 13-Sep.xlsx", _target())])
    assert not bad and res.pending[0].role == "Daily Target"
    assert res.pending[0].summary["tickets"] == 2

    res, bad = _stage("coverage", [("BASDLCoverageInsight_LTE_Grid_20260909142303.csv",
                                    _grid_csv()),
                                   ("BAS_LAT_drive.csv", b"x,y\n1,2\n"),
                                   ("walk-test.log", b"some log\n"),
                                   ("random.csv", b"x,y\n1,2\n")])
    roles = sorted(f.role for f in res.pending)
    assert roles == ["Coverage grid · BAS", "LAT · BAS_LAT_drive.csv", "Log · walk-test.log"]
    assert [n for n, _ in bad] == ["random.csv"]
    S.apply("coverage")
    import _resources as R
    grids = R.coverage()
    assert len(grids) == 1 and next(iter(grids.values())).rows == 40
    assert [f.role for f in R.coverage_extras()] == ["LAT · BAS_LAT_drive.csv",
                                                     "Log · walk-test.log"]


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #
def _html(at) -> str:
    return " ".join(e.proto.body for e in at.get("html"))


def test_the_data_resources_page_replace_preview_apply_and_active_data():
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    at = AppTest.from_file(str(APP / "views/data_resources.py"), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    text = _html(at)
    for title in ("Site Details Data (EP)", "KPI Data", "KMZ Data", "Complaint Data",
                  "Coverage Data", "Active Data", "Nothing saved yet"):
        assert title in text, title
    assert not at.get("file_uploader")                          # no upload until asked

    # Upload: the panel with its file field
    at.button(key="dr_rep_kpi").click().run()
    assert not at.exception, at.exception
    assert len(at.get("file_uploader")) == 1 and "Select File" in _html(at)

    # a file (as the field hands it over) is validated and previewed; nothing active yet
    _stage("kpi", [("Shams-4G-KPI-old.csv", _4g(30))])
    at.run()
    assert "Upload awaiting Apply" in _html(at) and _names("kpi") == []
    at.button(key="dr_apply_kpi").click().run()
    assert not at.exception, at.exception
    assert _names("kpi") == ["Shams-4G-KPI-old.csv"]
    assert any("KPI Data applied" in s.value for s in at.success)
    assert "Data Auto-Saved" in _html(at)

    # Replace: the new file is previewed with the file it replaces; Cancel keeps the old
    at.button(key="dr_rep_kpi").click().run()
    _stage("kpi", [("Shams-4G-KPI-new.csv", _4g(95))])
    at.run()
    text = _html(at)
    assert "Shams-4G-KPI-new.csv" in text and "Replaced — deleted on Apply" in text
    assert _names("kpi") == ["Shams-4G-KPI-old.csv"]
    at.button(key="dr_cancel_kpi").click().run()
    assert _names("kpi") == ["Shams-4G-KPI-old.csv"] and not S.resource("kpi").pending

    # ... and Apply makes the new file the only active one
    at.button(key="dr_rep_kpi").click().run()
    _stage("kpi", [("Shams-4G-KPI-new.csv", _4g(95))])
    at.run()
    at.button(key="dr_apply_kpi").click().run()
    assert not at.exception, at.exception
    assert _names("kpi") == ["Shams-4G-KPI-new.csv"]
    text = _html(at)
    for col in ("File Name", "Data Type", "Upload Date", "File Size", "Status", "Actions"):
        assert f"<span>{col}</span>" in text, col
    assert "Shams-4G-KPI-new.csv" in text and "Shams-4G-KPI-old.csv" not in text
    for word in WORDS_GONE:
        assert word not in text, word

    # View: the active file, with a download
    at.button(key="dr_view_kpi").click().run()
    assert not at.exception, at.exception
    assert any(d.proto.label.startswith("Download") for d in at.get("download_button"))

    # Delete asks first; Cancel keeps it, Delete removes it
    at.button(key="dr_rx_0").click().run()
    assert "Delete" in [b.label for b in at.button]
    at.button(key="dr_confirm_no").click().run()
    assert _names("kpi") == ["Shams-4G-KPI-new.csv"]
    at.button(key="dr_rx_0").click().run()
    at.button(key="dr_confirm_yes").click().run()
    assert not at.exception, at.exception
    assert _names("kpi") == []


@pytest.mark.parametrize("page", ["views/site_map.py", "views/kpi_analysis.py",
                                  "views/kpi_draw.py", "views/complaint_analysis.py",
                                  "views/dashboard.py"])
def test_no_other_page_uploads_anything(page):
    """Every upload is on the Data Resources page; the others read the active
    data and say nothing about it — no page carries a data monitor."""
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    _stage("kpi", [("R5 4G Monitoring Hourly KPI.csv", _4g(30))])
    S.apply("kpi")
    from rfopt.complaints.target_store import save_target
    save_target(_target(), "Target 13-Sep.xlsx")
    at = AppTest.from_file(str(APP / page), default_timeout=240)
    at.run()
    assert not at.exception, at.exception
    assert not at.get("file_uploader"), page
    side = " ".join(e.proto.body for e in at.sidebar.get("html"))
    for name in ("R5 4G Monitoring Hourly KPI.csv", "Target 13-Sep.xlsx"):
        assert name not in side, page             # the data monitor is gone
    assert "Manage in Data Resources" not in side, page


def test_a_registry_swap_waits_out_a_brief_windows_lock(monkeypatch):
    """A virus scanner (or a reader) holding registry.json for a moment makes
    Windows refuse the swap with "Access is denied": the save waits it out."""
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) <= 2:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(S.os, "replace", flaky)
    _stage("kpi", [("4G.csv", _4g(30))])
    S.apply("kpi")
    assert _names("kpi") == ["4G.csv"] and len(calls) > 2
