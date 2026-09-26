"""The EP tracker is part of the truth on whether a site is on air.

The KMZ is not always updated when a site goes on air, so a site the EP lists
as active is On Air everywhere the app reads a site's status — the Site Map,
the Overview counts and the Sleep Analysis plan-site check — whatever the KMZ
still says. The rule is global: nothing here is about one site.
"""

import pandas as pd

from rfopt.ingest.site_status import apply_ep_status, ep_on_air_sites
from rfopt.sleep import analysis as A


def _ep():
    return pd.DataFrame({
        "site_id": ["BAS3214", "BAS3214", "NAS0100", "EMA0200", "SAM0300", "BAS0999"],
        "latitude": [30.5, 30.5, 31.0, None, 30.9, 30.7],
        "longitude": [47.8, 47.8, 46.2, None, 47.1, 47.6],
        "status": ["Activated", "", "Deactivated", "Activated", "Activated", "On Air"],
        "_sheet": ["LTE", "UMTS", "LTE", "LTE", "LTE Deactive", "GSM"],
        "region": ["Region 5"] * 6,
    })


def test_the_ep_lists_a_site_on_air_from_its_active_cells_only():
    on = ep_on_air_sites(_ep())
    assert on == {"BAS3214", "BAS0999"}
    # deactivated status, no position, or only on a Deactive sheet: not on air
    assert not {"NAS0100", "EMA0200", "SAM0300"} & on
    assert ep_on_air_sites(None) == frozenset()
    assert ep_on_air_sites(pd.DataFrame()) == frozenset()


def test_only_the_eps_region_5_rows_are_read_never_the_site_id_prefix():
    ep = pd.DataFrame({
        "site_id": ["XYZ0001", "BAS0777", "NAS0888", "SAM0999"],
        "latitude": [30.5, 30.6, 30.7, 30.8], "longitude": [47.8, 47.7, 47.6, 47.5],
        "status": ["Activated"] * 4, "_sheet": ["LTE"] * 4,
        # the Region value as the EP writes it, give or take spacing and case
        "region": ["Region 5", "Region 4", " region  5 ", "Region 50"],
    })
    # a Region 5 site counts whatever its prefix; a BAS site in another region does not
    assert ep_on_air_sites(ep) == {"XYZ0001", "NAS0888"}
    # an EP without a Region field says nothing about any site
    assert ep_on_air_sites(ep.drop(columns="region")) == frozenset()
    # outside Region 5 the KMZ status stands
    kmz = pd.DataFrame({"site_id": ["BAS0777", "XYZ0001"], "status": ["Planned", "Planned"]})
    out = apply_ep_status(kmz, ep_on_air_sites(ep)).set_index("site_id")["status"]
    assert out.to_dict() == {"BAS0777": "Planned", "XYZ0001": "On Air"}


def test_an_ep_active_site_is_on_air_whatever_the_kmz_says():
    kmz = pd.DataFrame({"site_id": ["BAS3214", "BAS0480", "NAS0100"],
                        "status": ["Planned", "On Air", "Planned"],
                        "air": ["planned", "onair", "planned"]})
    out = apply_ep_status(kmz, ep_on_air_sites(_ep()))
    assert out.set_index("site_id")["status"].to_dict() == {
        "BAS3214": "On Air", "BAS0480": "On Air", "NAS0100": "Planned"}
    assert out.set_index("site_id")["air"].to_dict() == {
        "BAS3214": "onair", "BAS0480": "onair", "NAS0100": "planned"}
    assert kmz.loc[0, "status"] == "Planned"            # the input is left alone
    assert apply_ep_status(kmz, frozenset()) is kmz     # no EP: the KMZ stands


def test_the_plan_site_status_reads_the_ep_before_the_kmz():
    on = ep_on_air_sites(_ep())
    kmz = pd.DataFrame({"site_id": ["BAS3214", "NAS0100"], "status": ["Planned", "Planned"]})
    assert A.plan_site_status("BAS3214", kmz, on) == A.ON_AIR        # KMZ behind
    assert A.plan_site_status("bas0999", kmz, on) == A.ON_AIR        # not in the KMZ
    assert A.plan_site_status("BAS3214", None, on) == A.ON_AIR       # no KMZ at all
    assert A.plan_site_status("NAS0100", kmz, on) == A.NOT_ON_AIR    # both say planned
    assert A.plan_site_status("NAS0100", kmz) == A.NOT_ON_AIR
    assert A.plan_site_status("NAS0100", None, on) == ""             # nothing to read


def test_a_planned_ticket_whose_site_the_ep_puts_on_air_is_judged_on_the_data():
    on = ep_on_air_sites(_ep())
    kmz = pd.DataFrame({"site_id": ["BAS3214"], "status": ["Planned"]})
    plan = A.plan_site_status("BAS3214", kmz, on)
    good, text = A.judge("planned", "BAS0480-2", None, A.Point(-92.0, 400, 8.0), plan,
                         "BAS3214", metres=250.0)
    assert good == A.SOLVE
    assert text.startswith("The planned site BAS3214 is now on air.")
    assert "still" not in text


def test_the_eps_REGION_column_is_read_as_the_file_writes_it(tmp_path):
    """The EP's own header is `REGION` and its value `Region 5`."""
    from rfopt.ingest.cellparams import load_cell_params

    def sheet(ids, region, lte):
        n = len(ids)
        return pd.DataFrame({
            ("Site ID" if lte else "Site Code"): ids, "Cell Name": [f"C_{i}" for i in ids],
            ("Activation Status" if lte else "Status"): ["Activated"] * n,
            "Latitude": [30.5] * n, "Longitude": [47.8] * n, "Azimuth": [0] * n,
            ("M-DownTilt" if lte else "Mechanical Downtilt"): [2] * n, "REGION": region})

    path = tmp_path / "ep.xlsx"
    with pd.ExcelWriter(path) as w:
        sheet(["BAS3214", "XYZ0001", "BAS0777"], ["Region 5", "Region 5", "Region 4"],
              True).to_excel(w, sheet_name="LTE", index=False)
        sheet(["NAS0555", "SAM0666"], ["Region 5", "Region 3"], False).to_excel(
            w, sheet_name="UMTS", index=False)
    ep = pd.concat([load_cell_params(path, technology=t, region=None, include_deactive=True,
                                     use_cache=False).df for t in ("LTE", "UMTS")],
                   ignore_index=True)
    assert ep_on_air_sites(ep) == {"BAS3214", "XYZ0001", "NAS0555"}
