# PROGRESS

## doc00 plan

*Planner (fresh context). Spec: `product/v0-spec/00-FOUNDATION.md` + `CANONICAL-DECISIONS.md`.
Sealed arbiter: `acceptance/doc00/` (read-only) — **the builder may not edit `acceptance/`, `tests/`,
`fixtures/`, or `harness/`.** Authored per `orchestrator/skills/writing-plans.md`. Reviewed by the
`planner-reviewer` subagent; its changes folded in below (see "Review deltas").*

### 0 · The spine is locked by the harness (not a planner choice)

`harness/verify.sh` is the sole code arbiter: `ruff` → `mypy --strict` → `bandit` (over `services libs src`)
→ `pytest -q -x --maxfail=1`. Pytest collects `tests/doc00/test_m00_cmp.py … test_m15_ten.py`, then
`test_w_workflows.py`, **in filename order**, and `-x` stops at the first red. **Therefore the milestone
order below is mandated, not discretionary** — criteria can only go green in this sequence, satisfying
writing-plans rule #1 ("the sequence in which criteria go green, matching the pre-authored test-file order").
Each milestone = exactly one test file; its exit gate is that file green with every earlier file still green.

**Coverage (RTM re-derived locally):** 154 criteria, **154/154 referenced by a test file, 0 dangling, 0
uncovered.** Two criteria are cross-asserted in a second file (built once, re-proven later): `AC-OBS-007`
(green at **M4**, re-asserted M12) and `AC-HOST-005` (green at **M3**, re-asserted M9). `test_w_workflows.py`
(M17) introduces **no new criterion** — it re-exercises 38 as end-to-end chains. **0 `SPEC_BLOCKED`** (manifest:
0 spec-blocked, 0 unresolved contradictions; A-006/A-007/A-009 are *resolved* material contradictions, encoded
as build rules in §4).

### 1 · The seams — frozen contract homes (build against these; never redefine — AGENTS.md §"Contract homes")

The suite imports product code through two namespaces (`libs.<lib>`, `services.<svc>.<mod>`); these are the
homes every later milestone consumes:

- **`libs/contracts`** (M1): `Bundle{ask,speaker,timestamp,notes_ref:UUID,transcript_tail,task_id}` ·
  `Envelope{headline,detail,artifact,receipts,status,verification,draft_id,task_id}` + `EnvelopeStatus`
  Literal (`done|partial|failed|needs_clarification|needs_review`; **not** `verified/draft`) ·
  `AgentChunk` + `ChunkType` union (`INIT|TEXT|TOOL_USE|TOOL_RESULT|RESULT|ERROR`, discriminator **`type`**,
  per-variant metadata; `RESULT.total_cost_usd`) · `NoteOp` (`add|patch|close`) · `Readiness`
  (`connecting|cloning|indexing|ready|not_ready` + `coverage_pct`,`gaps`) · `channel-report.dm_available:bool`
  · progress-event variant (Envelope fields minus finalized status, A-011) · `ProxyMessage` registry +
  `assert_registry_closed()`.
- **`libs/agentkit`** (M1 seam, filled M11): provider seam · `stream_deltas` (**defined once, called once
  inside `BehaviorRunner.run()`**, C2) · `AbortRegistry`, `resume_with_fallback` (single definition only —
  arity is Doc 04/05, A-010) · `BehaviorRunner`/`BehaviorConfig`/`register` (typed Python constants, **no YAML**).
- **`libs/http`** (M1 seam): the one `dispatch()` funnel (single definition).
- **`libs/llm`** (M6): metered gateway; `routing.py` 8-seat table (real model ids); `PROXY_MAX_INFLIGHT_LLM`.
- **`libs/db`** (built-ahead at M4, tested M10): asyncpg pool · `Database.from_connection` facade + `repos`
  namespace (`sessions,repos,meetings,tenants,operations,cost`) — **no ORM** · Alembic.
- **`libs/ops`** (M4): `with_operation_run` · `claim_meeting` · `sweep_stale_on_read` · `sandbox_provider`
  (`provision/destroy/health_check`) · `run_reconcile_sweep`.
