"""FastAPI surface for the control room.

Two-step run design, because `EventSource` can only issue GET requests:

    POST /api/runs              -> {run_id}
    GET  /api/runs/{id}/events  -> SSE stream

Media is served here rather than from object storage so the demo has no signed
URL, CORS or range-request setup to get wrong. Every artefact is written with
`-movflags +faststart`, so the browser can seek and progressively play.
"""

from __future__ import annotations

import json
import os
import queue
from collections.abc import Iterator
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .orchestrator import FIXTURES, Orchestrator, Status

ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = ROOT / "media"
OUT_DIR = ROOT / "out"

app = FastAPI(title="Broadcast QC Incident Commander")

# The UI dev server runs on a different port; in production both are served
# from the same origin and this is a no-op.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

orchestrator = Orchestrator(
    grafana_url=os.environ.get("GRAFANA_URL", "http://localhost:3000"),
    out_dir=str(OUT_DIR),
)


class StartRequest(BaseModel):
    fixture: str = "fault"
    reasoner: str = "scripted"


class ApprovalRequest(BaseModel):
    approved: bool


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "fixtures": sorted(FIXTURES)}


@app.get("/api/profile")
def profile() -> dict:
    """The delivery spec being applied, so the UI can show what is being enforced."""
    target, tolerance = orchestrator.profile.loudness_target
    return {
        "id": orchestrator.profile.id,
        "version": orchestrator.profile.version,
        "target_lufs": target,
        "tolerance_lu": tolerance,
        "true_peak_ceiling": orchestrator.profile.true_peak_ceiling,
        "max_contiguous_body_black_s": orchestrator.profile.max_contiguous_body_black,
        "allowlist": sorted(orchestrator.allowlist),
    }


@app.post("/api/runs")
def start_run(body: StartRequest) -> dict:
    try:
        run = orchestrator.start(body.fixture, reasoner=body.reasoner)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": run.run_id, "fixture": run.fixture}


@app.get("/api/runs/{run_id}/events")
def stream(run_id: str) -> StreamingResponse:
    run = orchestrator.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")

    def gen() -> Iterator[str]:
        while True:
            try:
                event = run.events.get(timeout=15)
            except queue.Empty:
                # Comment frame: keeps proxies and Cloud Run from closing an
                # idle stream, and is ignored by EventSource.
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event["kind"] == "end":
                return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/runs/{run_id}/approval")
def approve(run_id: str, body: ApprovalRequest) -> dict:
    if not orchestrator.approve(run_id, approved=body.approved):
        raise HTTPException(status_code=409, detail="run is not awaiting approval")
    return {"ok": True, "approved": body.approved}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = orchestrator.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return {
        "run_id": run.run_id,
        "fixture": run.fixture,
        "status": str(run.status),
        "pipeline_run_id": run.pipeline_run_id,
        "proposal": run.proposal,
        "error": run.error,
    }


@app.get("/api/media/{name}")
def media(name: str) -> FileResponse:
    """Serve a fixture or a produced artefact. Path traversal is refused."""
    safe = Path(name).name
    for directory in (MEDIA_DIR, OUT_DIR):
        candidate = directory / safe
        if candidate.is_file():
            return FileResponse(candidate, media_type="video/mp4")
    raise HTTPException(status_code=404, detail="no such media")


__all__ = ["app", "orchestrator", "Status"]
