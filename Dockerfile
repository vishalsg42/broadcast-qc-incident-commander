# Broadcast QC Incident Commander - single container.
#
# One image, one origin: FastAPI serves both the API and the statically exported
# UI. That removes the second service, the reverse proxy, CORS preflight, and
# cross-origin EventSource - four things that each fail differently on Cloud Run.
#
# ffmpeg is installed as a system package. It is the measurement engine, not a
# Python dependency, and `pip install ffmpeg` installs a wrapper around a binary
# that would not be here.

# ---- stage 1: build the UI ---------------------------------------------------
FROM node:24-slim AS ui

WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY ui/ ./
# `output: 'export'` - every page is a client component, so there is nothing to
# render server-side and no Node runtime is needed at all in the final image.
RUN npm run build


# ---- stage 2: the Grafana MCP server -----------------------------------------
# The agent's observability toolbox is the OFFICIAL Grafana MCP server, not a
# client we wrote. Fetched at a pinned version and checksum-verified, because a
# binary that runs with our Grafana credentials is not something to take on
# trust from a redirect.
FROM debian:bookworm-slim AS mcp
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /fetch
COPY scripts/fetch_mcp_grafana.sh ./
RUN chmod +x fetch_mcp_grafana.sh && ./fetch_mcp_grafana.sh /out


# ---- stage 3: runtime --------------------------------------------------------
FROM python:3.12-slim

# ffmpeg for measurement (ebur128, blackdetect) and for the repair re-encode.
# tini because the CMD below execs uvicorn as PID 1, and PID 1 is what
# orphaned processes re-parent to. uvicorn never calls wait(), so a
# subprocess that outlives its parent becomes an unreapable zombie holding
# its memory on an instance that never restarts.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so a code change does not invalidate the layer.
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt

COPY pipeline/ ./pipeline/
COPY agent/ ./agent/
COPY server/ ./server/
COPY --from=ui /ui/dist ./ui/dist
# On PATH, where agent/grafana_mcp.py looks for it first.
COPY --from=mcp /out/mcp-grafana /usr/local/bin/mcp-grafana

# Generate the fixtures rather than shipping them.
#
# They are reproducible by construction, so building them here keeps ~36MB of
# media out of every build context and makes it impossible for the image to be
# missing them. `gcloud run deploy --source .` falls back to .gitignore when no
# .gcloudignore exists, and .gitignore excludes media/*.mp4 - so shipping them
# silently produced a container with no media at all.
COPY scripts/make_fixtures.sh ./scripts/
RUN chmod +x scripts/make_fixtures.sh && ./scripts/make_fixtures.sh && ls -la media/

# Cloud Run's filesystem is read-only apart from /tmp, and /tmp is tmpfs that
# counts against the instance's memory. Pipeline outputs go there deliberately:
# they are intermediates, and every repair writes a NEW artefact rather than
# overwriting, so the directory only has to survive one run.
ENV QCIC_OUT_DIR=/tmp/out \
    PYTHONUNBUFFERED=1 \
    PORT=8080

EXPOSE 8080

# Single worker on purpose. Run state and the approval handshake live in process
# memory, so a second worker would not see a run started by the first. Scale is
# not the point of a demo; correctness is.
# JSON form so the shell is explicit and `exec` replaces it - uvicorn then
# receives SIGTERM directly, which is how Cloud Run asks for a graceful stop.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "exec uvicorn server.app:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 75"]
