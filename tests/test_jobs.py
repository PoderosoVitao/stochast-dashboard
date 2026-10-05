import asyncio
from pathlib import Path

import pytest

from stochast_dashboard.jobs import JobAlreadyRunning, JobState

ADAPTER_SOURCE = """
from stochast.adapters import AgentResult

class FakeAdapter:
    def run(self, prompt):
        return AgentResult(output="ok")

def build_adapter():
    return FakeAdapter()
"""

SCENARIO_SOURCE = """
from stochast import scenario, expect

@scenario(runs={runs})
def my_scenario(agent):
    result = agent.run("hello")
    expect.output_contains(result, "ok")
"""


@pytest.fixture
def anyio_backend():
    return "asyncio"


def write_fixture(tmp_path: Path, runs: int = 3) -> tuple[Path, str]:
    scenario_file = tmp_path / "scenarios.py"
    scenario_file.write_text(SCENARIO_SOURCE.format(runs=runs))
    adapter_file = tmp_path / "adapter.py"
    adapter_file.write_text(ADAPTER_SOURCE)
    return scenario_file, f"{adapter_file}:build_adapter"


async def wait_until_idle(job: JobState) -> None:
    while job._busy:  # noqa: SLF001
        await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_start_publishes_the_expected_event_sequence(tmp_path: Path):
    scenario_file, adapter_spec = write_fixture(tmp_path, runs=3)
    job = JobState(out_dir=tmp_path / "out")

    await job.start(scenario_file, adapter_spec, concurrency=1)
    await wait_until_idle(job)

    types = [e["type"] for e in job.events]
    assert types == [
        "job_started",
        "run_completed",
        "run_completed",
        "run_completed",
        "scenario_finished",
        "job_finished",
    ]
    assert job.events[0]["scenarios"] == [{"name": "my_scenario", "runs": 3}]
    assert all(e["passed"] is True for e in job.events[1:4])


@pytest.mark.anyio
async def test_start_persists_records_and_a_scenario_finished_event(tmp_path: Path):
    scenario_file, adapter_spec = write_fixture(tmp_path, runs=2)
    out_dir = tmp_path / "out"
    job = JobState(out_dir=out_dir)

    await job.start(scenario_file, adapter_spec, concurrency=1)
    await wait_until_idle(job)

    assert (out_dir / "my_scenario.json").exists()
    finished = [e for e in job.events if e["type"] == "scenario_finished"][0]
    assert finished["stats"]["total_runs"] == 2
    assert finished["stats"]["passed_runs"] == 2


@pytest.mark.anyio
async def test_start_raises_when_a_job_is_already_running(tmp_path: Path):
    scenario_file, adapter_spec = write_fixture(tmp_path, runs=1)
    job = JobState(out_dir=tmp_path / "out")
    job._busy = True  # noqa: SLF001

    with pytest.raises(JobAlreadyRunning):
        await job.start(scenario_file, adapter_spec)


@pytest.mark.anyio
async def test_start_rejects_a_keyword_that_matches_nothing(tmp_path: Path):
    scenario_file, adapter_spec = write_fixture(tmp_path, runs=1)
    job = JobState(out_dir=tmp_path / "out")

    with pytest.raises(ValueError, match="no scenarios matched"):
        await job.start(scenario_file, adapter_spec, keyword="nope")


@pytest.mark.anyio
async def test_preview_discovers_without_starting_a_job(tmp_path: Path):
    scenario_file, _ = write_fixture(tmp_path, runs=5)
    job = JobState(out_dir=tmp_path / "out")

    scenarios = await job.preview(scenario_file)

    assert scenarios == [{"name": "my_scenario", "runs": 5, "tags": []}]
    assert job._busy is False  # noqa: SLF001
    assert job.events == []


@pytest.mark.anyio
async def test_completed_runs_are_kept_for_inspection(tmp_path: Path):
    scenario_file, adapter_spec = write_fixture(tmp_path, runs=3)
    job = JobState(out_dir=tmp_path / "out")

    await job.start(scenario_file, adapter_spec, concurrency=1)
    await wait_until_idle(job)

    records = job.records("my_scenario")
    assert sorted(r.run_index for r in records) == [0, 1, 2]
    assert job.record("my_scenario", 1).final_output == "ok"


@pytest.mark.anyio
async def test_record_lookups_raise_for_unknown_scenarios_or_runs(tmp_path: Path):
    scenario_file, adapter_spec = write_fixture(tmp_path, runs=1)
    job = JobState(out_dir=tmp_path / "out")

    await job.start(scenario_file, adapter_spec, concurrency=1)
    await wait_until_idle(job)

    with pytest.raises(KeyError):
        job.records("no_such_scenario")
    with pytest.raises(KeyError):
        job.record("my_scenario", 99)


@pytest.mark.anyio
async def test_subscribe_replays_events_published_before_subscribing(tmp_path: Path):
    scenario_file, adapter_spec = write_fixture(tmp_path, runs=1)
    job = JobState(out_dir=tmp_path / "out")

    await job.start(scenario_file, adapter_spec, concurrency=1)
    await wait_until_idle(job)

    subscription = job.subscribe()
    first = await subscription.__anext__()
    await subscription.aclose()

    assert first == job.events[0]
