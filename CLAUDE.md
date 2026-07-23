# CLAUDE.md — Proxy build-loop constitution (§14)

Versioned, PR-reviewed, and lean. Read alongside `AGENTS.md` (the shared method +
laws). This is the **build-loop** constitution: the stack, the commands, the five
standing laws as build constraints, and the hard rules — where **every rule names
its enforcement mechanism** (their most effective CLAUDE.md trait).

## What Proxy is
Proxy is an AI participant that joins a company's meetings already knowing their
codebase. The **product and the agent are both named Proxy** — a user-visible
string never carries an internal component name.

## Stack (one-liner)
Python 3.12 · **uv workspace** monorepo (root `pyproject.toml`, members
`services/*` + `libs/*`, one shared `uv.lock`) · **src-layout** packages
(`libs/<x>/src/<x>`, `services/<x>/src/<x>`) · ONE Cloud SQL **Postgres** + **GCS**
(object-versioned) durable substrate · Alembic migrations (Postgres only).

Source of truth vs cache: the clones, structural indexes, and dependency-graph are
a **rebuildable, derived cache** — the durable **source of truth is Postgres + GCS**.
Drop and rebuild the derived cache freely; never treat it as the record of truth.

## Commands
- `uv sync --all-packages` — install/refresh the whole workspace. **Never bare `uv sync`** (it prunes members' deps + the pinned tools); always follow with `uv pip install --python .venv/bin/python -r tools/linux-verify-requirements.txt`.
- `uv run --package <name> pytest` — run one workspace member's tests/tools.
- **`/forge <spec>`** — the packaged build loop (the `forge` plugin): understand → specify → plan → build → verify → DONE, for any spec. Accepts a doc-id (`/forge 00`) or a spec path (`/forge product/v0-spec/04-*.md`). **VERIFY mode** for already-built docs (00–03: confirm + fix, never rebuild); **BUILD mode** for new specs. This is the primary entry; `drive.sh`/`done-check.sh` below are the underlying engine forge orchestrates.
- `./drive.sh <id>` — run the full v2 loop for spec `<id>` (e.g. `00`, `03`).
- `./done-check.sh --spec <id>` — compute the DONE predicate (exit 0 = DONE). (forge ships its own 5-conjunct `done-check` in `forge/gates/`.)
- `alembic upgrade head` — apply the Postgres migrations to head.

## The build loop (doc-agnostic — every doc runs the SAME pipeline)
- Work **ONE** task from `slices/<id>/tasks.json` at a time; `/clear` between tasks.
- TDD: write the failing acceptance test → code to green → **never edit tests, cassettes, acceptance bundles, goldens, fixtures, or `_baseline.json`** (a lean PreToolUse guard enforces this).
- A task is done ONLY after its real path RAN on real/held-out data and the output was shown as evidence; flip `passes:true` in `tasks.json` only then.
- `_baseline.json` changes and the `EXTRACTION_COUNT_HALT` are **human-gated** (founder review; never auto-approve).
- A task that fails N identical times flags `BLOCKED:<reason>` and continues — it never deadlocks and never silently claims done.
- Requirements + per-requirement acceptance checks are already distilled in `acceptance/doc<NN>/` (read those, not spec prose); `product/v0-spec/CANONICAL-DECISIONS.md` overrides; integration journeys = Doc 09 §2/§3.

## The five standing laws (build constraints; every visible behavior traces to one)
1. **Grounded or silent** — cite `file:line` from the current clone, or say "not found by this method".
2. **Never overstate** — exact results tagged `resolved`, search-derived tagged `lower-bound`, failures spoken plainly.
3. **Human control is absolute** — barge-in stops speech; every world-touching action is a staged draft behind a human click.
4. **Dynamic, never hard-coded** — situation-to-action mappings live in model judgment; code owns only physics, pipes, and the substrate.
5. **Talk-and-glance** — operable entirely by speaking and glancing; nothing to install mid-meeting.

Path-scoped conventions (per-tree overrides) live in `.claude/rules/*`.

## Hard rules (each rule names the guard that enforces it)
- **Naming** — user-visible strings never contain internal names (Orchestrator/Scribe/workroom): enforced by the naming `lint` (`libs.lint.naming`); the product and the agent are Proxy.
- **Secrets** — secrets only from Secret Manager, never hard-coded or logged: enforced by `check-secret-bindings` at startup.
- **Contracts** — a message type produced but not in the contracts registry fails the build: enforced by `assert_registry_closed`.
- **Isolation** — the isolation triad (per-tenant volume + process + index, one tenant never sharing with another): enforced by a runtime tripwire on any cross-tenant read.
- **External calls** — every external call wrapped with retry + cost telemetry: enforced by the single `call_external` seam in `libs/http` (no raw client lives anywhere else).
- **Tool handlers** — every tool handler returns errors, never throws: enforced by the never-throw boundary in `libs.agentkit.tools`.

## Definition of Done
All bundle criteria green (rung 1 every pass, rung 2 per section) on every estate ·
adversarial clean · ruff + mypy `--strict` + bandit clean · no law/invariant-violating
path · evidence committed. **Done means the product is proven on real data — not that
the code compiles.**