- **Concrete API the workflows pin** (must exist by M17): `services.harness.{emit.Emitter, wake.answer_direct,
  budget.check_meeting_budget, orchestrator.run_wake_turn, server.lifespan_trace, settings}` ·
  `services.workroom.{recovery.should_restart, drafts.propose_change/teardown_review_session}` ·
  `services.control_plane.{webhooks.ingest/drain_pending, accept.accept_draft, authz.read_meeting}`.

### 2 · The risky-20% register (built + adversarially self-tested FIRST within each milestone)

The harness fixes the *milestone* order, so "risky-first" (writing-plans rule #5) is applied as: **design all
four clusters up-front (here), then within each owning milestone build its P0 boundary criteria before any
P1/P2 convenience, and self-attack each before moving on.** 24 P0 criteria cluster into four risks:

| # | Risk cluster | Owning milestone | P0 boundary criteria | The exact boundary that must not slip |
|---|---|---|---|---|
| R1 | **Import-namespace seam** | M1→M2 | (enables all) | `import libs.contracts` **and** `import services.control_plane.webhooks` resolve **while** `services/`=5 dirs (AC-REPO-006) and every member is `src/<pkg>/` (AC-REPO-002). See §3. |
| R2 | **Concurrency substrate** | M4 | AC-SUB-002,003,007,008,009,011,012,035 | one running row per (scope,type); interrupted/completed never blocks re-claim; fencing rowcount-0 ⇒ `is_owner=False` ⇒ **zero** emits on the wire (speak/send_chat/show_screen/apply/dispatch). |
| R3 | **Crypto-shred isolation** | M3 | AC-HOST-013,014 (+ AC-INV-009) | distinct per-tenant envelope key; destroying A's key leaves A unrecoverable **and** B fully readable; KMS PD floor; no LUKS; per-sandbox random JWT. |
| R4 | **Lethal-trifecta + tenant isolation** | M14→M16 | AC-INV-004,005,006,008,011; AC-TEN-001,002,003 | no transcript-triggered path reaches an outward side-effect without a human click; transcript fenced as untrusted data; world-touching tools in `disallowed_tools`; secrets redacted; accept requires authenticated tenant member (CSRF+idempotent+audit); cross-tenant read refused, zero rows leak; Nango tokens per-operation, never cached/logged. |

M17 (workflows) is the integration proof that R1–R4 hold **together** (W02/W03 concurrency, W07 draft-survives-teardown, W08 trifecta, W09 cross-tenant).

### 3 · The #1 structural risk — the import-namespace seam (resolve in M1 before any contract code)

The suite (conftest puts **repo-root** on `sys.path`) imports product as `libs.<lib>` and
`services.<svc>.<mod>`, and workflows import `services.control_plane.webhooks` / `services.control_plane.accept`
/ `services.control_plane.authz`. Simultaneously: **AC-REPO-002** requires `services/<svc>/src/<svc>/`
(bare-name src-layout), **AC-REPO-006** requires `set(services/*) == {harness,code_intel,transport,scribe,
workroom}` exactly (so **no `services/control_plane/` dir**), and **AC-CMP-001** discovers `control_plane`/
`meeting_runtime`/`code_intel` as **deploy-config strings** in `infra/`+`deploy/`, not as service dirs.

**These are jointly satisfiable but not naïvely** — the builder must choose a package build-config (hatch/uv)
so that: (a) `import libs.contracts` / `import services.harness.emit` resolve to the dotted namespace; (b) the
`control_plane`/`meeting_runtime` *deployable-assembly* code (webhooks, accept, authz, the boot server) lives
**inside the five allowed service packages** (orchestrator/webhook code is `harness`-hosted) yet is **exposed at
the `services.control_plane.*` import path via package configuration**, never as a sixth `services/` directory;
(c) each member still presents `src/<pkg>/` for the static check and one root `uv.lock` (AC-REPO-001/005).
**M1 exit gate includes a walking-skeleton import proof**: `python -c "import libs.contracts, services.harness,
services.control_plane"` succeeds, `mypy --strict services libs` passes, and `test_m01_repo` (AC-REPO-002/006/007)
is green — proving the namespace and the layout constraints coexist **before** a single downstream import is written.
If they prove jointly unsatisfiable under uv, that is a bundle bug → stop and flag (not a hand-wave); current
analysis says they are satisfiable via package-dir/force-include mapping.

### 4 · Resolved-ambiguity build rules (encode exactly; do not re-litigate)

- **A-006 (cost breaker basis):** `check_meeting_budget()` **returns the full sum** (model+transport+e2b), but
  the soft/hard caps that drive degrade→notes-only apply to the **listening subset** (transport+Scribe+orch-idle)
  only; Workroom/Opus/E2B spend is governed **solely** by the pre-dispatch estimate gate on `dispatch_workroom`,
  never the listening breaker. (M14: AC-INV-002/003; thresholds `task_spend_trips_listening_breaker=0`.)
- **A-007 (banned strings):** "GCE-per-meeting"/"GCE per meeting" is **removed** from the banned set (A1 revived
  the topology). Still-dead tokens that must fail: `session_transcripts`, `ManagedResource`, `warm pool`,
  `map_* pipeline`, `TILE_ADDRESS`, "every ask→workroom", "bundles the notes object". (M9: AC-CI-007.)
- **A-008:** `meeting_runtime` is **GCE MIG, not Cloud Run** (stale §5.3 prose superseded). (M3: AC-HOST-005.)
- **A-009 (FK chain):** `meeting_cost.meeting_id` **and** `staged_drafts.meeting_id` are declared
  `REFERENCES meetings(id)` in the migration (derived obligation of tenant isolation) — this makes AC-TEN-001's
  FK chain to `tenants` structurally hold. (M4: AC-SUB-025/027; M16: AC-TEN-001.)
- **A-010:** Doc 00 asserts only single-definition/DRY for `resume_with_fallback` (AC-CMP-010); **do not** invent
  an arity — it is pinned in Doc 04/05.
- **A-011:** progress event = Envelope structural fields, **no** finalized `EnvelopeStatus`; encoding agnostic. (M1.)

### 5 · Build-ahead dependencies (honest cross-cutting; a milestone marks when criteria go *green*, not first-touch)

- **`libs/db` + Alembic migrations** are stood up **during M4** (the substrate's schema — `operation_runs`,
  `meeting_cost`, `staged_drafts`, identity tables — needs migrations to apply), though the DB-layer criteria are
  formally green at **M10** (AC-DB-*) and the migrate-retry CMD at **M8** (AC-DOCK-003). `_support.apply_migrations`
  runs `alembic upgrade head`, so Alembic must exist by M4.
- **`libs/contracts`** (M1) and **the registry** (M11) share `ProxyMessage`; the registry base class is defined in
  M1, its closure check + dispatch validation completed in M11.
- **`Database` facade** (M10 criteria) is exercised by the M17 workflows, so its `repos` surface must be complete
  by M16.

### 6 · Adopt-vs-build ledger (commodity → adopt; differentiated glue → build)

**Adopt:** uv workspace + hatchling; Pydantic v2 (models/Literal/`get_args`); pydantic-settings; FastAPI
lifespan; asyncpg/psycopg3; Postgres partial-unique-index + `pg_advisory_xact_lock`; Alembic; Terraform google
provider + Cloud SQL Auth Proxy + GCS Object-Versioning + GCP KMS; Cloud Build; GitHub Actions + pre-commit;
ruff/mypy/bandit; structlog; Sentry; Langfuse (inert); Authlib + Google OIDC; Nango; E2B/Recall SDKs;
`testing.postgresql`. **Build (the differentiated glue only):** the wire contracts + registry + `stream_deltas`
delta-izer; the broker-free durable substrate (`with_operation_run`/fencing/atomic-claim/reconcile); the
per-tenant envelope-key crypto-shred scheme; the `dispatch()` funnel; the `is_owner` emit-frontier gate; the
cost meters; the trifecta guards. **No abstraction until a second concrete use exists; no config flag/base
class/defensive branch a criterion doesn't demand** (V0 has zero runtime flags — AC-CFG-009).

### 7 · Milestones (each: goal · criteria · exit gate)

- **M1 — Contract seam + walking skeleton (`test_m00_cmp`, 16).** Stand up the minimal uv workspace to host
  `libs/contracts` + `libs/agentkit` + `libs/http`; define every §2 wire type, the `AgentChunk`/`ChunkType`
  union with per-variant metadata, `stream_deltas` (one def, one call site in `BehaviorRunner.run`, correct
  per-`msg_id` suffix delta-izing incl. double-application corruption — AC-CMP-015), typed `BehaviorConfig`
  (no YAML), and single-definition `dispatch()`/`AbortRegistry`/`resume_with_fallback` stubs. **Resolve R1/§3
  first.** *Criteria:* AC-CMP-001..016. *Gate:* import proof (§3) + all 16 green.
- **M2 — Repo skeleton (`test_m01_repo`, 9).** Complete the layout: `services/`=5, `libs/`=6, `apps/{connect,tile}`
  Vite/pnpm (excluded from uv), src-layout everywhere, one `requires-python`, one root `uv.lock`, explicit deps,
  Dockerfile `uv sync --package <svc> --no-editable`, no god-package. *Criteria:* AC-REPO-001..009.
- **M3 — Hosting & crypto (`test_m02_host`, 14; R3).** Terraform text for the three deployables (`control_plane`
  Cloud Run: timeout 3600, `cpu-throttling=false`, Cloud SQL annotation, Direct-VPC, minScale≥1; `meeting_runtime`
  **GCE MIG, no bus/broker/volume**; `code_intel` stateful host + per-tenant encrypted volume); one PG15 private-IP
  via Auth-Proxy Unix socket; GCS Object-Versioning; no k8s/mesh/multi-region/GPU. **Build the per-tenant
  envelope-key crypto-shred (AC-HOST-013/014) first.** Direct-answer path touches no E2B/Workroom (AC-HOST-007).
  *Criteria:* AC-HOST-001..014 (AC-HOST-005 re-asserted M9).
- **M4 — Durable substrate (`test_m03_sub`, 37 + AC-OBS-007; R2).** `libs/ops` + `libs/db` + Alembic (build-ahead).
  `operation_runs` canonical columns + partial-unique index + status domain; `with_operation_run` heartbeat;
  fencing (rowcount-0 → `is_owner=False` → emit-frontier refuses all five verbs, AC-SUB-035); atomic `claim_meeting`
  + `created_by` owner-id (feeds AC-OBS-007 affinity); lazy + boot sweep; `check_pause`; sandbox verbs (no FSM) +
  triple-bound + join-driven pre-provision; idempotent token-gated reconcile; `webhook_events` dedupe→200→drain,
  **no `meeting_events` bus**; `meeting_cost` persisted + reload-not-reset; `staged_drafts` persisted at creation
  (survives teardown); identity/tenancy schema `{tenants,users,repos,meetings,sessions}`; restart-not-resume.
  **A-009 FK edges here.** *Criteria:* AC-SUB-001..037, AC-OBS-007.
- **M5 — Server boot (`test_m04_boot`, 7).** Fail-fast settings (names missing key); ordered lifespan
  (tracing→pool→Database→`provisioner_ready`→reaper→routers); reaper before routers; EPIPE tolerated / unknown
  crashes; parallel graceful shutdown; three Claude SDK auth modes. *Criteria:* AC-BOOT-001..007.
- **M6 — Config & secrets (`test_m05_cfg`, 11).** `.env.example` = boot-gate manifest; `routing.py` 8-seat real
  ids; `PROXY_MAX_INFLIGHT_LLM`; per-domain AES-256-GCM keys; `config/defaults.toml` tunables (env overrides
  secrets/seats only); Terraform `random_id` + `ignore_changes=[secret_data]`; `check-secret-bindings`; Nango vs
  Secret Manager split; Authlib+Google OIDC `/auth/{login,callback,logout}`; `[latency_slo]`; zero runtime flags.
  *Criteria:* AC-CFG-001..011.
- **M7 — Terraform layout (`test_m06_iac`, 6).** `modules/{bootstrap,platform}` + `envs/{dev,prod}`; dev
  auto-deploy / prod promote-only; `prevent_destroy` on data-bearing; template `ignore_changes`; least-privilege
  SA-per-role; `customer-platform` module recorded-builds-nothing. *Criteria:* AC-IAC-001..006.
- **M8 — Dockerfile (`test_m07_dock`, 4).** Multi-stage uv `--frozen --no-dev --package`; non-root uid 1001 + HOME;
  advisory-lock migrate + 30×5s retry then exec; `SANDBOX_IMAGE_HASH` LABEL. *Criteria:* AC-DOCK-001..004.
- **M9 — CI/CD (`test_m08_ci`, 7 + AC-HOST-005).** Fast ruff/mypy/unit/security block merges; `check-migration-order`;
  `check-sdk-isolation-triad`; Cloud Build build→AR→deploy + separate migrations; every guard in pre-commit **and**
  CI; fast/nightly split; banned-strings (**A-007**). *Criteria:* AC-CI-001..007, AC-HOST-005 (re-assert).
- **M10 — DB layer (`test_m09_db`, 4).** Pool `min2/max20/lifetime30/timeout10`; `Database` facade + repos, no
  ORM; `meeting_id` uuid everywhere except `operation_runs.scope_id` text; Alembic env.py advisory lock + retry.
  *Criteria:* AC-DB-001..004.
- **M11 — Contracts registry (`test_m10_reg`, 6).** `ProxyMessage.__init_subclass__` auto-register; single registry
  + `MessageType` enum; `assert_registry_closed()` (boot + CI); orphan type fails; Pydantic discipline
  (UUID/`max_length`/`Literal`); dispatch funnel validates client msgs once (tile→backend untrusted);
  signal-surface excluded (AC-CMP-011). *Criteria:* AC-REG-001..006.
- **M12 — Observability (`test_m11_obs`, 10).** structlog JSON; Sentry once; cost telemetry cache-read/creation
  split; Langfuse inert; `/health` + Healthchecks; hardening script (both firewall layers); live-WS affinity
  (re-assert AC-OBS-007); skip-list clean; **no raw source in logs/Sentry/artifacts**; volume snapshots.
  *Criteria:* AC-OBS-001..010.
- **M13 — Constitution (`test_m12_con`, 4).** Root `CLAUDE.md`: every hard rule names its guard; no internal names
  in user strings (product=Proxy); tool handlers return errors never throw; external calls wrapped retry+telemetry.
  *Criteria:* AC-CON-001..004.
- **M14 — Consolidated invariants (`test_m13_inv`, 13; R4).** Two honest cost meters (**A-006**); pre-dispatch
  estimate gate; **lethal-trifecta** (no transcript→side-effect without a click); transcript fenced untrusted;
  world-touching in `disallowed_tools`; core apply = code-change draft not push; secret read-path redaction;
  per-sandbox random JWT; offboarding sweep; accept requires authenticated tenant member (CSRF+idempotent+audit);
  read-only capability token; full tool telemetry. *Criteria:* AC-INV-001..013.
- **M15 — Build order & spike (`test_m14_bld`, 3).** Pre-build spike gate (p50 ≤ ~2.5s direct-answer + reliable
  `who_writes`/`get_dependents`); deterministic fallback per branch, never a silent patch; step-1 completion proof
  (CI-green + self-migrate/`/health` + deploy-lands + registry-closed + harness heartbeat/self-reap).
  *Criteria:* AC-BLD-001..003.
- **M16 — Tenant/creds cross-cutting (`test_m15_ten`, 3; R4).** `tenant_id` in **every** durable schema (A-009 FK
  chain); cross-tenant read refused, zero rows leak; Nango GitHub tokens minted per-operation, never cached/logged.
  *Criteria:* AC-TEN-001..003.
- **M17 — End-to-end workflows (`test_w_workflows`, 12 chains, 0 new criteria).** W01 connect→bind; W02 duplicate-join
  →single-owner→reap→reclaim; W03 reclaimed-zombie-emits-nothing; W04 webhook land→200→dedupe→drain; W05
  direct-answer-no-E2B; W06 cost-survives-recycle + resume-guard; W07 draft-survives-teardown→accept; W08 trifecta;
  W09 cross-tenant-refused; W10 ordered-boot fail-fast→health; W11 stream_deltas-once feeds all consumers + cost
  meter; W12 sandbox-bounded + reconcile-idempotent. *Gate:* all green — the integration proof R1–R4 compose.

### 8 · Non-goals / do-not-build (skip-list — building any is a defect: AC-OBS-008)

Kubernetes/mesh/multi-region · GPU/local inference · `meeting_events` bus/broker · `ManagedResource` FSM ·
`workroom_tasks`/`close_jobs`/`meeting_harness`/`feature_flags`/`meeting_cost_entries` tables · warm sandbox pool
· YAML behavior registry · embeddings/vector DB/SCIP/Zoekt · self-hosted Langfuse · per-customer-GCP-project
machinery · `resume_with_fallback` arity (Doc 04/05) · any runtime feature flag.

### Review deltas
*(folded from `planner-reviewer`; see below)*
