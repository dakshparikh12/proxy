# Pre-Meeting System — Build Plan

Reads alongside `DESIGN.md` (the locked design) and `ACCEPTANCE.md` (the testable criteria).
Goal: ONE consolidated, production-ready pre-meeting system that produces a durable `index.md`
repo map and serves the live-search toolbelt — helping EVERYTHING downstream: in-meeting Q&A,
the Scribe's grounding, AND the Workroom's code tasks.

## What "downstream" means (design it for all three consumers)
1. **In-meeting wake turn** — loads `index.md` as a cached prompt prefix (orientation) + mounts the
   live toolbelt (grep/read/glob/batch_read) for grounded citations (Law 1).
2. **Workroom** — the code-task agent gets the SAME map as orientation (skips re-exploration →
   fewer round-trips before it starts coding) + the same live toolbelt on the clone.
3. **Scribe referent lookup** — reads `graph_nodes` today; LEAVE that seam intact this pass
   (the one deferred coupling; the in-meeting-reading rework retires it).

## The coupling reality (verified — this drives the scope)
`code_intel` today does TWO jobs; only one is being replaced:
- **BUILD the graph** (REPLACE): `graph`, `graph_builder`, `graph_store`, `graph_gc`, `coverage`,
  `langs`, `orm`, `warm_resolver`, `verifier`, `repo_provider` (dead Nango), `meeting` (per-SHA).
- **SERVE the toolbelt** (REORIENT, don't delete): `sdk_server` / `mcp_server` / `direct` expose
  `mcp__code_intel__*` tools that the wake turn (`harness/code_intel_mount.py`, `wake.py`) and the
  Workroom (`workroom/session.py:_resolve_code_intel_server`) MOUNT. Today they resolve a context
  from `graph.db`. New: resolve from **clone + index.md**, serve live-search tools off the clone.

## Build order (strict dependency order; build green after each)
Module-by-module TDD in `services/premeeting/src/premeeting/`:
1. `paths.py`      — per-tenant volume rooting (port the lean bits of `code_intel/paths.py`).
2. `exclusions.py` — secret-path filtering + value redaction (port `code_intel/exclusions.py`).
3. `github_auth.py`— direct installation-token mint (JWT → `POST /app/installations/<id>/access_tokens`)
   THROUGH `libs/http.call_external`. Never cached, never logged.
4. `cloner.py`     — clone + delta-pull to the tenant disk; token→URL, argv REDACTED; bare+blobless+
   read-only; runs `exclusions.scan_after_clone`. (Port `code_intel/cloner.py`, add token threading.)
5. `map_store.py`  — durable Postgres `repo_maps` (+ Alembic migration in `./migrations/versions`);
   `save_map()` / `load_map(tenant, repo, sha)` — the clean downstream API. Tenant-isolated.
6. `map_build.py`  — the bounded ONE-agent Claude loop → `index.md`. Context discipline: directory
   skeleton (bounded depth) + high-yield files (BATCH read) + on-demand samples; NEVER the full file
   list. Read-only "quick" disposition via the ClaudeAgentProvider seam. Graceful degrade on huge repo.
7. `verify.py`     — deterministic: non-empty + has sections + every named path EXISTS in the clone
   (no hallucination) + top-level tracked dirs covered + no secret leaked. `ready` only on pass.
8. `readiness.py`  — the ready signal (clone + map + verify) for the connect-status poll.
9. `refresh.py`    — push-webhook rebuild: delta-pull → map_build → save → verify.
10. `repo_context.py` — resolve a `RepoContext` from (clone + loaded map); serve the live toolbelt
    (grep/read/glob/batch_read) off the clone. This REPLACES graph.db as the tool-serving substrate.
11. `pipeline.py`  — thin orchestrator: clone → map_build → save → verify → ready; register refresh.

## Wiring (additive, keep build green)
- `control_plane/connect.py::trigger_connect_index` → drive `premeeting.pipeline` (produce+store+verify
  the map) instead of the graph `run_full_pipeline`.
- `harness/code_intel_mount.py` + `wake.py` → resolve `RepoContext` from map+clone; mount map-as-prefix
  + live toolbelt.
- `workroom/session.py::_resolve_code_intel_server` → resolve the SAME `RepoContext`; hand the map as
  orientation to the code-task agent.
- `github_webhook.py` → route push to `premeeting.refresh` (keep HMAC gate).

## Deletion (FINAL phase — gated on build-green; conservative)
After the reorientation frees them AND `signoff.sh` is green, delete the graph-BUILD modules only.
If deleting a module breaks the tree and can't be cleanly resolved, DO NOT half-delete — flag
`BLOCKED:<module>` and leave the tree green. NEVER touch `scribe/referent.py`'s `graph_nodes` seam.

## The one honest gate (state it, never fake it)
The Anthropic key is out of credits (D-032). `map_build`'s plumbing (prompt assembly, tool
restriction, budget, bounded-read discipline, output capture, verify) is tested on the REAL path with
a FAKE provider returning a canned `index.md`. The real-model map-QUALITY battery (does Claude produce
a good map on real repos, scored by deepeval) is `BLOCKED-on-credits` — report it as such, never as passed.

## Definition of done for this build
All `ACCEPTANCE.md` criteria green on the real path (fake only at the model seam) · migration authored
(NOT applied to prod — human-gated) · `bash build/gates/signoff.sh` green (ruff + mypy --strict + bandit
+ naming lint + contracts registry) · downstream wired (wake + workroom both get the map) · deletion
done or explicitly staged with reasons · evidence shown (real test output, not assertions).
