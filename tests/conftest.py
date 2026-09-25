import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def sample_data():
    """Ensure the demo data set exists."""
    need = ["r5_lte_kpi_hourly.xlsx", "r5_lte_kpi_daily.csv",
            "r5_site_database.csv"]
    if not all((SAMPLE / n).exists() for n in need):
        subprocess.run([sys.executable, str(ROOT / "scripts" /
                        "generate_sample_data.py")], check=True)
    return SAMPLE


@pytest.fixture(scope="session")
def analysis(sample_data):
    from rfopt.pipeline import run_analysis
    return run_analysis(
        str(sample_data / "r5_lte_kpi_daily.csv"),
        site_db=str(sample_data / "r5_site_database.csv"),
        region_filter="R5", env_kind="suburban",
    )


@pytest.fixture(autouse=True)
def _own_data_store(tmp_path_factory, monkeypatch):
    """Every test gets its own empty Data Resources store — never the user's
    ~/.rfopt_cache — and nothing is written to the user's Desktop."""
    monkeypatch.setenv("RFOPT_CACHE_DIR", str(tmp_path_factory.mktemp("rfopt_cache")))
    # exports "to the Desktop" land in a folder of the test's own
    monkeypatch.setenv("RFOPT_DESKTOP", str(tmp_path_factory.mktemp("desktop")))


@pytest.fixture
def put_resource():
    """Store a file and apply it as the active data of a resource."""
    from rfopt.resources import store

    def put(kind, name, data, role=""):
        f = store.put_file(name, data)
        f.role = role
        store.stage(kind, [f])
        store.apply(kind)
        return f

    return put
