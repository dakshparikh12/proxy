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

**Coverage (RTM re-derived locally):** **155 criteria in the sealed `criteria.yaml`** (per-prefix: CMP 16,
REPO 9, HOST 14, SUB 37, BOOT 7, CFG 11, IAC 6, DOCK 4, CI 7, DB 4, REG 6, OBS 10, CON 4, INV 13, BLD 3,
TEN 4), **155/155 referenced by a test file, 0 dangling, 0 uncovered.** *(The `manifest.yaml` `counts.criteria`
field reads 154 — stale by one: the 2nd adversarial review's `+5 criteria` added the P0 `AC-TEN-004` but the
count field was not bumped. The sealed `criteria.yaml` is the source of truth and `test_m15_ten.py` exercises
`T-TEN-004`, so this is a manifest bookkeeping drift, **not** a coverage gap and **not** `SPEC_BLOCKED`; flagged
for the conductor.)* **Cross-file appearances (owner corrected after review):** `AC-OBS-007` is **owned by M12**
(`test_m11_obs.py` holds the real `route_to_owner` / `served_by_non_owner==0` assertion); M4 only supplies its
prerequisite `operation_runs.created_by` column, owned by `AC-SUB-036` — so M4 does **not** own AC-OBS-007.
`AC-HOST-005` is **owned solely by M3** (`test_m02_host.py`); M9's `test_m08_ci.py` mentions it only in a comment
explaining the AC-CI-007 banned-strings rationale (A-007) — **not** a second assertion. `test_w_workflows.py`
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
  (`provision/destroy/health_check`) · `run_reconcile_sweep` · plus the homes the sealed `test_m03_sub` hard-imports:
  `OperationHandle` · `cost.{MeetingCost, dispatch_workroom, record_micro_call_cost, check_meeting_budget}` ·
  `logging.{configure_logging, get_logger}` · `sentry.before_send` · `affinity.route_to_owner`.
