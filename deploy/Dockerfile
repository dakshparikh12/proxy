# syntax=docker/dockerfile:1
# Proxy service image -- Doc 00 §9. Multi-stage uv build: a builder stage that
# installs a single, self-contained service package (frozen, no-dev), and a
# slim non-root runtime stage that migrates-then-serves on boot.

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY . /app
# Per-service, self-contained: --package pins the single service package;
# --frozen forbids lockfile drift; --no-dev keeps dev deps out of prod.
RUN uv sync --frozen --no-dev --no-editable --package harness

FROM python:3.12-slim
WORKDIR /app

# release <-> sandbox-image provenance coordinate (fail-closed at promote/boot).
ARG SANDBOX_IMAGE_HASH
LABEL proxy.sandbox-image-hash=$SANDBOX_IMAGE_HASH

# Bring the built virtualenv + source across from the builder stage.
COPY --from=builder /app /app

# Non-root runtime user. HOME REQUIRED -- the Claude Agent SDK writes to $HOME.
RUN useradd -m -u 1001 appuser
ENV PORT=8080 HOME=/home/appuser
ENV PATH="/app/.venv/bin:$PATH"
USER appuser

# Parallel-boot safe: losers retry `alembic upgrade head` (bounded 30x5s ~= 150s,
# inside the startup-probe deadline) while the winner migrates under an advisory
# lock (see migrations/env.py); then every boot exec's the server.
CMD ["sh","-c","n=0; until alembic upgrade head; do n=$((n+1)); [ $n -ge 30 ] && exit 1; sleep 5; done && exec python -m harness.server"]
