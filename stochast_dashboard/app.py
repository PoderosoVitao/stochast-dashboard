from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/scenarios")
async def list_scenarios(path: str) -> list[dict[str, Any]]:
    try:
        return await job.preview(Path(path))
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


def _format_sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


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
