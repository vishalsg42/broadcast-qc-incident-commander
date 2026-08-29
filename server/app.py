"""FastAPI surface for the control room.

Two-step run design, because `EventSource` can only issue GET requests:

    POST /api/runs              -> {run_id}
    GET  /api/runs/{id}/events  -> SSE stream

Media is served here rather than from object storage so the demo has no signed
URL, CORS or range-request setup to get wrong. Every artefact is written with
`-movflags +faststart`, so the browser can seek and progressively play.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import secrets
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline.policy import available_profiles

from .orchestrator import FIXTURES, Orchestrator, Status

ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = ROOT / "media"
# Cloud Run mounts a read-only filesystem apart from /tmp, so where pipeline
# artefacts land has to be configurable. /tmp there is tmpfs and counts against
# the instance memory, which is why fixtures are kept to 45 seconds.
OUT_DIR = Path(os.environ.get("QCIC_OUT_DIR", str(ROOT / "out")))

# Load .env at import so the server behaves identically however it is launched.
# Without GOOGLE_GENAI_USE_VERTEXAI=TRUE, google-genai silently falls back to the
# Gemini Developer API and asks for an API key - an error that looks nothing like
# the missing-ADC problem it actually is.
load_dotenv(ROOT / ".env")
os.environ.setdefault("CLOUDSDK_CONFIG", str(ROOT / ".gcloud"))

UI_DIR = ROOT / "ui" / "dist"

# A hosted demo URL that starts runs is a spend vector: every run costs ffmpeg
# CPU and Vertex tokens. DEMO_TOKEN gates anything that starts work; reading is
# open so the page still loads for anyone.
DEMO_TOKEN = os.environ.get("DEMO_TOKEN") or None

# Upper bound on one SSE connection. Longer than the slowest legitimate run
# (Grafana Cloud ingestion can take 5 minutes) and short enough that abandoned
# connections cannot accumulate.
STREAM_MAX_SECONDS = float(os.environ.get("QCIC_STREAM_MAX_SECONDS", "900"))

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


def require_token(
    token: str | None = Query(default=None),
    x_demo_token: str | None = Header(default=None),
) -> None:
    """Gate anything that starts work. No-op when DEMO_TOKEN is unset (local)."""
    if DEMO_TOKEN is None:
        return
    supplied = token or x_demo_token or ""
    # Constant-time: a timing oracle on a demo token is unlikely to matter, but
    # comparing secrets with == is the kind of thing reviewers rightly flag.
    if not secrets.compare_digest(supplied, DEMO_TOKEN):
        raise HTTPException(status_code=403, detail="a demo token is required to start a run")


class StartRequest(BaseModel):
    fixture: str = "fault"
    reasoner: str = "scripted"
    profile_id: str | None = None


class ApprovalRequest(BaseModel):
    approved: bool


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "fixtures": sorted(FIXTURES),
        "token_required": DEMO_TOKEN is not None,
    }


@app.on_event("startup")
def warm_up() -> None:
    """Run one throwaway delivery in the background on boot.

    Grafana Cloud's first write to a new Loki stream takes 90s+ because the
    stream must be created; later writes land in well under a second. Judging
    happens weeks after recording, so without this a visitor's first run stalls
    on a cold ingestion path. Backgrounded so readiness is not delayed.
    """
    if os.environ.get("QCIC_WARMUP", "1") != "1":
        return

    def _warm() -> None:
        # A failed warm-up must never stop the service.
        with contextlib.suppress(Exception):
            orchestrator.start("clean", reasoner="scripted")

    threading.Thread(target=_warm, daemon=True).start()


def _profile_payload(profile) -> dict:
    target, tolerance = profile.loudness_target
    return {
        "id": profile.id,
        "name": profile.name,
        "standard": profile.standard,
        # Plain-English summary. A reader who does not know what LKFS means
        # still has to be able to tell what this profile enforces.
        "plain": profile.plain,
        "version": profile.version,
        "target_lufs": target,
        "tolerance_lu": tolerance,
        "true_peak_ceiling": profile.true_peak_ceiling,
        "max_contiguous_body_black_s": profile.max_contiguous_body_black,
        # A profile that demands a measurement this probe cannot make is
        # adjudicable by nobody here. Saying so is the point, not an omission.
        "measurable": profile.is_measurable,
        "requires": profile.required_measurement,
        # The allowlist is a list of action objects; the UI only needs the names
        # of the actions the agent is permitted to propose under this profile.
        "allowlist": sorted(a["id"] for a in profile.remediation_allowlist),
    }


@app.get("/api/profile")
def profile() -> dict:
    """Every delivery spec on offer, so the operator picks what is enforced."""
    profiles = available_profiles()
    return {
        "default": orchestrator.profile.id,
        "profiles": [_profile_payload(p) for p in profiles],
    }


@app.post("/api/runs", dependencies=[Depends(require_token)])
def start_run(body: StartRequest) -> dict:
    try:
        run = orchestrator.start(
            body.fixture, reasoner=body.reasoner, profile_id=body.profile_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"run_id": run.run_id, "fixture": run.fixture, "profile_id": run.profile_id}


@app.get("/api/runs/{run_id}/events")
def stream(run_id: str) -> StreamingResponse:
    run = orchestrator.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")

    def gen() -> Iterator[str]:
        # A hard lifetime. Without one, an abandoned connection holds a
        # threadpool thread forever: FastAPI runs sync endpoints in a bounded
        # pool, so a few closed tabs exhaust it and the whole service stops
        # answering. Observed locally after a handful of interrupted runs.
        deadline = time.monotonic() + STREAM_MAX_SECONDS
        while time.monotonic() < deadline:
            try:
                event = run.events.get(timeout=15)
            except queue.Empty:
                # Comment frame: keeps proxies and Cloud Run from closing an
                # idle stream, and is ignored by EventSource. A failed write
                # here is how we learn the client has gone.
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event["kind"] == "end":
                return
        yield 'data: {"kind": "end", "status": "stream_timeout"}\n\n'

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/runs/{run_id}/approval", dependencies=[Depends(require_token)])
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
        "profile_id": run.profile_id,
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


# Mounted last so every /api route is matched first. html=True serves
# index.html for unknown paths, which a single-page app needs.
if UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")


__all__ = ["app", "orchestrator", "Status"]
