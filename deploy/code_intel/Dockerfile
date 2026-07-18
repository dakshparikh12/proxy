# code_intel deployable (stateful GCE host, per-tenant encrypted volume).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /app
COPY . /app
RUN uv sync --frozen --no-dev --package code_intel --no-editable

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=build /app /app
CMD ["/app/.venv/bin/python", "-m", "code_intel.server"]
