# meeting_runtime deployable (GCE MIG — A1). One asyncio harness process per meeting;
# holds no volume; durability = the Postgres substrate.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /app
COPY . /app
RUN uv sync --frozen --no-dev --package harness --no-editable

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=build /app /app
CMD ["/app/.venv/bin/python", "-m", "harness.server"]
