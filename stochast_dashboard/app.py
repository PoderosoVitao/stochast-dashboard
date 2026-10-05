from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from stochast.records import RunRecord
from stochast.stats import analyze_scenario

from stochast_dashboard.charts import render_chart
from stochast_dashboard.jobs import JobAlreadyRunning, JobState

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI()
job = JobState(out_dir=Path("stochast-results"))


class StartRunRequest(BaseModel):
    path: str
    adapter: str
    keyword: str = ""
    runs: int | None = None
    concurrency: int = 5
    seed: int | None = None


class PreviewRequest(BaseModel):
    path: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# POST, not GET: this imports and executes arbitrary local Python as a side
# effect, so it must require a CORS preflight (a plain GET wouldn't) to stop
# any webpage the user has open from being able to trigger it cross-origin.
@app.post("/api/scenarios")
async def list_scenarios(body: PreviewRequest) -> list[dict[str, Any]]:
    try:
        return await job.preview(Path(body.path))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runs", status_code=202)
async def start_run(body: StartRunRequest) -> dict[str, str]:
    try:
        await job.start(
            Path(body.path),
            body.adapter,
            keyword=body.keyword,
            runs=body.runs,
            concurrency=body.concurrency,
            seed=body.seed,
        )
    except JobAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "started"}


# Serializes with default=str because tool results inside a record can be any
# object a user's tool handler returned, not just JSON-native values.
def _dumps(data: Any) -> str:
    return json.dumps(data, default=str)


def _format_sse(event: dict[str, Any]) -> str:
    return f"data: {_dumps(event)}\n\n"


def _completed_records(scenario: str) -> list[RunRecord]:
    try:
        records = job.records(scenario)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such scenario in the current run") from exc
    if not records:
        raise HTTPException(status_code=404, detail="no runs have completed yet")
    return records


# The read-only endpoints below are plain `def`, not `async def`, so FastAPI
# runs them in its threadpool: chart rendering would otherwise block the
# event loop that drives the live event stream.
@app.get("/api/runs/current/scenarios/{scenario}/stats")
def scenario_stats(scenario: str) -> Response:
    stats = analyze_scenario(_completed_records(scenario))
    return Response(_dumps(dataclasses.asdict(stats)), media_type="application/json")


@app.get("/api/runs/current/scenarios/{scenario}/runs/{run_index}")
def run_detail(scenario: str, run_index: int) -> Response:
    _completed_records(scenario)
    try:
        record = job.record(scenario, run_index)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="that run hasn't completed") from exc
    return Response(_dumps(dataclasses.asdict(record)), media_type="application/json")


@app.get("/api/runs/current/scenarios/{scenario}/charts/{kind}.svg")
def scenario_chart(scenario: str, kind: str) -> Response:
    records = _completed_records(scenario)
    try:
        svg = render_chart(kind, records)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown chart") from exc
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@app.get("/api/runs/current/events")
async def stream_events(request: Request) -> StreamingResponse:
    async def generator() -> AsyncIterator[str]:
        subscription = job.subscribe()
        try:
            async for event in subscription:
                if await request.is_disconnected():
                    break
                yield _format_sse(event)
        finally:
            await subscription.aclose()

    return StreamingResponse(generator(), media_type="text/event-stream")
