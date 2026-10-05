from __future__ import annotations

import asyncio
import dataclasses
import threading
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

from stochast.adapters import AgentAdapter
from stochast.discovery import discover_scenarios, resolve_adapter_factory
from stochast.records import RunRecord, save_run_records
from stochast.runner import run_scenario
from stochast.scenario import Scenario, clear_registry, registered_scenarios
from stochast.stats import analyze_scenario


class JobAlreadyRunning(Exception):
    pass


# Orchestrates a single stochast run in the background, broadcasting its
# progress as JSON-able events to every connected dashboard client. One job
# runs at a time; a second start() while busy raises JobAlreadyRunning.
class JobState:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.events: list[dict[str, Any]] = []
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._discovery_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._busy = False
        self._records: dict[str, list[RunRecord]] = {}
        self._records_lock = threading.Lock()

    # Imports scenarios at `path` without starting anything, so a client can
    # preview what would run before committing.
    async def preview(self, path: Path) -> list[dict[str, Any]]:
        async with self._discovery_lock:
            scenarios = self._discover(path)
        return [{"name": s.name, "runs": s.runs, "tags": s.tags} for s in scenarios]

    # Starts a run in a background thread and returns once it has been
    # scheduled, without waiting for it to finish.
    async def start(
        self,
        path: Path,
        adapter_spec: str,
        *,
        keyword: str = "",
        runs: int | None = None,
        concurrency: int = 5,
        seed: int | None = None,
    ) -> None:
        async with self._discovery_lock:
            if self._busy:
                raise JobAlreadyRunning("a run is already in progress")
            scenarios = [s for s in self._discover(path) if keyword in s.name]
            if not scenarios:
                raise ValueError("no scenarios matched")
            if runs is not None:
                scenarios = [dataclasses.replace(s, runs=runs) for s in scenarios]
            factory = resolve_adapter_factory(adapter_spec)
            self._busy = True
            self._loop = asyncio.get_running_loop()
            self.events = []
            with self._records_lock:
                self._records = {s.name: [] for s in scenarios}

        self.publish(
            {
                "type": "job_started",
                "scenarios": [{"name": s.name, "runs": s.runs} for s in scenarios],
            }
        )
        thread = threading.Thread(
            target=self._run_job, args=(scenarios, factory, concurrency, seed), daemon=True
        )
        thread.start()

    def _discover(self, path: Path) -> list[Scenario]:
        clear_registry()
        discover_scenarios(path)
        return registered_scenarios()

    # Runs every matched scenario to completion, persisting and broadcasting
    # results as it goes. Executes on a background thread.
    def _run_job(
        self,
        scenarios: list[Scenario],
        factory: Callable[[], AgentAdapter],
        concurrency: int,
        seed: int | None,
    ) -> None:
        try:
            for scenario in scenarios:
                records = run_scenario(
                    scenario,
                    factory,
                    concurrency=concurrency,
                    seed=seed,
                    on_run_complete=self._make_on_run_complete(scenario.name),
                )
                save_run_records(records, self.out_dir / f"{scenario.name}.json")
                stats = analyze_scenario(records)
                self.publish(
                    {
                        "type": "scenario_finished",
                        "scenario": scenario.name,
                        "stats": dataclasses.asdict(stats),
                    }
                )
            self.publish({"type": "job_finished"})
        except Exception as exc:
            self.publish({"type": "job_error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            self._busy = False

    # Returns the current job's completed runs for a scenario, in completion
    # order. Raises KeyError for a scenario the current job doesn't include.
    def records(self, scenario_name: str) -> list[RunRecord]:
        with self._records_lock:
            return list(self._records[scenario_name])

    # Returns one completed run's full record. Raises KeyError if that run
    # hasn't completed yet or doesn't exist.
    def record(self, scenario_name: str, run_index: int) -> RunRecord:
        for record in self.records(scenario_name):
            if record.run_index == run_index:
                return record
        raise KeyError(run_index)

    # Builds a run_scenario callback bound to one scenario's name, so each
    # `run_completed` event says which scenario it belongs to.
    def _make_on_run_complete(self, scenario_name: str) -> Callable[[RunRecord], None]:
        def callback(record: RunRecord) -> None:
            with self._records_lock:
                self._records[scenario_name].append(record)
            self.publish(
                {
                    "type": "run_completed",
                    "scenario": scenario_name,
                    "run_index": record.run_index,
                    "passed": record.passed,
                    "latency_ms": record.latency_ms,
                    "cost_usd": record.cost_usd,
                }
            )

        return callback

    # Thread-safe: schedules the actual append/broadcast on the event loop
    # that `start()` captured, regardless of which thread calls it.
    def publish(self, event: dict[str, Any]) -> None:
        assert self._loop is not None
        self._loop.call_soon_threadsafe(self._publish_on_loop, event)

    def _publish_on_loop(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    # Replays everything published so far, then streams new events as they
    # arrive, for as long as the caller keeps consuming.
    async def subscribe(self) -> AsyncGenerator[dict[str, Any], None]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        replay = list(self.events)
        self._subscribers.append(queue)
        try:
            for event in replay:
                yield event
            while True:
                yield await queue.get()
        finally:
            self._subscribers.remove(queue)
