import time
from pathlib import Path

from fastapi.testclient import TestClient

from stochast_dashboard import app as app_module

ADAPTER_SOURCE = """
from stochast.adapters import AgentResult

class FakeAdapter:
    def run(self, prompt):
        return AgentResult(output="ok")

def build_adapter():
    return FakeAdapter()
"""

SCENARIO_SOURCE = """
from stochast import scenario

@scenario(runs=2)
def my_scenario(agent):
    agent.run("hello")
"""


def write_fixture(tmp_path: Path) -> tuple[Path, str]:
    scenario_file = tmp_path / "scenarios.py"
    scenario_file.write_text(SCENARIO_SOURCE)
    adapter_file = tmp_path / "adapter.py"
    adapter_file.write_text(ADAPTER_SOURCE)
    return scenario_file, f"{adapter_file}:build_adapter"


def test_index_serves_the_static_page():
    with TestClient(app_module.app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Stochast Dashboard" in response.text


def test_list_scenarios_previews_without_starting_a_job(tmp_path: Path):
    scenario_file, _ = write_fixture(tmp_path)
    app_module.job.out_dir = tmp_path / "out"

    with TestClient(app_module.app) as client:
        response = client.post("/api/scenarios", json={"path": str(scenario_file)})

    assert response.status_code == 200
    assert response.json() == [{"name": "my_scenario", "runs": 2, "tags": []}]


def test_list_scenarios_returns_an_empty_list_for_a_path_with_no_scenarios():
    with TestClient(app_module.app) as client:
        response = client.post("/api/scenarios", json={"path": "/no/such/path.py"})

    assert response.status_code == 200
    assert response.json() == []


def test_list_scenarios_returns_400_when_a_scenario_file_fails_to_import(tmp_path: Path):
    broken_file = tmp_path / "broken.py"
    broken_file.write_text("this is not valid python (")

    with TestClient(app_module.app) as client:
        response = client.post("/api/scenarios", json={"path": str(broken_file)})

    assert response.status_code == 400


def test_list_scenarios_rejects_a_plain_get_so_it_cannot_be_triggered_cross_origin():
    with TestClient(app_module.app) as client:
        response = client.get("/api/scenarios", params={"path": "/no/such/path.py"})

    assert response.status_code == 405


def test_start_run_returns_202_and_eventually_finishes(tmp_path: Path):
    scenario_file, adapter_spec = write_fixture(tmp_path)
    app_module.job.out_dir = tmp_path / "out"

    with TestClient(app_module.app) as client:
        response = client.post(
            "/api/runs",
            json={"path": str(scenario_file), "adapter": adapter_spec, "concurrency": 1},
        )
        assert response.status_code == 202
        while app_module.job._busy:  # noqa: SLF001
            time.sleep(0.01)


def test_start_run_returns_409_when_a_job_is_already_running(tmp_path: Path):
    scenario_file, adapter_spec = write_fixture(tmp_path)
    app_module.job.out_dir = tmp_path / "out"
    app_module.job._busy = True  # noqa: SLF001

    try:
        with TestClient(app_module.app) as client:
            response = client.post(
                "/api/runs",
                json={"path": str(scenario_file), "adapter": adapter_spec},
            )
        assert response.status_code == 409
    finally:
        app_module.job._busy = False  # noqa: SLF001


def test_start_run_returns_400_when_no_scenarios_match(tmp_path: Path):
    scenario_file, adapter_spec = write_fixture(tmp_path)
    app_module.job.out_dir = tmp_path / "out"

    with TestClient(app_module.app) as client:
        response = client.post(
            "/api/runs",
            json={
                "path": str(scenario_file),
                "adapter": adapter_spec,
                "keyword": "nope",
            },
        )

    assert response.status_code == 400
