# control_plane deployable (Cloud Run). Built from the harness package, which
# hosts the control_plane assembly (webhooks, connect page, API, WS gateway, auth).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /app
COPY . /app
# Self-contained, never editable (AC-REPO-005): resolve just this deployable's package.
RUN uv sync --frozen --no-dev --package harness --no-editable

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=build /app /app
CMD ["/app/.venv/bin/python", "-m", "control_plane.server"]
