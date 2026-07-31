# Proxy

Proxy is an AI teammate that joins your meeting already knowing your codebase and does the
work live. There are two places: the **meeting** (what people see) and the **workroom** — a
per-meeting E2B sandbox running native Claude with your repo, the live transcript, and one
connection to the room. When someone addresses Proxy, it reasons, does the real work in the
sandbox, verifies, and responds — speaking, dropping a note in chat, showing a screen, or
offering a staged draft — choosing how itself, live.

**Read `SPEC.md` first** — it is the product source of truth. `CLAUDE.md` + `AGENTS.md` are
the build constitution + shared method.

## How it works (one live path)
- **Pre-meeting** (`services/premeeting`, once per repo): connect the repo (read-only GitHub
  App) → clone → build a repo map → store it (Postgres `repo_maps` + GCS; rebuilt on a signed
  push).
- **Meeting** (`services/{in-meeting, control-plane, workroom}`): warm a per-meeting E2B
  sandbox (repo + map + who's in the room + the prime + an empty `MEETING_NOTES.md`) → feed
  the transcript in as fast as it's produced → wake native Claude when Proxy is addressed →
  it responds through the one `to_meeting` connection, carried host-side over Recall /
  Cartesia. Credentials never enter the sandbox.

## Stack
Python 3.12 · **uv workspace** monorepo (members `services/*` + `libs/*`, one shared
`uv.lock`, src-layout) · Cloud SQL **Postgres** + **GCS** (object-versioned) durable
substrate · **E2B** sandboxes · Recall + AssemblyAI + Cartesia · Alembic migrations · one
**Cloud Run** service (`control_plane`).

## Develop
```bash
uv sync --all-packages                      # install/refresh the whole workspace
                                            # (NEVER bare `uv sync` — it prunes members)
uv pip install --python .venv/bin/python -r tools/linux-verify-requirements.txt

uv run --package <name> pytest              # run one member's tests
alembic upgrade head                        # apply Postgres migrations
bash build/gates/signoff.sh                 # whole-product static + unit gate
                                            # (ruff · mypy --strict · bandit · offline pytest)
```
The real-meeting proof runs separately on live infra (E2B + Anthropic + Recall/Cartesia).

## Deploy
One Cloud Run service is the whole hosted estate. Full production runbook (env contract, GCP
estate, Recall dashboard setup, verification): **`deploy/v0/RUN.md`**. Terraform lives in
`infra/`; Cloud Build + Dockerfiles in `deploy/`.