- **M4 service surface** (built at M4 against the frozen import homes `test_m03_sub` pins): `services.harness.
  {build_emitter, recover_meeting_harness, ingest_webhook, drain_pending_webhooks, check_meeting_budget,
  complete_signin, resolve_session, invite_proxy, resolve_bot_id, record_seam_cost}` · `services.workroom.
  {recover_task, propose_change, accept_draft}` · `services.scribe.{record_scribe_cost, apply_note_delta}`.
  (Tests accept alternate homes via try/except; pick the AGENTS.md-canonical one per §3's namespace mapping.)
- **Concrete API the workflows pin** (must exist by M17): `services.harness.{emit.Emitter, wake.answer_direct,
  budget.check_meeting_budget, orchestrator.run_wake_turn, server.lifespan_trace, settings}` ·
  `services.workroom.{recovery.should_restart, drafts.propose_change/teardown_review_session}` ·
  `services.control_plane.{webhooks.ingest/drain_pending, accept.accept_draft, authz.read_meeting}`.

### 2 · The risky-20% register (built + adversarially self-tested FIRST within each milestone)

The harness fixes the *milestone* order, so "risky-first" (writing-plans rule #5) is applied as: **design all
four clusters up-front (here), then within each owning milestone build its P0 boundary criteria before any
P1/P2 convenience, and self-attack each before moving on.** All **24 P0 criteria** are enumerated across
R2–R4 below (R1 is the enabling seam, owning no P0 of its own): R2 = 12 (`AC-SUB-002,003,007,008,009,011,012,035`
concurrency/fencing + `AC-SUB-025,026,027,028` cost/draft durability), R3 = 3 (`AC-HOST-013,014` + `AC-INV-009`),
R4 = 9 (`AC-INV-004,005,006,008,011` + `AC-TEN-001,002,003,004`). 12+3+9 = 24, no P0 double-listed:

| # | Risk cluster | Owning milestone | P0 boundary criteria | The exact boundary that must not slip |
|---|---|---|---|---|
| R1 | **Import-namespace seam** | M1→M2 | (enables all) | `import libs.contracts` **and** `import services.control_plane.webhooks` resolve **while** `services/`=5 dirs (AC-REPO-006) and every member is `src/<pkg>/` (AC-REPO-002). See §3. |
| R2 | **Concurrency + cost/draft durability substrate** | M4 | AC-SUB-002,003,007,008,009,011,012,035 (concurrency/fencing) · AC-SUB-025,026,027,028 (durability) | **Concurrency:** one running row per (scope,type); interrupted/completed never blocks re-claim; fencing rowcount-0 ⇒ `is_owner=False` ⇒ **zero** emits on the wire (speak/send_chat/show_screen/apply/dispatch). **Durability (money + irreversible write):** `meeting_cost` canonical columns + FK→`meetings(id)` (A-009); a recycled orchestrator **reloads** spent cost from `meeting_cost`, never resets to 0; `staged_drafts` persisted at creation (GCS Object-Versioned + `proposed` row, FK→`meetings(id)`); a human accept **after teardown** reads the persisted draft, not the dead sandbox. Build + self-attack these four before the P1 substrate mass. |
| R3 | **Crypto-shred isolation** | M3 | AC-HOST-013,014 (+ AC-INV-009) | distinct per-tenant envelope key; destroying A's key leaves A unrecoverable **and** B fully readable; KMS PD floor; no LUKS; per-sandbox random JWT. |
| R4 | **Lethal-trifecta + tenant isolation** | M14→M16 | AC-INV-004,005,006,008,011; AC-TEN-001,002,003,004 | no transcript-triggered path reaches an outward side-effect without a human click; transcript fenced as untrusted data; world-touching tools in `disallowed_tools`; secrets redacted; accept requires authenticated tenant member (CSRF+idempotent+audit); cross-tenant read refused, zero rows leak; Nango tokens per-operation, never cached/logged; **`/internal/notes` token-gated + `meeting_id→tenant`-scoped on the session-less internal path (AC-TEN-004, P0)**. *(Per-sandbox random JWT `AC-INV-009` lives in R3.)* |

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
**M1 exit gate includes a walking-skeleton import proof — run inside the actual `uv`-synced venv (`.venv/bin/python`,
the same interpreter `verify.sh` uses), NOT bare repo-root**: `python -c "import libs.contracts, services.harness,
services.control_plane"` succeeds, `mypy --strict services libs` passes, and `test_m01_repo` (AC-REPO-002/006/007)
is green. This matters because `conftest.py` puts repo-root on `sys.path` and repo-root `services/` has **no**
`control_plane/` dir (AC-REPO-006) — so `import services.control_plane` can only resolve from the installed
workspace mapping, never from the tree. The builder must **confirm the editable/force-included namespace install
actually exposes `services.<pkg>`** (editable installs of package-dir-remapped namespaces are a known failure
mode) — proving the namespace and the layout constraints coexist **before** a single downstream import is written.
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
  *Criteria:* AC-HOST-001..014 (AC-HOST-005 owned here; M9 only references it in an AC-CI-007 comment).
- **M4 — Durable substrate (`test_m03_sub`, 37; R2).** `libs/ops` + `libs/db` + Alembic (build-ahead).
  `operation_runs` canonical columns + partial-unique index + status domain; `with_operation_run` heartbeat;
  fencing (rowcount-0 → `is_owner=False` → emit-frontier refuses all five verbs, AC-SUB-035); atomic `claim_meeting`
  + `created_by` owner-id (AC-SUB-036 — the enabler M12's AC-OBS-007 affinity later reads); lazy + boot sweep; `check_pause`; sandbox verbs (no FSM) +
  triple-bound + join-driven pre-provision; idempotent token-gated reconcile; `webhook_events` dedupe→200→drain,
  **no `meeting_events` bus**; `meeting_cost` persisted + reload-not-reset; `staged_drafts` persisted at creation
  (survives teardown); identity/tenancy schema `{tenants,users,repos,meetings,sessions}`; restart-not-resume.
  **A-009 FK edges here.** *Criteria:* AC-SUB-001..037 (AC-SUB-036's `created_by` enables AC-OBS-007, which
  goes green at M12 — not owned here).
- **M5 — Server boot (`test_m04_boot`, 7).** Fail-fast settings (names missing key); ordered lifespan
  (tracing→pool→Database→`provisioner_ready`→reaper→routers); reaper before routers; EPIPE tolerated / unknown
  crashes; parallel graceful shutdown; three Claude SDK auth modes. *Criteria:* AC-BOOT-001..007.
- **M6 — Config & secrets (`test_m05_cfg`, 11).** `.env.example` = boot-gate manifest; `routing.py` 8-seat real
  ids; `PROXY_MAX_INFLIGHT_LLM`; per-domain AES-256-GCM keys; `config/defaults.toml` tunables (env overrides
  secrets/seats only); Terraform `random_id` + `ignore_changes=[secret_data]`; `check-secret-bindings` — home
  **`libs.ops.check_secret_bindings`** (the test accepts `services.ops` too, but that is not one of the 5 allowed
  `services/` dirs per AC-REPO-006, so pick the lib to avoid a needless remap); Nango vs
  Secret Manager split; Authlib+Google OIDC `/auth/{login,callback,logout}`; `[latency_slo]`; zero runtime flags.
  *Criteria:* AC-CFG-001..011.
- **M7 — Terraform layout (`test_m06_iac`, 6).** `modules/{bootstrap,platform}` + `envs/{dev,prod}`; dev
  auto-deploy / prod promote-only; `prevent_destroy` on data-bearing; template `ignore_changes`; least-privilege
  SA-per-role; `customer-platform` module recorded-builds-nothing. *Criteria:* AC-IAC-001..006.
- **M8 — Dockerfile (`test_m07_dock`, 4).** Multi-stage uv `--frozen --no-dev --package`; non-root uid 1001 + HOME;
  advisory-lock migrate + 30×5s retry then exec; `SANDBOX_IMAGE_HASH` LABEL. *Criteria:* AC-DOCK-001..004.
- **M9 — CI/CD (`test_m08_ci`, 7).** Fast ruff/mypy/unit/security block merges; `check-migration-order`;
  `check-sdk-isolation-triad`; Cloud Build build→AR→deploy + separate migrations; every guard in pre-commit **and**
  CI; fast/nightly split; banned-strings (**A-007** — its rationale comment references AC-HOST-005's GCE topology,
  but does not re-assert it). *Criteria:* AC-CI-001..007.
- **M10 — DB layer (`test_m09_db`, 4).** Pool `min2/max20/lifetime30/timeout10`; `Database` facade + repos, no
  ORM; `meeting_id` uuid everywhere except `operation_runs.scope_id` text; Alembic env.py advisory lock + retry.
  *Criteria:* AC-DB-001..004.
- **M11 — Contracts registry (`test_m10_reg`, 6).** `ProxyMessage.__init_subclass__` auto-register; single registry
  + `MessageType` enum; `assert_registry_closed()` (boot + CI); orphan type fails; Pydantic discipline
  (UUID/`max_length`/`Literal`); dispatch funnel validates client msgs once (tile→backend untrusted);
  signal-surface excluded (AC-CMP-011). *Criteria:* AC-REG-001..006.
- **M12 — Observability (`test_m11_obs`, 10).** structlog JSON; Sentry once; cost telemetry cache-read/creation
  split; Langfuse inert; `/health` + Healthchecks; hardening script (both firewall layers); live-WS affinity
  routes reconnects to the `operation_runs` claim owner (**AC-OBS-007 goes green here**, reading M4's `created_by`);
  skip-list clean; **no raw source in logs/Sentry/artifacts**; volume snapshots.
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
- **M16 — Tenant/creds cross-cutting (`test_m15_ten`, 4; R4).** `tenant_id` in **every** durable schema (A-009 FK
  chain); cross-tenant read refused, zero rows leak; Nango GitHub tokens minted per-operation, never cached/logged;
  **`/internal/notes` (P0, AC-TEN-004)** token-gated *outside* the auth wall on the session-less internal path,
  resolving `meeting_id → owning tenant` server-side — untokened/invalid-token refused (returns no notes) and no
  cross-tenant notes ever returned (the internal-notes exposure frontier that AC-TEN-002's session-based `/m/`
  oracle structurally cannot exercise). Handler home: `services.harness.internal` / `libs.http.internal`
  (test accepts either). *Criteria:* AC-TEN-001..004.
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

### Review deltas — folded from the `planner-reviewer` (skeptical staff-engineer pass)

Reviewer verdict: **plan sound — no CRITICAL, no `SPEC_BLOCKED`.** Per-rule: rule 1 (order) PASS · rule 2
(coverage) PASS after fixes · rule 3 (seams) PASS · rule 4 (adopt-vs-build) PASS · rule 5 (risky-first) PASS
after fixes · must-not (no sealed edits / no weakening / no over-build) PASS. Independent re-derivation confirmed
**155 criteria, 24 P0s, per-prefix counts exact, manifest `counts.criteria:154` stale-by-one (AC-TEN-004)**.
The following change requests were folded into the plan above:

- **[author, pre-review] AC-TEN-004 coverage gap (P0).** The draft mapped only `AC-TEN-001..003` and claimed
  "154 criteria". Sealed `criteria.yaml` holds **155**; the P0 `/internal/notes` criterion `AC-TEN-004` (tested
  by `test_m15_ten.py::test_ten_004`) was unmapped. → Fixed §0 count (155/155 + manifest-drift note), M16
  (`AC-TEN-001..004`, count 4, description added), R4 risk row (AC-TEN-004 added).
- **[IMPORTANT #1] R2 omitted 4 P0 durability criteria.** `AC-SUB-025/026/027/028` (cost-survives-recycle,
  draft-survives-teardown — money + irreversible-write P0s) were buried in M4's P1 mass, violating rule #5. →
  §2 R2 row expanded to name all 12 SUB P0s as build-first/self-attack-first; intro line now enumerates all 24
  P0s across R2–R4 (12+3+9) with no double-listing.
- **[IMPORTANT #2] AC-OBS-007 ownership inverted.** Draft said "green at M4, re-asserted M12"; the real
  `route_to_owner` assertion lives only in `test_m11_obs.py` (**M12 owns it**); M4 supplies only the `created_by`
  enabler (owned by `AC-SUB-036`). → Fixed §0, M4 *Criteria* line, and M12 wording.
- **[MINOR #3] AC-HOST-005 "re-asserted M9" overstated.** M9's `test_m08_ci.py` only *comments* on the GCE
  topology inside the AC-CI-007 test — not a second assertion. **M3 is sole owner.** → Fixed §0, M3, M9 lines.
- **[MINOR #4] §3 import-proof environment.** The walking-skeleton proof must run in the `uv`-synced venv
  (`.venv/bin/python`), because `conftest` puts repo-root on `sys.path` where `services/control_plane/` does not
  exist — `import services.control_plane` resolves only from the workspace mapping. Builder must confirm the
  editable/force-included namespace install exposes `services.<pkg>` (a known editable-remap failure mode). →
  §3 M1 exit gate reworded. Reviewer confirmed the layout/namespace resolution is **jointly satisfiable, not
  hand-waving** (`test_m01_repo` forbids only a flat `services/<pkg>/<pkg>/`; a force-included `src/control_plane/`
  is permitted; AC-CMP-001 counts deployables from infra text, not dirs).
- **[MINOR #5] Under-specified seam homes.** → `check-secret-bindings` pinned to `libs.ops.check_secret_bindings`
  (M6); §1 now enumerates the concrete `services.harness.*` / `services.workroom.*` / `services.scribe.*` and
  extra `libs.ops.*` homes that `test_m03_sub` hard-imports.

Reviewer confirmed all resolved-ambiguity encodings (**A-006/A-007/A-009/A-010/A-011**) faithful to
`requirements/ambiguities.yaml`, and no milestone builds a skip-list item, a runtime flag (AC-CFG-009), or an
abstraction no criterion demands. **Plan LOCKED — hand off to `subagent-driven-build`.**

## ADJUDICATION RESOLVED — proceed with this reading:
 — No `SPEC_BLOCKED` entry was ever recorded in `PROGRESS.md`; the doc00 plan asserts "0 `SPEC_BLOCKED`, 0 unresolved contradictions," `dispositions.yaml` agrees, and the build is green through M4, so there is nothing genuinely blocked — continue in the mandated milestone order to M5 (`test_m04_boot`, AC-BOOT-001..007). To preempt the one near-frontier ambiguity (the "(prod)"-qualified boot keys), implement the reading the spec and criterion already fix in lockstep — `00-FOUNDATION.md:203` and `AC-BOOT-001` (`criteria.yaml:1632`) both list "`DATABASE_URL`, `GCS_BUCKET`, `SESSION_SECRET` (prod), GCP project (prod), each AES credential key, `RECALL_API_KEY`, `ANTHROPIC_*`": treat `DATABASE_URL`, `GCS_BUCKET`, the AES credential keys, `RECALL_API_KEY`, and `ANTHROPIC_*` as unconditionally req

## SPEC_BLOCKED — M11 registry (AC-REG-002 vs AC-REG-004/005), 2026-07-17

**Status:** Build is green through M10 (115/115 criteria on test_m00_cmp … test_m09_db;
ruff + mypy --strict clean). M11 (`test_m10_reg.py`, AC-REG-001..006) is **blocked by a
test-proven contradiction inside the sealed bundle** — I stopped the pass here per AGENTS.md
("an untestable/contradictory criterion is a spec bug — record it in PROGRESS.md and stop").
reg_001/reg_004/reg_005 pass with the CANONICAL-correct registry I built; reg_002 cannot pass.

**Blocked criterion:** `AC-REG-002` — `tests/doc00/test_m10_reg.py::test_reg_002_assert_registry_closed_passes_when_set_equal`.

**Exact conflict (contradiction between three sealed criteria + a CANONICAL override):**

- `AC-REG-002` asserts, on the *live* objects:
  `union = {str(m) for m in get_args(MessageType)}; registry = {str(k) for k in CHANNEL_REGISTRY}; assert union == registry`.
- `AC-REG-005` (`test_reg_005`) asserts `isinstance(MessageType, type) and issubclass(MessageType, enum.Enum)`
  (with error text "MessageType must be an Enum (closed discriminator), not an open alias").
  This is the CANONICAL decision: `CANONICAL-DECISIONS.md:18` — "discriminator `MessageType` (an `Enum`)";
  `08-EXPERIENCE.md:188` — `class MessageType(StrEnum)`. Per AGENTS.md, CANONICAL overrides the doc it conflicts with.
- `AC-REG-004` (`test_reg_004`) asserts `models = list(CHANNEL_REGISTRY.values()); assert models` (registry non-empty).

`typing.get_args(X)` returns `()` for **any** class that is not a subscripted typing generic —
proven for a plain Enum **and** the CANONICAL `StrEnum`:
`get_args(<enum.Enum subclass>) == ()` and `get_args(<enum.StrEnum subclass>) == ()`.
Therefore, when `MessageType` is an Enum (forced by AC-REG-005 + CANONICAL), `union` in AC-REG-002 is
ALWAYS `set()`, so `union == registry` can only hold when `CHANNEL_REGISTRY` is **empty** — which
`AC-REG-004` forbids. No object can be simultaneously a subscripted generic (non-empty `get_args`,
required by AC-REG-002) **and** an `isinstance(x, type)` Enum subclass (required by AC-REG-005). The
two criteria are jointly unsatisfiable with a non-empty registry.

**Root cause:** `AC-REG-002` was written against the *stale* Doc 00 §12 code snippet
(`00-FOUNDATION.md:303`: `assert set(get_args(MessageType)) == set(CHANNEL_REGISTRY)`), which presumes
`MessageType` is a `Literal`. That snippet is superseded by `CANONICAL-DECISIONS.md:18` (Enum) and by the
canonical closure in `09-VERIFICATION.md:16` (`set(MessageType) == set(CHANNEL_REGISTRY)`, i.e. iterate the
Enum members, NOT `get_args`). The sealed test kept the stale `get_args` form; with the CANONICAL Enum it is
unsatisfiable. Fix belongs in the sealed bundle (builder cannot edit `tests/`/`acceptance/`): `AC-REG-002`
should assert `set(m.value for m in MessageType) == set(CHANNEL_REGISTRY)` (or `set(MessageType)`), matching
`09-VERIFICATION.md:16` and `AC-REG-005`.

**Evidence (test-proven, not assertion):**
- `python -c "import enum,typing; class M(enum.StrEnum): A='a'; print(typing.get_args(M))"` → `()`.
- `pytest test_m10_reg.py::test_reg_002` **in isolation** (no probe pollution) fails with
  `union-only=set(), registry-only={'invite-proxy','connect-repo','approve-draft'}` — i.e. the product
  registry (3 CANONICAL client message types) is non-empty while `get_args(MessageType)` is empty.
- reg_001/reg_004/reg_005 PASS with the same registry (Enum `MessageType`, `__pydantic_init_subclass__`
  auto-registration keyed on `model_fields["type"].default`, `validate_inbound_message` funnel).

**Work committed with this block (correct-per-CANONICAL, kept for the continuation):**
`libs/contracts/src/contracts/registry.py` rewritten to the CANONICAL design — `MessageType(enum.Enum)`,
`ProxyMessage.__pydantic_init_subclass__` auto-registration, three concrete tile→backend messages
(field-discipline clean: UUID ids, `Field(max_length)` free-text, `Literal` selectors),
`assert_registry_closed()` (set-equality of Enum values vs registry + signal-surface leak guard),
`validate_inbound_message()` central funnel; `MessageType`/`validate_inbound_message` exported from
`libs.contracts`. M1 (AC-CMP-009/011) and M2–M10 remain green.

### Independent re-verification (builder session 2, 2026-07-17) — block STANDS, still needs a founder sealed-bundle fix

A second fresh builder session re-derived the contradiction from scratch and **confirms it is genuine**, not a
builder-skill gap. State reproduced: `test_m00_cmp … test_m09_db` = **115/115 green** (ruff + mypy --strict clean);
`test_m10_reg.py` first-red at `test_reg_002` (order: reg_001 pass → reg_002 FAIL), so under the `-x` harness
(`verify.sh`: `pytest -q -x --maxfail=1`) M11 halts M12–M17 entirely — ~40 downstream criteria are stuck behind
this one mis-transcribed criterion.

**Decisive new proof (stronger than the get_args-on-Enum observation):** the two criteria demand mutually
exclusive Python facts of the *same live object* `libs.contracts.MessageType`:
- `AC-REG-005` (`test_m10_reg.py:211`): `isinstance(MessageType, type) and issubclass(MessageType, enum.Enum)`.
- `AC-REG-002` (`:75-77`): `union = {str(m) for m in get_args(MessageType)}` must equal the (non-empty,
  per `AC-REG-004:158`) registry.

`typing.get_args(x)` returns a non-empty tuple **only** when `x` is an instance of `_GenericAlias` /
`types.GenericAlias` / `types.UnionType` / `_AnnotatedAlias`. Empirically verified this session that **every**
such object has `isinstance(x, type) == False` (`list[int]`, `List[int]`, `int|str`, `Union[int,str]`,
`Annotated[int,'x']` all give `get_args non-empty=True, isinstance(type)=False`). Conversely every Enum class
gives `get_args == ()`. Therefore no object can satisfy REG-005 (`isinstance(type)=True`) **and** yield the
non-empty `get_args` REG-002 needs — the intersection is empty **at the language level**, independent of any
implementation choice. The shipped product `assert_registry_closed()` (`registry.py:96`) is already
CANONICAL-correct (iterates Enum members per `09-VERIFICATION.md:16`); the defect is purely the sealed test's
stale `get_args` form.

**No route-around taken.** Building M12–M17 speculatively was declined: it can never register green through
`verify.sh` while reg_002 fails first under `-x`, and shipping unverifiable code violates "verify.sh exit 0 is
the only green." **Required fix (founder-only, builder forbidden to edit `tests/`/`acceptance/`):** change
`AC-REG-002` to `set(m.value for m in MessageType) == set(CHANNEL_REGISTRY)` (equivalently `set(MessageType)`),
matching `AC-REG-005` + `CANONICAL-DECISIONS.md:18` + `09-VERIFICATION.md:16`. Once the bundle is corrected the
existing `registry.py` is expected to pass reg_001..006 unchanged, and the build can resume at M12.
