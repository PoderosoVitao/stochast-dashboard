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


def run_job_to_completion(client: TestClient, tmp_path: Path) -> None:
    scenario_file, adapter_spec = write_fixture(tmp_path)
    app_module.job.out_dir = tmp_path / "out"
    response = client.post(
        "/api/runs",
        json={"path": str(scenario_file), "adapter": adapter_spec, "concurrency": 1},
    )
    assert response.status_code == 202
    while app_module.job._busy:  # noqa: SLF001
        time.sleep(0.01)


def test_stats_endpoint_reports_the_current_runs_statistics(tmp_path: Path):
    with TestClient(app_module.app) as client:
        run_job_to_completion(client, tmp_path)
        response = client.get("/api/runs/current/scenarios/my_scenario/stats")

    assert response.status_code == 200
    assert response.json()["total_runs"] == 2


def test_run_detail_returns_the_full_record(tmp_path: Path):
    with TestClient(app_module.app) as client:
        run_job_to_completion(client, tmp_path)
        response = client.get("/api/runs/current/scenarios/my_scenario/runs/1")

    assert response.status_code == 200
    body = response.json()
    assert body["run_index"] == 1
    assert body["final_output"] == "ok"
    assert "raw_messages" in body


def test_run_detail_returns_404_for_a_run_that_does_not_exist(tmp_path: Path):
    with TestClient(app_module.app) as client:
        run_job_to_completion(client, tmp_path)
        response = client.get("/api/runs/current/scenarios/my_scenario/runs/99")

    assert response.status_code == 404


def test_chart_endpoint_serves_svg(tmp_path: Path):
    with TestClient(app_module.app) as client:
        run_job_to_completion(client, tmp_path)
        response = client.get("/api/runs/current/scenarios/my_scenario/charts/latency.svg")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert "<svg" in response.text


def test_chart_endpoint_returns_404_for_an_unknown_chart(tmp_path: Path):
    with TestClient(app_module.app) as client:
        run_job_to_completion(client, tmp_path)
        response = client.get("/api/runs/current/scenarios/my_scenario/charts/pie.svg")

    assert response.status_code == 404


def test_scenario_endpoints_return_404_for_an_unknown_scenario(tmp_path: Path):
    with TestClient(app_module.app) as client:
        run_job_to_completion(client, tmp_path)
        response = client.get("/api/runs/current/scenarios/nope/stats")

    assert response.status_code == 404


def test_sse_serialization_tolerates_non_json_tool_results():
    event = {"type": "scenario_finished", "result": object()}

    assert app_module._format_sse(event).startswith("data: ")  # noqa: SLF001


def test_frontend_files_are_always_revalidated_so_updates_never_mix_versions():
    with TestClient(app_module.app) as client:
        for path in ("/", "/static/app.js", "/static/style.css"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"


def test_chart_endpoint_serves_the_dark_variant(tmp_path: Path):
    with TestClient(app_module.app) as client:
        run_job_to_completion(client, tmp_path)
        dark = client.get("/api/runs/current/scenarios/my_scenario/charts/latency.svg?theme=dark")
        invalid = client.get(
            "/api/runs/current/scenarios/my_scenario/charts/latency.svg?theme=sepia"
        )

    assert dark.status_code == 200
    assert "#1a1a19" in dark.text
    assert invalid.status_code == 422
