# syntax=docker/dockerfile:1
# Proxy v0 — THE production image (the ONE deployable: the control-plane service).
#
# It serves the reactive-workroom control plane: the Recall/GitHub webhook
# receiver + orchestrator, the connect page + API, the authenticated /m surface,
# the Output-Media page, the WS gateway, and the sandbox→host relay
# (POST /meetings/{id}/relay). Per-meeting work runs in E2B sandboxes (an external
# provider), so this host is stateless — truth lives in Postgres + GCS.
#
# Multi-stage uv build: the builder resolves ONLY the control-plane package from
# the frozen workspace lock (its closure pulls in the in-meeting, workroom, and
# premeeting members + the shared libs); the runtime stage is slim, non-root, and
# migrates-then-serves on boot.
#
# Build (from repo root):
#   docker build -f deploy/control-plane/Dockerfile -t proxy-control-plane .
# Deploy env contract: deploy/README.md + the Terraform module in terraform/.

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY . /app
# Self-contained, never editable: resolve exactly this deployable's package from
# the shared workspace lock. --frozen forbids lock drift; --no-dev keeps dev-only
# dependencies out of the production image.
RUN uv sync --frozen --no-dev --no-editable --package control-plane

FROM python:3.12-slim
WORKDIR /app

# Bring the built virtualenv + source across from the builder stage. The source
# tree stays in the image because the libs.* dotted facades resolve from the repo
# root at runtime (WORKDIR /app is on sys.path via `python -m`).
COPY --from=builder /app /app

# Non-root runtime user. HOME REQUIRED — the Claude Agent SDK writes to $HOME.
RUN useradd -m -u 1001 appuser
ENV PORT=8080 HOME=/home/appuser
ENV PATH="/app/.venv/bin:$PATH"
USER appuser

# Container-level liveness: GET /health on the bound port. Cloud Run uses its own
# probes and ignores this; it serves plain docker / VM (self-host) deployments.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health',timeout=4)" || exit 1

# Parallel-boot safe: losers retry `alembic upgrade head` (bounded 30x5s ~= 150s,
# inside the startup-probe deadline) while the winner migrates under a Postgres
# advisory lock (see migrations/env.py); then every boot exec's the uvicorn server
# (control_plane.server.main → uvicorn on 0.0.0.0:$PORT).
CMD ["sh","-c","n=0; until alembic upgrade head; do n=$((n+1)); [ $n -ge 30 ] && exit 1; sleep 5; done && exec python -m control_plane.server"]
