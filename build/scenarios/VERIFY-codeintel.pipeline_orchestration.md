# VERIFY sign-off — codeintel.pipeline_orchestration

Node: `codeintel.pipeline_orchestration` (region 01-CODE-INTELLIGENCE, disposition **verify**)
Integration point: `services/code_intel/src/code_intel/pipeline.py`
(`run_full_pipeline` / `Pipeline`) + `verifier.py` (`StaticAnalysisVerifier`);
live caller: `services/harness/src/control_plane/connect.py` (connect→index trigger).

## Definition of done — verified clause by clause

- **No Python module under `libs/code_intel`** — `ls libs/code_intel` → no such
  file/directory; `glob('libs/code_intel/**/*.py')` == `[]`
  (AC-CANON-001, `test_ac_canon_001_...`).
- **No Zoekt/ElasticSearch import or subprocess; `rg` the only search binary** —
  `StaticAnalysisVerifier.find_imports_of(zoekt/elasticsearch/…)` == `[]` and every
  text-search subprocess argv0 in `services/code_intel` is `rg`
  (AC-CANON-002, `test_ac_canon_002_...`).
- **All graph writes go to per-repo `*.db`, no psycopg2/asyncpg opened, no
  SHA-versioned table** — `run_full_pipeline` traced: every opened DB connection is
  `sqlite3` to a `.db` path; no `psycopg2`/`asyncpg` import in the graph layer;
  `find_sha_versioned_table_schema()` == `[]` (the `built_at_sha` *column* is allowed,
  a per-SHA *table name* is not) (AC-CANON-003, `test_ac_canon_003_...`).
- **connect→ready orchestration runs `run_full_pipeline`** — the twelve doc01
  workflows (W01–W12) drive the real connect→clone→scan→index→graph→server→readiness
  pipeline end-to-end (W01 reaches `ready`; W08 honest abstention; W09 tenant
  isolation; W10 webhook lifecycle + hard-delete).
- **connect→index trigger is the LIVE caller** — `create_app()` (module-level
  `app = create_app()`, re-exported as the ASGI app in `control_plane/__init__.py`)
  calls `install_connect_routes(app)`, mounting `POST /connect/install/start`
  → `_spawn_trigger` → `trigger_connect_index` → `code_intel.run_full_pipeline`.
  Reachable on the **real served product path**, not isolation-only.

## Hard rules

- `assert_registry_closed` green (`tests/doc08/test_m10_registry_canonical.py` 12 passed).
- Server-side tenant: `_tenant_for_install(repo_url)` derives the tenant from the repo
  binding; the client-supplied `install_id` is an opaque poll handle that authorizes
  nothing beyond reading its own readiness row.
- Never-throw: `trigger_connect_index` surfaces any failure as an honest `not_ready`
  (named gap), never a silent success; `_StoreReadinessListener.set_error` is a no-op parity.
- No internal names (Orchestrator/Scribe/Workroom) in pipeline.py or connect.py user-visible strings.
- ruff (line 120) + mypy --strict clean on all three integration-point modules.

## Evidence

```
$ bash build/setup-test-env.sh .venv/bin/python -m pytest \
    tests/test_canonical_contracts.py tests/doc01/test_w_workflows.py -p no:testmon -q
17 passed, 42 warnings in 7.87s      # exit 0

$ .venv/bin/ruff check --line-length 120 pipeline.py verifier.py connect.py
All checks passed!

$ .venv/bin/mypy --strict pipeline.py verifier.py connect.py
Success: no issues found in 3 source files
```

Verification only — no product code changed (the built reality already satisfies the DoD).
