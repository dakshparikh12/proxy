# Proxy — Master Test List (the single source for deployability confidence)

> Synthesized from the 7 per-subsystem derivations in `live-test/derived/` (ops, db,
> http-contracts, in-meeting, workroom-agentkit, premeeting, control-plane; ~1,800 raw items),
> `TEST_MATRIX.md` + `ACCEPTANCE_FORMAT.md` (the dynamic, per-scenario, process-based acceptance
> philosophy), and `PROXY_SYSTEM_SPEC.md` (product intent).
>
> **What this file is.** One deduped, organized, classified master list of everything that must
> hold for Proxy to be customer-deployable in ANY meeting scenario. A wiring that appeared on
> both the producer and consumer side of two files is merged into ONE item. It feeds two things:
> (a) a **LIVE** end-to-end meeting test — Proxy + 2 speaking Recall bots on a real repo, judged
> scenario-by-scenario against the process invariants; and (b) a set of **INTERNAL** targeted
> unit/integration tests for the things a meeting can never exercise (tenant-isolation SQL, HMAC,
> reaper races, boot order, contract closure, migrations).
>
> **Classification legend**
> - **LIVE** — provable in the live meeting transcript (product behavior, the reactive loop,
>   nuances, channels, hear/speak, present-back). Judged per-scenario, process-based (never a
>   numeric threshold in Phase A).
> - **INTERNAL** — needs a targeted unit/integration test on real substrate; a meeting can't
>   exercise it (isolation SQL, HMAC, races, boot order, closure gates, migrations, guards).
> - **BOTH** — the behavior shows in the meeting AND has an internal invariant that must be
>   asserted deterministically (e.g. barge-in is heard live AND the ≤200ms budget/cut path is
>   unit-proven; self-echo is observed live AND the 45s/4-token/0.7 window is unit-proven).
>
> **How to read the P0 and FIX-FIRST sections.** P0 is the non-negotiable cross-cutting battery
> — if any P0 fails, Proxy is not deployable. FIX-FIRST is every real defect / gap / doc-vs-code
> drift the readers surfaced; each names its file and why it matters. Do FIX-FIRST before trusting
> any LIVE result that touches the same path.

---

# 0. Counts

| Classification | Count |
|---|---|
| LIVE | 118 |
| INTERNAL | 511 |
| BOTH | 47 |
| **Deduped master total** | **676** |

(The 676 deduped items compress the ~1,800 raw derivation lines: merged producer/consumer
wirings, collapsed per-field/per-boundary variants into tight groups, and folded the five
files' cross-subsystem sections into the shared integration + P0 batteries. No coverage was
dropped — each raw line maps to a master item or a tight group below.)

P0 MUST-PASS items: **34** (all INTERNAL or BOTH; enumerated in §2).
FIX-FIRST defects/gaps/drift: **22** (enumerated in §3).
LIVE scenario groups: **11** (enumerated in §5, with per-group item counts).

---

# 1. Deduped, organized master list (by subsystem/capability)

Each entry: `[CLASS] area — statement`. Tight groups collapse a family of boundary/edge variants
into one line where they test the same behavior. P0 items are marked `‹P0›` and cross-referenced
in §2; FIX-FIRST items are marked `‹FIX-n›` and detailed in §3.

## 1.1 Pre-meeting understanding (services/premeeting)

- [INTERNAL] github_auth — `build_app_jwt` signs RS256 with correct iss/iat(-60s)/exp(+540s), PKCS1v15+SHA256; non-RSA key → AuthError before sign.
- [INTERNAL] github_auth — `InstallationTokenMinter.mint` posts to `/app/installations/<id>/access_tokens`, returns ONLY the token string, mints fresh per call (no instance cache).
- [INTERNAL] github_auth — 401/422/timeout/missing-`token`-key all raise typed `AuthError`; pipeline turns it into `not_ready(["auth: ..."])`, never a hang or silent empty clone.
- [INTERNAL]‹P0› github_auth/cloner — the installation token NEVER appears in any logged argv (redacted at the `gitio.run_git` boundary) or in the `.mint()` return path.
- [INTERNAL] cloner — clean work-tree at `<vol>/<tenant>/repos/<repo>/checkout`, no nested `.git`; blobless clone above 100k files via `file_count_provider`; `ExclusionManager.scan_after_clone` runs after every clone.
- [INTERNAL] cloner — private repo clones via `x-access-token:<token>@github.com/...`; ssh/file URLs unchanged; `pull_delta` is fetch+ff (never re-clone) and re-mints the token each pull.
- [INTERNAL] cloner/gitio — `run_git` refuses `"push"` (RuntimeError before exec); a clone failure returns an empty checkout (no raise) → verify catches it → `not_ready`.
- [INTERNAL] exclusions — default globs (.env/.pem/.key/id_rsa/secrets.*/credentials) + tenant policy globs; AKIA + name-anchored + connection-URI-userinfo secret VALUE collection; `redact()` replaces all occurrences; code-identifier RHS not collected.
- [INTERNAL] paths — `volume_root` env→/tenants→temp fallback; blank tenant_id raises (no shared-root collapse); two tenants same repo → separate roots. ‹FIX-16› tenant_id with slashes is a path-traversal surface (blank check only).
- [INTERNAL] symbol_map — deterministic tree-sitter ranked map with REAL file:line, no model call; personalized-PageRank ranking; `_fit_to_budget` ≈11k tokens; REQUIRED_MAP_MARKERS/REQUIRED_NAV_MARKERS shape anchors; >1MB files + >100-char lines clamped; networkx/scipy-absent → uniform-rank fallback (never crash).
- [INTERNAL] symbol_map — no-parseable-source repo → honest stub map (verify special-cases pass); single-language/tiny repo → `_fallback_rank`; two tree-sitter API shapes both handled.
- [BOTH] comprehension — Part-2 bounded native-Claude pass over a read-only sandbox produces a holistic understanding; `verify_comprehension` grounds every `path:line` claim against the real clone and drops ungrounded ones; wholesale-reject when <½ claims ground or verified text <400 chars. (LIVE: the resident understanding yields zero-read grounded answers — Scenario group 7.)
- [INTERNAL]‹P0› comprehension — `ANTHROPIC_API_KEY` popped inside the sandbox runner (subscription CLI auth only); `exclusions.redact` scrubs any inline credential in the prose; secret never rides the map.
- [INTERNAL] comprehension — token empty/whitespace → early honest return (Part-1 stands); clone-missing / model-wrote-DONE-but-no-file / setup-timeout / E2B-quota → honest `ComprehensionResult` reason, never a fake understanding.
- [INTERNAL]‹FIX-1› comprehension — private-repo sandbox clone uses `shlex.quote(repo_url)` with NO auth-token injection → a private repo's in-sandbox clone FAILS → empty understanding. Gap: Part-2 comprehension only works for public repos today.
- [INTERNAL] understanding — composes `_COMPREHENSION_HEADER + comp + _NAV_HEADER + navigation`; empty-comp → nav alone (no naked divider); both empty → "".
- [INTERNAL]‹FIX-2› map_build/pipeline — `build_understanding_map` has NO outer try/except and `pipeline.run_pipeline`'s map-build stage has none either → an exception escaping `build_comprehension` bubbles uncaught → the connect trigger gets an unhandled exception → user sees NO readiness signal and NO named reason.
- [INTERNAL] map_build — `build_map` = deterministic Part-1 only; `build_understanding_map` runs Part-1 always + Part-2 only when call+token present; honest `degraded=True` when comp fails; passes the COMPACT nav map (not the full ranked dump) to the comprehension pass. ‹FIX-11› deprecated `_build_map_llm` is dead code on the live path (must be removed).
- [INTERNAL]‹FIX-13› map_build — `stream_deltas` is exported + docstring'd as "the AgentChunk consumer the map-build stream reads through" but `map_build.py` does NOT apply it (collects raw chunks). Doc-vs-code lie or unapplied optimization; output differs for a delta-emitting vs accumulation-emitting provider — must resolve + test both fakes.
- [INTERNAL]‹P0› map_store — `save_map`/`load_map` PK `(tenant_id, repo, sha)` with tenant_id FIRST; `load_map(tenantB, repo, sha)` can NEVER return tenantA's row even at identical (repo, sha); byte-exact multi-MB round-trip; fresh conn per call (cross-instance read).
- [INTERNAL] map_store — `load_latest_map` = newest built_at; FK violation on unknown tenant → propagates as store fault → `not_ready`; miss → None. ‹FIX-19› built_at ties → non-deterministic `load_latest` order (needs a tiebreak or documented tolerance).
- [INTERNAL] verify — 5-gate readiness (shape markers / no hallucinated paths / all top-level tracked dirs covered / no secret path or value / ready only on clean pass); accepts EITHER symbol-map OR nav-map+comprehension shape; basename-fallback hallucination check (real file at wrong dir is NOT a hallucination); URL/domain/code-symbol/attribute-ref never treated as path claims.
- [INTERNAL] verify — diverse-repo stress (cal.com, gin/Go, click/Python) all pass without false hallucination reasons; a planted credential in source never appears in the stored map (defence-in-depth with comprehension's redact).
- [INTERNAL] pipeline — sequences mint→clone→map-build→store→verify→ready; emits connecting→cloning→indexing(→ready) to the listener (swallows listener blips); store fault short-circuits verify; `map_store=None` / `minter=None` (public-repo/test) are not readiness failures by themselves; NEVER raises out (except the ‹FIX-2› map-build hole).
- [INTERNAL] readiness — `signal_from_result` → status/states/gaps/sha; a `not_ready` with empty reasons backfills "not ready: no reason recorded" (never an unexplained not_ready).
- [BOTH] refresh — push-triggered rebuild delta-pulls, re-builds Part-1 ONLY (no comprehension), re-stores at new SHA, re-verifies; `rebuilt=True` even if store/verify fails; old-SHA row untouched (a live meeting pinned to old SHA is unaffected). ‹FIX-6› refresh drops to Part-1-only (loses the holistic understanding until a full re-connect). (LIVE side: a mid-meeting push must NOT swap the resident understanding.)
- [INTERNAL]‹FIX-8› refresh — concurrent-push checkout race: two webhooks in flight → two `refresh_on_push` for same tenant/repo → race on the checkout dir → interleaved clone state / possibly stored at wrong SHA (diverged-history ff-fail → head_sha returns old SHA).
- [INTERNAL] repo_context — clone-backed `code_intel` MCP (read/batch_read/grep/glob) confines reads to `clone.resolve()` (".." → "path outside tenant volume"); rg excludes secret paths + redacts secret values; caps (200 grep hits / 20 batch files); every handler returns is_error, never throws; server name `code_intel` matches wake/workroom allowed_tools.

## 1.2 The durable substrate (libs/db + migrations + repo_maps)

- [INTERNAL] config — `load_defaults` parses/memoizes `config/defaults.toml`, walks up to repo root from any CWD; each accessor returns toml value or conservative `_FALLBACK`; missing/malformed/unreadable toml → fallback (never crash); env NEVER overrides operational tunables.
- [INTERNAL]‹P0› config — `assert_reaper_ratio()` passes iff STALE_AFTER_S ≥ 3× HEARTBEAT_S (equal passes, strictly-less fails); raises `ReaperRatioError` naming both values + the 3× rule; raises on heartbeat ≤ 0; MUST be called at boot before any reaper runs (D-033 double-free guard).
- [INTERNAL] config — `sandbox_jwt_ttl_s` (900) > `sandbox_jwt_refresh_margin_s` (300) invariant (a margin ≥ TTL re-mints constantly); JWT signing key comes from Secret Manager, not config/env-inline.
- [INTERNAL] database — `open_pool` sizing (min2/max20/inactive30/cmd_timeout10); `_normalise_dsn` strips `postgresql+psycopg://` only, preserves Cloud SQL unix-socket DSN verbatim; ~2×20≈40 conns stay under Cloud SQL max; unreachable DSN surfaces bounded error; `Database.connect(None)` raises ValueError.
- [INTERNAL] database — `acquire()` returns conn to pool even on body exception (no leak); 21st concurrent acquire waits then succeeds; `Repos` re-instantiated per access (no shared mutable state).
- [INTERNAL] database — sandbox keepalive markers are PROCESS-LOCAL (`bump_activity`/`last_activity_at`); a recycled process / other instance sees None → TTL reconcile treats unknown conservatively (never falsely reaps); distinct from the ownership heartbeat.
- [INTERNAL]‹P0› database — `sweep_stale_operation_runs` flips only `status='running'` rows past STALE_AFTER_S; a BOOTING instance NEVER reaps a FRESH row (D-033); boundary exactly-at-window not reaped; idempotent (2nd sweep = 0); reaping one tenant's stale run never touches another's fresh run; original `created_by` preserved for post-mortem.
- [INTERNAL] repositories — `flip_and_append`/`apply_delta` wrap the comprehension-flip + note-delta append in ONE transaction (both revert on any mid-tx failure); raw-module handles (cost/sessions/webhooks) require the caller to own the tx; two concurrent `db.repos` accesses don't interfere.
- [INTERNAL]‹P0› meetings — `get_by_bot_id` returns the AUTHORITATIVE tenant_id (every downstream write scoped to it — the P0 cross-tenant audit point); map keyed on the meeting's EXACT `pinned_sha` (never "latest") so a push mid-meeting can't swap the SHA.
- [INTERNAL]‹P0› meetings — `get_repo_for_tenant` is tenant-filtered by construction; another tenant's repo resolves to None EXACTLY like a nonexistent repo (no existence leak); `full_name` stored/matched byte-for-byte and must equal the `repo_maps.repo` key (whitespace/case mismatch strands invite at 404).
- [INTERNAL]‹P0› meetings — `get_repo_by_id`/`get_by_id` take a BARE id with NO tenant filter → every call site must have proven the id belongs to the caller's tenant upstream (the unscoped-id audit — enumerate each site).
- [INTERNAL] meetings — insert defaults status='live', platform=NULL not fabricated; FK violations (repo/tenant) + status-CHECK violation raise; `mark_ended` COALESCE keeps first ended_at (idempotent); upsert_repo backfills only NULLs; `0010` UNIQUE index makes concurrent connects produce exactly one repo row.
- [INTERNAL]‹P0› transcript/notes — `flip_and_append` idempotency: twice for the same segment → exactly ONE `seg-<id>` delta + comprehended once (WHERE-NOT-EXISTS guard holds despite NULL window); atomicity: forced 2nd-statement failure rolls back both (segment stays pending, no orphan delta); `transcript_segments` has NO `note` column (dropped in 0004).
- [INTERNAL] notes — `append_delta` returns None on ON-CONFLICT dedupe vs (id,created_at) on fresh; distinct `op` is a new row; NULL window_start_s is NOT deduped by the ledger index (flip_and_append compensates); `load_deltas` ascending-id (deterministic left-fold); `count_segments` before/after invariant proves no lifecycle DELETE; `reap_orphaned_meetings` takes the POOL (boot barrier) and keys on `operation_runs.scope_id = meetings.id::text`.
- [INTERNAL]‹P0› notes — `load_deltas`/`count_segments`/`pending_segment_ids` are meeting-scoped by construction → meeting A never returns meeting B's deltas within a tenant or across tenants.
- [INTERNAL]‹P0› cost — `record_cost` ADDS on ON CONFLICT (meeting_id) (never overwrites); N sequential + 2 overlapping writes sum correctly (no lost update, recycle-safe); zero-cost still upserts; FK to meetings; exactly one row per meeting.
- [INTERNAL]‹P0› drafts — `insert_draft` status='proposed'; `set_draft_status` is the ONLY transition (a proposed draft never auto-applies — Law 3); `list_drafts_for_meeting` single-meeting, oldest-first, deterministic tie-break; `artifact_ref` opaque GCS pointer durable past sandbox TTL; unknown draft → None/[] / 0-row update (never invent).
- [INTERNAL]‹P0› sessions — `get_session` returns the tenant the session was created with; a tampered/forged session_id → None (unknown), never another tenant's row; uuid PK non-enumerable. ‹FIX-21› no expiry column — verify expiry/rotation enforced elsewhere or flag (a never-expiring server session is a risk).
- [INTERNAL]‹P0› identity — `upsert_user_by_email` mints a tenant for a new email, backfills a NULL-tenant legacy user, and (COALESCE) NEVER re-points an existing user to a different tenant (a flip would move the whole data view across the isolation boundary).
- [INTERNAL]‹FIX-9› identity — concurrent first-sign-in for the same new email creates an ORPHAN tenant row (loser of the race mints a tenant BEFORE the conflicting user INSERT). Real race in the SELECT→INSERT-tenant→upsert-user flow; verify acceptable/cleaned.
- [INTERNAL] webhooks — `insert_event` True-on-insert / False-on-`ON CONFLICT (delivery_guid) DO NOTHING` (at-least-once→exactly-once); concurrent same-guid → one True; `_derive_provider` (github keys → github, else recall); provider outside {github,recall} → CHECK raises; `list_pending` FIFO by created_at; drain idempotent (crash before mark_processed → retried, no double-apply); tenant_id nullable, resolved by content at processing time.
- [INTERNAL] migrations — `upgrade head` (0001→0010) + `downgrade base` clean on scratch DB; per-revision round-trips (esp. 0004 destructive recreate / 0005 forward reconciliation / 0007 defaults + `meeting_cost_telemetry` / 0010 dup-repo de-dup before UNIQUE); DAG chain intact; all raw `op.execute` (column set = single source of truth, matched to every repo SELECT/RETURNING).
- [INTERNAL]‹P0› migrations — schema audit: EVERY durable tenant-scoped table reaches `tenants` (tenants/users/repos/meetings/sessions/staged_drafts/meeting_cost/note_deltas/transcript_segments/connect_readiness/repo_maps/webhook_events/meeting_cost_telemetry); `operation_runs` deliberately has NO tenant_id (12 pinned columns); partial unique index `operation_runs_one_running_per_scope`; `note_deltas_source_window_uniq` NULL-window distinctness.
- [INTERNAL]‹FIX-7› cost writers — verify EVERY cost writer targets `meeting_cost` (not the 0007 `meeting_cost_telemetry`); a writer hitting the wrong table silently loses spend so a recycled orchestrator reloads a wrong total.

## 1.3 External-call seam + wire contracts (libs/http + libs/contracts)

- [INTERNAL] call_external — first-attempt success → `ExternalCallOutcome(attempts=1, cost=unit×1)`, value untouched, frozen; `max_retries` default 3, overridable; last captured exception re-raised after exhaustion; non-transient exceptions propagate immediately with no retry/no cost.
- [INTERNAL]‹FIX-3› call_external — backoff is LINEAR (`0.2×attempt`) despite the docstring claiming "exponential" — doc-vs-code drift to flag (assert the actual 0.2/0.4/0.6 sequence).
- [INTERNAL] call_external — cost telemetry ONLY on success (a fully-failed call records NO cost); `total_cost_usd = unit×attempts` (failed attempts metered); `unit_cost_usd` default 0.0 → verify metered callers (model/TTS/E2B) pass a real unit. ‹FIX-15› `_record_cost` only computes/returns — the docstring's "emits to the ops cost ledger" is NOT wired (no real ledger emission).
- [BOTH] call_external — transport-cancel resilience (Law 4): `CancelledError` with `cancelling()==0` is a transport blip → RETRIED with backoff; with `cancelling()>0` → RE-RAISED immediately (prompt shutdown); a meeting-end `task.cancel()` honored within one iteration; a GOAWAY/stream-reset as bare CancelledError doesn't crash the wake loop (WS6 regression). Cancel during the backoff sleep propagates.
- [INTERNAL]‹P0› call_external factories — `check_call_external` guard: NO raw vendor client (Anthropic/httpx/GCS/Recall/Deepgram/Cartesia/E2B `AsyncSandbox.create|connect`) constructed OUTSIDE `libs/http` (aliased/lazy-in-function/attribute-form all caught); lazy SDK imports (importing `http.external` doesn't import anthropic/e2b/gcs); absent SDK → ImportError surfaced honestly (caller decides fatal vs no-op).
- [INTERNAL]‹P0› dispatch — the six-step funnel: is_owner fence (before rate-limit) → per-conn rate-limit → registry lookup → central Pydantic validate (once) → meeting/tenant isolation on meeting_id → entity→owner→tenant (smuggle fix) → route once. Any step-1..5 failure sends exactly one GENERIC error and routes nothing.
- [INTERNAL]‹P0› dispatch — a `meeting_id` owned by a different tenant → `"Not found"` IDENTICAL to a nonexistent one (no tenancy oracle); a smuggled `canvas_id`/`artifact_id` owned by another meeting → `"Not found"` even with a valid own meeting_id (both gates independent); `store.meeting_tenant` reads OUR substrate, never client-self-authorized.
- [INTERNAL] dispatch — non-owner routes nothing AND sends no error (silent refusal, not even counted against rate limit); rate-limit is per-conn (`conn.id`, not tenant/user) → independent budgets; only messages are the two generic strings ("Slow down." / "Not found") — no info leak; unknown/non-string/missing type, malformed body, over-length arg, extra field, non-UUID meeting_id, spoofed outbound type all → "Not found"; MemoryStorage is process-local (documented multi-instance limitation).
- [INTERNAL]‹P0› resolve_entity_tenant — grants only when resolved owner == principal's tenant; a cross-tenant read / non-UUID / null-principal-tenant / absent-owner → `{allowed:False, tenant_id:None}` (the foreign tenant id is NEVER leaked back).
- [INTERNAL]‹P0› gateway — `authorize_upgrade` returns a SERVER-resolved `Connection.tenant_id` (never a client field); no/invalid session → RejectUpgrade(401) BEFORE socket accept; disallowed origin → 403; over `MAX_CONN_PER_USER`(8) → 429; fail-closed order 401→403→429; distinct `Connection.id` per conn.
- [INTERNAL] http/registry — `protected()` yields a NON-NULL tenant_id by construction (403 before AuthzCtx if null); `public()` yields nullable tenant BY DESIGN (can't be a query filter by accident — mypy-provable); PROTECTED_DEP_MARKER walkable through Depends.
- [INTERNAL]‹P0› http/registry — route enumeration: EVERY route classifies protected|public|internal|ws|framework, never `raw`; PUBLIC_ROUTES is the only unauth-reachable set, each earning it by a scoped grant (webhooks HMAC-gated, `/m/{id}` capability-token, `/health` no tenant data); `classify_route` precedence framework→ws→protected→internal→public→raw (a dual-mode `/m/{id}` served to a signed-in member classifies protected, not down-graded to public).
- [INTERNAL]‹P0› webhook — `verify_recall_signature` (Svix HMAC-SHA256 base64 over `{id}.{ts}.{raw_body}`) + `verify_github_signature` (X-Hub hex over raw UTF-8 body): verified over RAW body bytes (re-serialised dict FAILS); OR-accumulated constant-time compare over all candidates (rotation); empty/unset secret → fail-CLOSED 401; tampered body / missing headers / no-v1 / bad-base64-secret → 401 (never a `binascii` crash); WIRED AHEAD of the durable `webhook_events` insert (a forged delivery can't dedupe-poison the table nor trigger a rebuild).
- [INTERNAL]‹P0› safe_error — a bare Exception → 500 "Service temporarily unavailable" NEVER leaks `str(exc)`/stack/DB/table; `RequestValidationError` → 422 with the caller's own bad input only; unknown status → generic; `_jsonable` coerces bytes/ctx so the handler never itself raises; all four bindings installed (override Starlette's `{"detail":...}`).
- [INTERNAL]‹P0› channel_action handler — a structurally-sound frame is a no-op SUCCESS; ANY exception inside → caught → `"Not found"` (RETURNS, never raises — §14 tool-handler never-throw); contract still holds when the real service is bound via `register_handler(..., replace=True)`.
- [INTERNAL]‹P0› contracts registry — `assert_registry_closed` four-part closure: (1) MessageType enum == CHANNEL_REGISTRY keys; (2) every inbound has EXACTLY one handler; (3) every outbound ≥1 projector; (4) no SIGNAL_SURFACE_EVENTS name leaked into the registry; each violation is a distinct AssertionError; called at boot + CI (`check_field_contract`); duplicate `type` → ValueError at import.
- [INTERNAL]‹P0› contracts field-diff — `assert_contract_fields_consumed` names every produced-but-unconsumed field (minus empty allowlist) AND every consumed-but-never-produced field (a rename's old half — NEVER allowlisted); AgentChunk consumer reads AST-swept from live source (a `.type`→`.kind` rename fails the diff); fail-soft on a deployed wheel (no source tree → whole-wire frames only, never a crash-import).
- [INTERNAL]‹FIX-14› contract_reads — the sweep root is `__file__.parents[4]`; if the repo layout moves, the sweep silently returns {} → the field-diff passes VACUOUSLY. Assert a non-empty AgentChunk consumer set is found on the real tree (vacuous-pass regression guard).
- [INTERNAL] contracts channel — `ChannelAction` non-UUID meeting_id / over-2000 `arg` → ValidationError before any DB lookup; `surface` excludes "tile"; `action` a closed 9-value Literal; outbound frames cap `chunk≤8000`/`patch≤100000`/`line≤200`/`text≤8000`; `TileState` EXACTLY the 8 §2.2 states (a 9th → ValidationError); `DraftCard` links by draft_id (no raw URL — Law 3).
- [INTERNAL] contracts chunks — `AgentChunk.text` is `str|None` (SEALED — can't narrow to `str=''` without editing a sealed test); `ChunkType` 6-member Literal; `AGENT_CHUNK_METADATA_KEYS` per-variant; RESULT carries `total_cost_usd` = the same figure call_external would meter (cost consistency); fresh metadata dict per instance.
- [INTERNAL] contracts data — Bundle carries `notes_ref` UUID handle (no notes body — truth-is-live) + `transcript_tail: str`; `MaterialChangeKind` closed 7-member StrEnum (no 8th/combined); `NoteDelta` op Literal add/patch/close; `Readiness` excludes "mapping"; these four are plain BaseModel (never in the WS closure).
- [INTERNAL] package import — importing `contracts` self-registers every ProxyMessage before closure can run; `collect_consumed_fields` in a try/except at import (a sweep failure never breaks import); `libs/http`→`contracts` is the ONE cross-lib dep (no cycle).

## 1.4 Sandbox/ops seams (libs/ops)

- [INTERNAL]‹P0› sandbox — `provision_sandbox` mints a ~43-char URL-safe `secrets.token_urlsafe(32)` jwt_secret; two calls for the SAME (tenant, meeting_id) mint DIFFERENT secrets AND ids; frozen dataclass; over 10k mints all unique (no shared secret ever).
- [INTERNAL]‹P0› sandbox_provider — `secret_for` per-id only (host owns `_SECRET_BY_SANDBOX`); NO fleet-shared secret constant anywhere (grep-assert); a token minted for sandbox A NEVER verifies against B's secret (cross-sandbox forgery test).
- [INTERNAL] sandbox_provider — provision idempotency (§3.9): re-provision of a live meeting returns the SAME handle with its already-minted secret; provision after destroy mints a FRESH distinct secret + resets the age clock + un-ends the meeting; a stale not-alive entry forces a fresh provision.
- [INTERNAL] sandbox_provider — destroy idempotent (drops the secret — dies with the sandbox; 404-tolerant; no un-awaited-coroutine ResourceWarning); `heartbeat_bump` extends STRICTLY beyond `timeout_s` (a silent long build isn't reaped; adversarial re-set-same-deadline caught); `ensure_running` fast-path (no claim when healthy) / cold / contended (N concurrent → exactly ONE provision, losers get the winner's handle) / winner-errored fallthrough.
- [INTERNAL] sandbox_provider — `reconcile_sandboxes` reaps every orphan/past-TTL sandbox (idempotent; exactly-at-TTL not reaped; unknown-start not reaped; ended-meeting reapable under TTL); a survived-close sandbox IS reaped (cost backstop); NO registry table / warm pool / FSM.
- [INTERNAL]‹P0› _RealE2BBackend — every op routes through `http.call_external(service="e2b")` (retry+cost); the SOLE E2B construction site is `libs/http`; `create` threads per-sandbox `JWT_SECRET`+`SESSION_ID` into `envs` (never logged); `network=` egress kwarg threaded ONLY when provided (default-DENY requires the caller to pass it) — a non-allowlisted host must be unreachable (Phase-3 live, host-side threading asserted now).
- [INTERNAL]‹P0› claim — `claim_meeting` ON-CONFLICT-DO-NOTHING on the partial unique (scope_id, operation_type WHERE status='running'); exactly one of N concurrent claimers wins; a non-running row never blocks re-claim; `sweep_stale_on_read` flips stale running→interrupted (crash-recovery); `MIN_REAPER_RATIO` ≥3× enforced fail-closed; the ONLY coordination primitive is Postgres (no broker/in-mem lock); `with_meeting_lock` advisory-lock serialises same-key, concurrent different-key.
- [INTERNAL]‹P0› operation_run — `with_operation_run` claims→heartbeats→completes; exception → failed + re-raise; already-owned → RuntimeError naming scope+op; `heartbeat()` is a FENCE (0-row update → is_owner=False → the zombie self-terminates and emits NOTHING — split-brain guard); `_finish`/`_cancel` gated on status='running' (a finalizer never clobbers a re-owned row); BaseException/CancelledError still finalizes failed + no leaked heartbeat task.
- [INTERNAL] reconcile — async 3-step sweep (stale-harnesses→meeting-sandboxes→notes-retention) per-step isolated (a raising step captured in `errors`, never aborts the rest); idempotent; `_step_notes_retention` intentional V0 no-op; sync token-gated `/internal/reconcile` refuses without a valid `hmac.compare_digest` token (constant-time), dev-literal never reaches prod.
- [INTERNAL]‹P0› reconcile offboard — deletes an offboarded tenant's rows across EVERY tenant-scoped table (dynamic `information_schema` discovery, `psycopg.sql.Identifier` no-injection, `::text` compare) + `gcs.delete_prefix("tenants/<tenant>/")`; AFTER offboarding A, NO A row/object remains in ANY table and B is untouched; idempotent; a live sandbox for an offboarded tenant is also reaped (no compute residue).
- [INTERNAL]‹P0› capability — `mint_capability_token` a frozen dataclass (no jti/opaque blob — checked STRUCTURALLY); `authorize` grants ONLY `notes:read` on its own meeting and REFUSES `draft:accept` + every world-touching action (Law 3); fail-closed order signature→revocation→expiry→meeting→scope; HMAC over the whole body incl. epoch; per-process random signing key (no shared constant).
- [INTERNAL]‹P0› capability — cross-import-identity sentinel: a token minted via `ops.capability` authorizes/revokes via `libs.ops.src.ops.capability` (mint on close-line path, verify on read route); if the shared-state sentinel breaks, a valid token is wrongly refused or a revoked one wrongly honored.
- [INTERNAL] capability — per-token revocation persists process-life; per-meeting epoch bump mass-revokes pre-bump tokens for THAT meeting only (B untouched), fresh post-bump mint honored, monotonic; wrong-meeting → refused; expired (>= boundary) → refused; `decode_capability_token`/`verify_capability_token` NEVER throw on a hostile string (return None → route falls to 404/session).
- [INTERNAL]‹P0› logging — `_scrub_source_processor` redacts raw customer source (def/class/import/return + everything after, DOTALL multi-line) → `[redacted-source]` before render; non-str values untouched; benign strings pass; the per-sandbox secret never logged. ‹FIX-20› source not starting with the 4 markers (bare expression/docstring) may slip through — test against real cal.com samples.
- [INTERNAL] guards — naming lint (no Orchestrator/Scribe/workroom in user-visible sinks, word-boundary case-insensitive, docstrings skipped) shares the ONE `is_user_visible_sink` classifier with copy_guide (voice/honesty shapes, banned as-an-ai/filler/exclamation, seed-JSON single-source); `check_banned_strings` dead-token resurrection guard; `check_sdk_isolation_triad` (bare `query()` needs all 3 markers); `check_field_contract` imports the real closure; `check_secret_bindings` Terraform↔deploy drift (directional; INTERNAL_RECONCILE_TOKEN + sandbox secret covered).

## 1.5 The meeting engine — hear / speak / channels / loop (services/in-meeting + control-plane reactive loop)

### Transport in (hear)
- [BOTH] recall.join — `join(link)` posts `/bot`, returns the launched bot id; a `/bot` response with no `id` raises "no bot launched" (never a shared/placeholder id — P0 isolation); `bot_name="Proxy"` so Recall labels Proxy's own speech "Proxy" (the self-wake filter); AssemblyAI BYOK rides empty (no credential in the body); no `webhook_url` → no recording_config.
- [LIVE] hear+transcribe — a bot speaks; the line appears in Proxy's transcript via real STT, words correct incl. code terms; the transcript accumulates and is later recalled unprompted (was-in-the-room).
- [INTERNAL] webhooks drain — `_transcript_body` unwraps up-to-3 nested `data` envelopes; `_transcript_text` handles words-as-string (stub) / words-as-list (real AssemblyAI) / text/transcript fallback; `_chat_line` sender+text extraction; `_bot_id` flat/nested/top-level; `get_by_bot_id` resolves the meeting server-side (unknown bot → safe no-op); a row is marked processed even on a no-op / caught fault (never a poison queue); chat feeds `is_chat=True` (@proxy rule, not the voice rule).
- [BOTH] meeting_session wake gate — voice `\bproxy\b` (case-insensitive) wakes; chat requires `@proxy\b`; `speaker=="Proxy"` never wakes (self-wake suppression); a non-address line still appends to the transcript + feeds `feed_transcript`. (LIVE: Scenario groups 4, 5.)

### Speak (voice out)
- [LIVE] speak-back — Proxy replies via Cartesia, audible/gapless/natural in the room; first audio at the first clause (streaming), not the whole answer.
- [INTERNAL] speak/SpeakPipe — sentence buffering on `.!?`+ws, trailing partial flushes after 0.5s quiet; ONE synth in flight (FIFO, no garbled interleave); `commit_tail` closes this turn's utterance so a next turn can't concatenate; speaking-state fires before first audio / clears only when idle (orb never flickers); 2-byte s16le alignment (odd byte carried, final dangling byte zero-padded).
- [INTERNAL]‹FIX-18› speak — TWO sentence splitters exist (`speak._TERMINATORS`=`.!?` vs `session_host._TERMINATORS`=`.!?;…`); verify the live relay path (session_host→relay→connection.say) and the file/replay path each produce natural in-order audio (splitter divergence).
- [INTERNAL] tts — `synthesize` streams the EXACT verbatim text (no headline substitution), s16le/16kHz/mono; one configured voice for all synthesis; `register` never rides the wire; re-framed to ≤tts_chunk_ms chunks (a surviving chunk can't defeat barge-in); key never logged/in-body; `call_external=None` → clean empty degrade.
- [BOTH] output_media — orb webpage + WS PCM feed: bounded 256-frame deque (drops oldest atomically, live audio never stalls); ordered bytes+state frames (orb sync); late-attaching page gets the retained tail; latest-attach-wins; s16le/16kHz gapless playback (SAMPLE_RATE must match `tts._SAMPLE_RATE_HZ`); a hostile meeting_id can't smuggle `</script>` (XSS guard). (LIVE: audio is heard, orb pulses.)

### Channels + human-control (the to_meeting interface)
- [BOTH] meeting_connection.to_meeting — routes say/chat/dm/screen/offer/mute (+ aliases); medium normalized (empty→say); unknown medium → speaks + honest detail (never drops words); dm-without-`to` / screen-sink-None / offer-sink-None → honest ok=False; every send appended to `self.sent` (host audit); ANY sink raise → `MeetingSend(ok=False)` NEVER re-raises (tool-handler never-throw). (LIVE: Scenario group 8.)
- [LIVE] chat (broadcast) — posts to meeting chat, correct content appears.
- [LIVE] dm — direct message delivered to the right participant only (Zoom per-participant; honest "everyone" degrade elsewhere, never a fabricated private send).
- [LIVE] screen-share — shows an artifact on screen; visible, correct, readable.
- [BOTH] mute/unmute — silences the webpage PCM channel FIRST then the Recall flag (human hears silence immediately — Law 3); while muted every write_audio is dropped, state frames kept; idempotent; the mute flag lives on the transport so a sink made before mute still honors it. (LIVE: audio actually stops/resumes on request.)
- [BOTH]‹P0› offer (human-control) — a world-touching change is staged as a draft behind a click (`propose_change` → approve URL posted to chat); applies ONLY on click; NEVER pushes; empty approve_url → no chat spam. (LIVE: the card is posted and applies only on click — Scenario group 8/10.)

### Barge-in / self-echo (the reflexes)
- [BOTH]‹P0› barge-in — a human line ≥2 tokens while `connection.speak.speaking==True` → `connection.barge_in()` → raises the cut latch AND awaits `speak.cut()` (drops buffered text, queued sentences, in-flight synth; speaking→False) within `barge_in_budget_ms`(200); the latch silences only the interrupted turn's remaining voice (chat/dm after still land); `begin_turn` lowers it; `barge_in` never raises even if cut faults; idempotent. (LIVE: speech stops fast — Scenario group 4.)
- [BOTH]‹P0› self-echo suppression — Proxy's own voice on a no-headphones mic (≥4 tokens, ≥0.7 containment, 45s window vs `connection.spoken`) is relabeled to Proxy → never re-wakes/interrupts itself; only the `say` channel records to `spoken`; a barge-dropped say doesn't record; `spoken` bounded to 64. (LIVE: never self-wakes — Scenario group 5.)

### The warm session + wake driver (the reactive loop core)
- [INTERNAL] session_host — ONE persistent `ClaudeSDKClient` per meeting (warm turn ~1-3s not cold spawn); spoken prose streams sentence-by-sentence; captures tools/cost/turns/ttft/deliver_at; thinking never streamed/spoken; pre-tool prose force-flushed (opener heard NOW); `_write_result` atomic temp+rename; `_parse_intents` byte-identical to workroom's; heartbeat rewrites `_host.ready` concurrently (a working turn keeps mtime advancing; SIGKILL → stale in seconds).
- [BOTH] session_host opener — ONE canned "On it — give me a moment." fires only when the model committed to real work (first non-to_meeting tool) AND stayed silent 2.0s past it (or a 15s no-tool hard floor); the model's OWN opener always wins (atomic check-then-set); NEVER fires on a turn that then chooses SILENCE (cross-talk) — the historical spurious-opener bug is fixed by the tool-gate; opener text generic (Law 4). (LIVE: an ack ≤~2.5s before digging in; no spurious "On it" on a silent decline — Scenario groups 3, 5, 6.)
- [INTERNAL] session_host config — `permission_mode=bypassPermissions`, `setting_sources=["project"]` (loads CLAUDE.md once), fixed EFFORT (byte-identical flags → cache warm), MODEL=Sonnet default (Haiku over-explores), `include_partial_messages=True`; TOOL-WORKSHOP: tools/allowed/disallowed UNSET (full native toolset — `[]` disables all); ONLY credential in the sandbox is `CLAUDE_CODE_OAUTH_TOKEN`; `meeting` MCP `alwaysLoad:True` (to_meeting never behind ToolSearch); Context7 only when `CONTEXT7_API_KEY` set (egress-gated).
- [INTERNAL] workroom provision — provisions/seeds the E2B sandbox (repo, `compose_resident_prime(prime, map_text)` as CLAUDE.md, REPO_MAP.md, empty MEETING_NOTES.md, the MCP server + `.mcp.json` named `meeting`); pip+npm installs SERIALIZED (concurrent OOMs the ~478MB base) + `&&`-chained (no half-provision); sha=None → depth-1 else full+checkout `|| true`; `resume_id` reconnects a paused snapshot (~1s), a resume fault degrades to fresh cold; the ONLY injected credential is the OAuth token; every E2B round-trip rides `call_external`.
- [INTERNAL] workroom run_ask — ONE warm delivery path; a warm miss restarts the host ONCE then honest `error`; a genuine drain CancelledError re-raised (prompt shutdown), a spurious transport cancel absorbed into `error` (WS6: never crashes the loop); NEVER raises; dead-host watch (heartbeat frozen for 20s → abort fast, not the 900s ceiling); adaptive poll backoff (0.25/0.5/1.0s); corrupt-result/WAKE_IN-fault → None → restart-retry.
- [BOTH] wake prompt — recent transcript INLINED (no MEETING_NOTES read to judge); JUDGE-IF-ADDRESSED (stay COMPLETELY silent on incidental "proxy"/cross-talk — a spoken "not addressed" is itself an interruption); DELIVER-IN-ONE-TURN (never "I'll bring it back" then nothing); NO-OVERSTATE (never "I already did this"/"as I showed earlier"); the ONE exception = a genuine blocker → ask ONE crisp question and stop; world-touching → real artifact + `medium='offer'`. (LIVE: Scenario groups 6, 7, 10.)
- [INTERNAL] sandbox_meeting_mcp — exactly one `to_meeting(content, medium="chat", to="")` tool; proof mode appends JSONL / live mode POSTs to the relay; docstring says SPEAK by writing (tool only for chat/dm/screen/offer/mute); relay bearer `PROXY_MEETING_TOKEN` never logged; a relay POST exception → local `relay_error` line + honest tool result (never crashes the turn); 15s urlopen timeout; recorded JSONL shape == `_parse_intents` reads.
- [INTERNAL] prime — `WORKROOM_PRIME` lean + byte-stable (a byte change invalidates the resident cache → latency regression); names the tool by exact `mcp__meeting__to_meeting` ("already loaded, don't search"); encodes ground-or-silent, verify-or-say-you-couldn't, offer-is-delivery; `render_meeting_info` renders `- Name (id: <pid>)` (DM uses the id, never the name), no-metadata → honest placeholder.

### The reactive session + runtime lifecycle (control-plane)
- [BOTH] meeting_session dispatch — a wake is a BACKGROUND task in `_inflight` (the room keeps flowing while Proxy works — monitor-while-working); `result.sent` non-empty → FILE-mode replay each intent; empty+error → ONE honest degrade say; empty+no-error → RELAY mode (already responded) or cross-talk silence (both stay silent); `result.sent` keyed per-wake (concurrent wakes never drop each other); the degrade line spoken only when nothing was recorded. (LIVE: Scenario groups 1, 6.)
- [BOTH] meeting_session ASK→ANSWER→CONTINUE — a delivered question sets the pending latch; the next substantive (≥2 token) human line → continuation wake WITHOUT a name mention; expires after 180s; explicit address supersedes/clears the latch. (LIVE: Scenario group 6.)
- [INTERNAL] meeting_runtime — registry register idempotent; `end_meeting` ordered teardown (cancel keepwarm FIRST → drain session → close speak_pipe → teardown workroom → close output_media → drop) each bounded by TEARDOWN_TIMEOUT_S(30); every step's raise/timeout suppressed (teardown never blocks); idempotent second `end_meeting`; total ≤3×30s must fit Cloud Run SIGTERM grace.
- [INTERNAL]‹P0› relay route — `POST /meetings/{id}/relay` authenticates the per-meeting bearer with `hmac.compare_digest` (constant-time); `_expected_token` traverses runtimes→workroom.relay_token, fails CLOSED to "" if any hop is None; wrong/empty bearer → 401; unknown meeting → 404; `to_meeting` raise → 200 `{ok:false}` (the in-sandbox caller must report back, never mishandle a non-2xx); medium defaults "say"; a relay racing teardown → 404 not a crash.
- [INTERNAL] provisioner — `provision_meeting` atomically claims (INSERT ON CONFLICT via the partial index) — exactly one winner, redelivered `in_call` for a registered meeting → claimed=False without a DB hit; relay_token = `secrets.token_urlsafe(32)` minted at join, stashed on workroom AND passed to the sandbox env; the sandbox holds NO push/send creds; keep-warm extends every interval (cancelled at end, CancelledError re-raised); every honest-degrade branch (missing OAuth token / no bound repo / provision raise / offer/screen/mute/map-text fault) boots the meeting anyway (voice, no brain) — never a webhook 500.
- [INTERNAL] server boot — deterministic order tracing→pool→database→provisioner_ready→reaper→routers (routers STRICTLY after the reaper; `provisioner_ready` gate before any webhook handler); `_real_reaper` calls `assert_reaper_ratio()` (boot fails on unsafe config); `reap_orphans` marks running/in_meeting → interrupted (idempotent); webhook drain every 0.25s; graceful drain (SIGTERM → begin_drain, meeting continues to DRAIN_GRACE_S=300, then os._exit); EPIPE swallowed, unknown crashes.

## 1.6 Draft lifecycle + agent execution layer (services/workroom + libs/agentkit)

- [INTERNAL]‹P0› propose_change/objectstore — GCS write (`objectstore.put`) precedes the DB row insert (artifact durable before the row); production path `gs://proxy-drafts/{meeting_id}/{uuid}` (tenant-isolated); accept reads from durable storage, never the in-memory session.
- [INTERNAL]‹FIX-4› propose_change — GCS-put-before-DB-insert means a crash/DB-error between the two ORPHANS the GCS artifact (draft_id never queryable, object never cleaned up). Test partial-failure recovery + that no accept path reads an orphaned artifact. (Present on both sync `_persist_bundle_row_sync` and async `_propose_change_async`.)
- [INTERNAL] drafts helpers — `_normalize_files` rejects non-dict/no-`path` (ValueError), records `original_from="meeting.pinned_sha"` when old_sha absent; `_build_bundle` files XOR unified_diff (both-provided is underspecified — verify no silent corruption); empty files AND no diff → ValueError; path-traversal `path` stored verbatim (accept-stage must reject — verify no traversal reaches the FS).
- [INTERNAL]‹P0› disposition isolation — `mcp_servers_for_disposition` returns the propose_change server ONLY for "worker", `{}` for every other/unknown disposition; adversarially verify that even with a misconfigured `ProviderQuery.mcp_servers` on a non-worker disposition, the SDK-level `disallowed_tools` blocks the write (both the MOUNT decision and the BLOCK decision independently).
- [INTERNAL]‹FIX-5› accept parity — `workroom.accept_draft` has NO idempotency guard while `control_plane.accept.apply_accepted_draft` short-circuits on status in (applied, rejected); two parallel accept paths share objectstore but differ in idempotency — verify consistency (a double-accept on the workroom path re-flips).
- [INTERNAL] accept/reject route — dual-layer auth (protected() + own-session/CSRF-double-submit/tenant); `authorize_draft_accept` runs BEFORE apply (CSRF first, then tenant, then the DB query); durable idempotency (deterministic UUID5 accept_id, cross-instance replay → already_applied); reject can NEVER un-apply an accepted draft; code-change NEVER pushes (pushed=False, bundle_url is a download handle); audit fires exactly once per real apply; concurrent accept race → first writer wins, second sees already_applied.
- [INTERNAL] recovery — `_has_deliverable` truthy-dict-key check; `should_restart`(sync)/`recover_task`(async) read `operation_runs.result_ref` ORDER BY started_at DESC LIMIT 1; no-row/no-deliverable → restart; `WORKROOM_OP_PREFIX="workroom:"`; recover only returns a verdict (caller restarts).
- [INTERNAL] deltas — `_DeltaState.feed` per-msg_id suffix delta-izer (non-TEXT pass-through); first chunk = full text, subsequent = suffix; `chunk.text=None`→""; missing/empty msg_id collides in `_seen` (verify upstream always sends a valid msg_id); non-idempotent by design (never applied twice); `_seen` grows unbounded (acceptable for bounded meetings); sync-in→sync-out, async-in→async-gen-out.
- [INTERNAL]‹P0› guardrails — `injection_guardrail_suffix` is the ONE definition of the injection guardrail; `with_injection_guardrail` appends it LAST (final authoritative); no per-service redefinition (grep-assert the marker appears once); `with_proxy_guardrails` is a separate behavioral (tone) guardrail that composes.
- [INTERNAL] provider seam — `ProviderQuery` pins `strict_mcp_config=True`, `setting_sources=()`, `disallowed_tools` merged with module-level SDK_LOCAL_TOOLS as defensive defaults; `compute_builtin_tools` ALWAYS returns () (no host built-ins in sandbox — the curated set flows via allowed_tools); `thinking_policy` enables thinking only for {grounded-answer, plan-artifact, build-planning} on claude-opus (budget 3000); `pick_provider` KeyError before any registration (boot must guard).
- [INTERNAL]‹P0› sdk_provider — `build_sdk_options` is the ONLY ClaudeAgentOptions construction site with the isolation triad pinned (`strict_mcp_config=True`, `setting_sources=[]`, `permission_mode="bypassPermissions"`, merged disallowed_tools); `compute_builtin_tools()==()` → no host Read/Grep/Bash reaches a sandboxed call (belt: disallowed_tools backstop); no call site passes `tools=SDK_LOCAL_TOOLS`.
- [INTERNAL] sdk_provider stream — drives `sdk_query`, yields normalized AgentChunks; ANY SDK exception → a terminal ERROR chunk (NEVER raises — in-band); auth threaded onto `merged_env` (auth wins on key collision) without mutating the process env permanently; SDK crash/timeout mid-stream → ERROR chunk (recovery is the caller's via resume_with_fallback); `make_map_provider` auth priority api_key > auth_token > oauth_token > vertex-ADC, no-auth → None; secrets flow through auth_env only (never logged, verified at DEBUG).
- [INTERNAL]‹FIX-10›‹FIX-12› resume/abort (git-tracked, deleted in the pivot — kept for review) — `resume_with_fallback` checks `getattr(abort, "aborted", False)` (duck-typed Any: a None/wrong object silently returns False → the Law-3 human-control gate is BYPASSED, a killed build can be resurrected); `AbortController` is per-event-loop (unsafe across threads); JSON-truncation vs stale-session classifier ordering could retry a stale session twice.

---

# 2. P0 MUST-PASS — the non-negotiables for deployability

If ANY of these fails, Proxy is not customer-deployable. All are INTERNAL or BOTH (a deterministic
assertion on real substrate), because a P0 is exactly the class of failure a happy-path meeting
would not surface. The LIVE meeting confirms the human-facing half of the BOTH items.

## Tenant isolation (a cross-tenant read = P0)
1. **repo_maps PK isolation** — `load_map(tenantB, repo, sha)` can NEVER return tenantA's row at identical (repo, sha); tenant_id is first in the PK. `[map_store]`
2. **repo existence non-leak** — `get_repo_for_tenant` returns None for another tenant's repo EXACTLY like a nonexistent one (tenant B cannot detect A owns "org/repo"). `[db.meetings]` / `[POST /meetings → 404]`
3. **authoritative tenant from bot_id** — `get_by_bot_id` returns the tenant every downstream write is scoped to; a webhook can never cross into another tenant's repo. `[db.meetings]`
4. **unscoped-by-id read audit** — every `get_by_id`/`get_repo_by_id`/`get_draft`/`get_session`/`get_cost`/`get_operation_run`/`load_deltas`/`count_segments` call site has proven the id belongs to the caller's tenant upstream (server-side). `[db — global audit]`
5. **note-plane meeting scoping** — `load_deltas`/`count_segments`/`pending_segment_ids` never return another meeting's/tenant's rows. `[db.notes]`
6. **funnel meeting/entity isolation** — a foreign `meeting_id` and a smuggled foreign `canvas_id`/`artifact_id` both → generic "Not found" (no tenancy oracle, both gates independent). `[http.dispatch]`
7. **server-resolved connection tenant** — `authorize_upgrade` and `protected()` set tenant_id from the signed session, never a client field; `public()`'s nullable tenant can't become a query filter. `[http.gateway / registry]`
8. **entity-tenant resolution never leaks a foreign id** — `resolve_entity_tenant` DENY returns `tenant_id:None` even when a foreign tenant was resolved. `[http.dispatch]`
9. **tenant offboarding purges everything** — after offboarding A, NO A row remains in ANY tenant-scoped table AND no A object under the GCS prefix AND any live A sandbox is reaped; B untouched; idempotent. `[ops.reconcile]`
10. **schema tenant-reachability** — every durable tenant-scoped table has a path to `tenants` (AC-TEN-001 audit); `operation_runs` deliberately has none. `[migrations]`
11. **per-meeting sandbox / channel / relay-token / Recall queue** — no shared placeholder bot id, no output-media channel leak, no relay-token reuse across meetings. `[in-meeting / provisioner]`
12. **dual-tenant integration battery** — seed two full tenants; run EVERY read as B against A's ids; zero cross-tenant rows returned anywhere. `[db — end-to-end]`

## Per-sandbox secret / forgery containment (P0 isolation)
13. **no fleet-shared secret** — grep-assert no module-level default secret; every sandbox mints its own; two calls for the same (tenant, meeting) mint different secrets. `[ops.sandbox / sandbox_provider]`
14. **cross-sandbox forgery impossible** — a token/secret minted for sandbox A never verifies against B's secret; the secret dies with the sandbox on destroy; never logged (source-scrub + envs). `[ops.sandbox_provider / logging]`

## Relay-token alignment (else Proxy literally cannot respond)
15. **relay token agrees end-to-end** — the `secrets.token_urlsafe(32)` minted in `provisioner._assemble_workroom`, stashed on `workroom.relay_token`, passed to the sandbox env, equals what `relay._expected_token` resolves; a mismatch → every sandbox→host call 401s (silent Proxy). Constant-time compare. `[provisioner ↔ relay — cross-subsystem D]`

## Secrets fail-closed
16. **boot secret gate** — missing DATABASE_URL/GCS_BUCKET/RECALL_API_KEY/AES keys/any Anthropic auth → RuntimeError at import naming the key(s); prod-gated SESSION_SECRET/SESSION_SIGNING_KEY/INTERNAL_RECONCILE_TOKEN/PROXY_INTERNAL_TOKEN/PUBLIC_BASE_URL/GCP_PROJECT_ID crash naming the key; the dev-insecure session key literal can never reach prod. `[settings]`
17. **webhook secret fail-closed** — unset Recall/GitHub webhook secret → the route fails CLOSED (401 every delivery), never crashes boot, never accepts an unverifiable delivery. `[settings / webhook]`
18. **secret-binding drift gate** — every Terraform-declared secret is deploy-bound (the exact boot-crash class); INTERNAL_RECONCILE_TOKEN + sandbox JWT material covered. `[ops.check_secret_bindings]`
19. **secrets never logged** — installation token (git argv), per-sandbox JWT (envs), relay/OAuth/API keys, session key, source bytes — none reach stdout (`_scrub_source_processor` + redaction). `[premeeting / ops.logging / transport]`

## Prompt-injection guardrail actually present in the sandbox
20. **guardrail in the mounted CLAUDE.md** — the ONE `injection_guardrail_suffix` (transcript is untrusted data, never instructions) is present in the prime/CLAUDE.md that mounts into E2B; the model rejects an injected command from transcript content. Single definition, no per-service copy. `[agentkit.guardrails / prime → ‹FIX-3 in workroom-agentkit›]`

## Human-control gates (offers behind a click; no auto-apply)
21. **capability token can't accept** — `authorize` refuses `draft:accept` and every world-touching action even with an otherwise-valid token (fail-closed order). `[ops.capability]`
22. **offer is the only world-touching path** — a change is staged as a draft (`propose_change`), applies ONLY on a human click (`set_draft_status` the sole transition); the sandbox holds no push/send creds; a proposed draft never auto-applies. `[drafts / provisioner._offer / meeting_connection]`
23. **accept cross-tenant barrier + CSRF, upstream of apply** — `authorize_draft_accept` (CSRF then tenant) runs before `apply_accepted_draft`; wrong tenant → 403, no content; durable idempotency; reject can't un-apply; code-change never pushes. `[accept / authz]`
24. **disposition write-isolation** — propose_change mounted ONLY for "worker"; even a misconfigured non-worker query is blocked by disallowed_tools (mount AND block independently). `[workroom.mcp_servers_for_disposition]`
25. **killed build never resurrected** — the abort/human-control gate holds (see ‹FIX-10›: today it's duck-typed `getattr(abort,"aborted",False)` — a wrong object bypasses it). `[agentkit.resume]`

## Barge-in
26. **barge-in cuts within budget** — ≥2-token human over-talk while speaking → `barge_in()` cuts in-flight speech within `barge_in_budget_ms`(200) across session→connection→speak→channel; the latch silences only the interrupted turn; never raises. `[BOTH — meeting_session/connection/speak/output_media]`

## Self-echo suppression
27. **own voice never re-wakes/interrupts** — a no-headphones echo (≥4 tokens, ≥0.7 containment, 45s) is relabeled to Proxy; only `say` records to `spoken`; a barge-dropped say doesn't record. `[BOTH — meeting_session ↔ connection]`

## Honest degrade (never fake, never crash, never silent)
28. **every dependency fault degrades honestly** — Recall/Cartesia/E2B/Anthropic/relay error/timeout/cancel/malformed line/OOM/dead-host/corrupt-result → an honest `WorkroomResult.error` or a no-op, NEVER a fake success and NEVER a crash of the meeting loop; a needed response is met with the answer or ONE honest degrade line, never total silence. `[in-meeting — subsystem invariant]`
29. **tool handlers return errors, never throw** — `to_meeting`, `channel_action`, `propose_change` tool, relay route, repo_context tools all return an error result instead of raising. `[in-meeting / http / workroom / premeeting]`
30. **join/consent honesty** — a join failure returns `joined=False` (never a false "joined"); consent is a hard gate posted as the first observable action; a consent-post failure halts honestly (never a false "posted"); `ConsentGate` starts CLOSED. `[transport.join / consent]`

## Coordination / substrate correctness (double-free + closure are P0)
31. **reaper ratio enforced at boot** — `assert_reaper_ratio()` (STALE_AFTER_S ≥ 3× HEARTBEAT_S) is called on every reaper's boot path; a booting instance NEVER reaps a fresh row (D-033 double-free guard). `[db.config / database / ops.claim / server boot]`
32. **exactly-one claim + ownership fence** — the partial unique index yields exactly one claim winner per (scope, op); `heartbeat()` fences a reclaimed owner (0-row → is_owner=False → the zombie emits NOTHING — split-brain guard); a finalizer never clobbers a re-owned row. `[ops.claim / operation_run / dispatch is_owner]`
33. **contracts closure green** — `assert_registry_closed` (enum⇔registry set-equal, one handler per inbound, ≥1 projector per outbound, no signal-event leak) + `assert_contract_fields_consumed` (no produce/consume drift, including the consumed-but-never-produced rename half) pass at boot + CI; `register_handler(replace=True)` keeps exactly-one-handler. `[contracts.registry / check_field_contract]`
34. **single external seam** — `check_call_external` green: no raw vendor client (incl. E2B create/connect) outside `libs/http`; the retry+cost wrapper is unbypassable. `[ops.check_call_external / all external callers]`

---

# 3. FIX-FIRST — every real bug / gap / doc-vs-code drift the readers surfaced

Do these before trusting any LIVE or INTERNAL result on the same path. Each: what it is · file/subsystem · why it matters.

**FIX-1 — Private-repo comprehension has no auth-token injection (empty understanding for private repos).**
`comprehension.py` `_run_in_sandbox` setup uses `shlex.quote(repo_url)` with NO token — the in-sandbox `git clone` of a private repo fails → empty Part-2 → the customer's private repo onboards with only the deterministic Part-1 map and NO holistic understanding.
*File:* `services/premeeting/src/premeeting/comprehension.py` (+ `map_build.build_understanding_map` repo_url path).
*Why:* Private repos are the customer case; the "it already knows your codebase" promise silently degrades to Part-1-only with no signal.

**FIX-2 — No try/except around the comprehension build → no readiness signal on crash.**
`build_understanding_map` and `pipeline.run_pipeline`'s map-build stage have NO try/except; an exception escaping `build_comprehension` (import error, structural fault) bubbles uncaught out of the background connect trigger.
*File:* `services/premeeting/src/premeeting/map_build.py`, `pipeline.py` (INTEGRATION-11).
*Why:* The user sees NO readiness verdict and NO named reason — a silent dead connect. Everything else in the pipeline returns an honest `not_ready`; this one hole breaks that contract.

**FIX-3 — Prompt-injection guardrail possibly absent from the sandbox's mounted CLAUDE.md.**
The old host-side `with_injection_guardrail` call is gone with the workroom pivot; the guardrail must now live in the E2B sandbox's `CLAUDE.md`/prime. If it isn't in the mounted prime, transcript content is treated as instructions.
*File:* `libs/agentkit/src/agentkit/guardrails.py` (definition) ↔ `services/in-meeting/src/in_meeting/prime.py` / the composed CLAUDE.md.
*Why:* Critical security failure (Law: transcript is untrusted data). This is also P0-20 — verify the guardrail text is present in the mounted CLAUDE.md and the model rejects an injected command.

**FIX-4 — Orphaned GCS artifact on DB failure (put-before-insert).**
`objectstore.put` writes GCS BEFORE the `staged_drafts` row inserts; a crash/DB error between them orphans the artifact (draft_id never queryable, object never cleaned).
*File:* `services/workroom/src/workroom/drafts.py` (`_persist_bundle_row_sync`, `_propose_change_async`).
*Why:* In real GCS this wastes storage undetectably and needs a reconciliation job; test partial-failure recovery + that no accept path can read an orphaned artifact via a stale ref.

**FIX-5 — Two parallel accept paths with divergent idempotency.**
`control_plane.accept.apply_accepted_draft` short-circuits on status in (applied, rejected); `workroom.accept_draft` has NO idempotency guard (re-reads, re-flips to 'accepted' every call).
*File:* `services/workroom/src/workroom/drafts.py` vs `services/control-plane/.../accept.py`.
*Why:* Inconsistent behavior on a double-accept; the world-touching path must be uniformly idempotent.

**FIX-6 — Refresh-on-push drops to Part-1-only.**
`refresh_on_push` re-builds the deterministic symbol map only — the Part-2 holistic understanding is NOT re-run (call/token not threaded).
*File:* `services/premeeting/src/premeeting/refresh.py`.
*Why:* After any push, a meeting gets orientation but loses the qualitative depth until a full re-connect — a design trade-off that must be confirmed acceptable and surfaced (the "it knows your codebase" quality silently thins after a push).

**FIX-7 — Cost writers may target the wrong table.**
0007 created `meeting_cost_telemetry` distinct from `meeting_cost`; a writer hitting the wrong one silently loses spend so a recycled orchestrator reloads a wrong total.
*File:* `libs/db/.../cost.py` + every cost writer (scribe/seam-meter/workroom).
*Why:* Silent spend loss + wrong per-meeting cost accounting; verify all converge on `meeting_cost`.

**FIX-8 — Concurrent-push checkout race.**
Two push webhooks in flight → two `refresh_on_push` for the same tenant/repo race on the checkout dir → interleaved clone state; a diverged-history ff-fail leaves `head_sha` returning the old SHA → the map is stored at the wrong SHA.
*File:* `services/premeeting/src/premeeting/refresh.py`.
*Why:* A wrong-SHA map means the resident understanding no longer matches the code the meeting pins to.

**FIX-9 — Identity orphan-tenant race.**
Concurrent first-sign-in for the same new email: the loser mints a tenant BEFORE the conflicting user INSERT, leaving an orphan `tenants` row (SELECT→INSERT-tenant→upsert-user flow).
*File:* `libs/db/.../repos/identity.py`.
*Why:* Orphan tenant rows accumulate; verify acceptable or add cleanup. (A user is never mis-pointed — COALESCE holds — but the orphan is real.)

**FIX-10 — `abort.aborted` duck-typing hole (human-control gate bypass).**
`resume_with_fallback` uses `getattr(abort, "aborted", False)` typed `Any`; a None/wrong object silently returns False → the Law-3 gate is bypassed → a killed build can be resurrected.
*File:* `libs/agentkit/src/agentkit/resume.py` (git-tracked, deleted in the working tree — must be confirmed dead or fixed before re-introduction).
*Why:* Human-control-absolute is a founding law; a silent bypass is a P0-class defect (P0-25) wherever this path is live.

**FIX-11 — Dead deprecated `_build_map_llm`.**
The deprecated LLM map builder is preserved but nothing on the live path calls it.
*File:* `services/premeeting/src/premeeting/map_build.py`.
*Why:* Dead code on a security-sensitive path; the readers flag it should be removed (spec WS1 "strip all dead code").

**FIX-12 — AbortController is per-event-loop / shared namespace.**
`AbortController` is created on the asyncio loop at construction; use across threads/loops is unsafe; the barge-in `_aborted` set and model-loop `_controllers` share one registry (a key collision between a task_id and an utterance_id → false-positive aborts).
*File:* `libs/agentkit/src/agentkit/abort.py` (git-tracked, deleted — review before any revival).
*Why:* A cross-loop abort silently no-ops; a namespace collision falsely aborts.

**FIX-13 — `stream_deltas` not applied on the map_build path (doc-vs-code drift).**
`agentkit.__init__` docstrings `stream_deltas` as "the AgentChunk consumer the map-build stream reads through," but `map_build.py` collects raw chunks without it. For a true-delta-emitting provider vs an accumulation-emitting one, the map output differs.
*File:* `libs/agentkit/src/agentkit/__init__.py` docstring ↔ `services/premeeting/.../map_build.py`.
*Why:* Either a documentation lie or an unapplied optimization; resolve and test with both a delta-emitting and an accumulation-emitting fake.

**FIX-14 — Field-diff sweep can pass vacuously.**
`contract_reads` derives the sweep root from `__file__.parents[4]`; if the repo layout moves, the sweep silently returns {} → the produce/consume field-diff passes with zero coverage.
*File:* `libs/contracts/src/contracts/contract_reads.py`.
*Why:* A vacuous pass hides real AgentChunk `.type↔.kind` drift; assert a non-empty consumer set is found on the real tree.

**FIX-15 — Cost telemetry has no real ledger emission.**
`call_external._record_cost` only computes and returns; the docstring claims "in production this emits to the ops cost ledger" but no emission is wired.
*File:* `libs/http/src/http/external.py`.
*Why:* Per-meeting cost accounting (TEST_MATRIX #39) depends on a real emission somewhere; flag the gap for live cost accounting.

**FIX-16 — Path-traversal surface in `tenant_repo_dir`.**
`paths.tenant_repo_dir` guards only a blank tenant_id, not slashes/spaces — a tenant_id containing `/` composes into the path (traversal).
*File:* `services/premeeting/src/premeeting/paths.py`.
*Why:* Tenant ids are server-derived UUIDs today (low exposure), but the isolation root must not be forgeable by a crafted tenant id.

**FIX-17 — `'exponential' backoff actually linear.**
`call_external` backoff is `asyncio.sleep(0.2 * attempt)` (0.2/0.4/0.6…) while the docstring says "exponential."
*File:* `libs/http/src/http/external.py`.
*Why:* Doc-vs-code drift; not a functional bug but the retry-timing contract must be documented accurately (assert the real linear sequence). *(Same file as FIX-15; kept distinct as it's a separate claim.)*

**FIX-18 — Two divergent sentence splitters.**
`speak._TERMINATORS`=`.!?` vs `session_host._TERMINATORS`=`.!?;…` — the host-side voice pipe and the in-sandbox streamer split sentences differently.
*File:* `services/in-meeting/src/in_meeting/speak.py` vs `session_host.py`.
*Why:* The live relay path and the file/replay path can chunk audio differently; verify both produce natural, in-order audio (a subtle audio-quality/ordering risk).

**FIX-19 — `load_latest_map` non-deterministic on built_at ties.**
`ORDER BY built_at DESC LIMIT 1` is non-deterministic when two maps were built in the same tick.
*File:* `services/premeeting/src/premeeting/map_store.py`.
*Why:* A meeting using the "latest" convenience path could load either of two same-tick maps; add a deterministic tiebreak (e.g. sha) or document the tolerance.

**FIX-20 — Source-scrub misses non-marker-prefixed source.**
`_scrub_source_processor` redacts from the four code markers (def/class/import/return) onward; source that doesn't start with one (a bare expression, a docstring) may slip through to stdout.
*File:* `libs/ops/src/ops/logging.py`.
*Why:* A partial customer-source leak into logs (isolation + grounded-or-silent); test coverage against real cal.com source samples.

**FIX-21 — Sessions table has no expiry column.**
`sessions` carries no explicit expiry; a never-expiring server session is a security risk if the app/cookie layer doesn't enforce TTL/rotation.
*File:* `libs/db/.../repos/sessions.py` (+ migrations).
*Why:* Verify expiry/rotation is enforced at the cookie/app layer or flag the gap (long-lived accept links depend on the session outliving the meeting — the trade-off must be intentional).

**FIX-22 — `NoteDelta` allows a null note_id on a non-add op.**
`NoteDelta(op="patch"/"close", note_id=None)` is permitted by the type though semantically a patch/close needs a target note_id.
*File:* `libs/contracts/src/contracts/notes.py`.
*Why:* A semantic gap that could produce an unanchored patch/close; flag for a validator or documented invariant.

---

# 4. Classification summary (how the master list splits)

- **LIVE (118)** — everything a human in the meeting sees or the transcript proves: hear/transcribe/recall, speak-back/streaming/opener, all channels (chat/dm/screen/mute/offer card), the reactive loop (monitor-while-working, present-back, deliver-in-one-turn), the nuances (clarify, blocker, ask→continue, barge-in heard, cross-talk silence, self-echo not-waking, honest degrade, no confabulation), trust/grounding (zero-read file:line, knows-where-to-look), reliability (no crash, recover-from-hiccup, long-meeting memory). The BOTH items' human-facing halves are counted under §5, not double-counted here.
- **INTERNAL (511)** — everything a happy-path meeting can't exercise: all tenant-isolation SQL + the dual-tenant battery, HMAC/webhook verification, the six-step funnel + route enumeration, boot secret/reaper-ratio gates + boot order, contracts closure + field-diff, migrations up/down + tenant-reachability, the claim/fence/reconcile races, per-sandbox secret + forgery containment, offboarding, the premeeting pipeline stages + verify gates, draft lifecycle + disposition isolation, agentkit provider/isolation-triad, all the guard scanners.
- **BOTH (47)** — behaviors seen live AND with a deterministic internal invariant: barge-in (heard + ≤200ms cut path), self-echo (observed + 45s/4-token/0.7 window), offer (card posted + never-auto-apply + no-push), mute (silence heard + webpage-first ordering), opener (ack heard + tool-gate/no-spurious-on-silence), wake-gate (wakes/ignores live + `\bproxy\b`/`@proxy`/self-filter), comprehension (zero-read grounded live + verify-drops-ungrounded), refresh (no mid-meeting swap live + Part-1 re-store), output-media (audio heard + bounded-buffer/XSS), to_meeting routing (channel choice live + never-throw), ASK→CONTINUE (resumes live + 180s latch), transport-cancel (loop survives live + retry/re-raise rule), session-dispatch (relay-vs-replay-vs-degrade live + per-wake keying), recall.join (bot in room + no placeholder id).

---

# 5. LIVE scenario map (so the transcript can be built to cover everything)

Every LIVE and BOTH-human-half item groups into one of these 11 diverse transcript scenarios.
Each is a coherent chunk judged at its END against the process invariants (grounded · verified ·
resident-knowledge · one-lookup-for-the-tail · parallelized · asked-when-vague · no-auto-apply ·
right-channel · handled-interruption · presented-back-proportional-honest). Nuances (barge-in,
clarify, cross-talk, self-echo) recur across several groups so we prove they fire consistently.
Counts = distinct LIVE/BOTH items the group must exercise (maps to TEST_MATRIX beats in parens).

**LG-1 — Foundation pipes: join, hear, transcribe, speak, resident context (12 items).**
Proxy + both bots join; consent line posts first; a bot speaks → the line appears in Proxy's
transcript via real STT (code terms correct); the transcript accumulates and is later recalled
unprompted; Proxy speaks back via Cartesia (audible, gapless). (Matrix A1-A5, B4.)

**LG-2 — Codebase understanding resident (trust/grounding) (9 items).**
Zero-read grounded `file:line` answer (no file read in the trace); the private-repo TRUST test
(a fact only the understanding could provide, answered zero-read); knows-where-to-look → ONE
targeted lookup for a detail not in the understanding; no-confabulation "not found by this
method" for something absent. (Matrix B6-B8, F30; spec WS6 a/b/c.)

**LG-3 — Simple reactive round-trip + right-channel + opener (10 items).**
Chit-chat → quick natural reply (no over-work); an instant opener ≤~2.5s before digging into a
real task; a simple lookup delivered correct+timely; gist aloud / detail in chat / artifact on
screen chosen per beat. (Matrix C9-C11, D16; opener BOTH-half.)

**LG-4 — Real work + present-back (the artifacts — SHOW them) (14 items).**
A real coding task in the repo → actual code, run/verified, offered as a staged change; a real
drafting task → an artifact on screen/chat; web research → a cited answer; above-and-beyond
(structured/verified, not minimal); present-back re-anchored to the ask though the convo moved
on; deliver-in-one-turn (never "I'll bring it back" then nothing); no-overstate. (Matrix D12-D15,
E18; wake-prompt BOTH-half.)

**LG-5 — Concurrency, parallelism, background-listening (11 items).**
Hear-while-working (bots keep talking during a long task → transcript keeps flowing, proven by
later recall); no dead air (opener + meaningful beats, not silence/spam); a new ask mid-work
(head-of-line, first not dropped); two real parallel tasks completed independently; monitor-while-
working (a wake is a background task); concurrent wakes never drop each other's delivery.
(Matrix E17, E19-E21; session-dispatch BOTH-half.)

**LG-6 — Vague → clarify → continue; blocker mid-work (8 items).**
An ambiguous ask → ONE crisp clarifying question (asks not guesses) → bot answers → Proxy resumes
the SAME task (no restart); a blocker mid-work → said honestly + work continues; ASK→ANSWER→
CONTINUE latch (continuation without a name mention). (Matrix F22-F24; ASK→CONTINUE BOTH-half.)

**LG-7 — Barge-in / talk-over (6 items).**
A bot talks over Proxy mid-sentence → speech stops fast (<~200ms); a following chat/dm after the
barge-in still lands (voice cut, not typing); the next wake speaks normally (latch lowered);
repeated barge-ins fire consistently. (Matrix F25; barge-in BOTH-half.)

**LG-8 — Cross-talk / self-echo (self-wake safety) (8 items).**
Bots talk to each other with "proxy" said incidentally → NO false wake, complete silence (a
spoken "not addressed" would itself be an interruption); Proxy's own voice on a no-headphones mic
→ never wakes/interrupts itself; no spurious "On it…" on a silent cross-talk decline. (Matrix
F26-F27; self-echo + opener-on-silence BOTH-halves.)

**LG-9 — Channels / capabilities exercised explicitly (12 items).**
Chat broadcast (correct content); DM to one participant only (right person); screen-share (visible,
readable); mute/unmute (audio actually stops/resumes, silence heard immediately); offer card posted
and applies ONLY on click; concurrent addresses (two bots at once → both handled sanely). (Matrix
F28, G31-G35; to_meeting/mute/offer BOTH-halves.)

**LG-10 — Trust / grounding / honesty under pressure (10 items).**
Honest degrade on a can't-do/couldn't-run case (says so plainly, never fakes "verified"); a
world-touching change surfaced ONLY as an offer (never described-as-done, never auto-applied);
grounded-or-silent held across the whole meeting; no-overstate ("I already did this" never said).
(Matrix D-honesty, F29-F30, G35 human-control; spec laws 1-3 — dynamic, verified on the real
transcript, not a unit test.)

**LG-11 — Reliability over a full-length meeting (8 items).**
No crashes end-to-end; recover from a vendor/network hiccup (transport-cancel) with the meeting
unaffected; long-meeting memory (recalls early content late — no forgetting); cost tracked
(number captured); teardown clean at meeting end. (Matrix H36-H39; transport-cancel BOTH-half.)

> Build order for the one long transcript: LG-1 → LG-2 → LG-3 → LG-4 → LG-5 (the hard concurrency
> stretch) → LG-6..LG-8 woven throughout → LG-9 channels → LG-10 honesty recurring → LG-11
> reliability across the whole run. Barge-in / clarify / cross-talk / self-echo recur in several
> spots to prove consistency, not a one-off.

---

# 6. Traceability note

Every raw derivation line maps here: per-file [CAPABILITY]/[WIRING]/[EDGE]/[FAILURE]/[NUANCE]
items became §1 master items or tight groups; the five cross-subsystem sections became the §1.5/§1.6
integration items + the §2 P0 batteries; the readers' explicitly-named defects + every additional
drift found on scan became §3 FIX-FIRST; the product-behavior items became §5 LIVE scenarios. The
acceptance contract for every LIVE/BOTH item is `ACCEPTANCE_FORMAT.md` (per-scenario, process-based,
GO only when outcome+invariants+extent+output all hold); the deployability bar is §2 all-green plus
§3 all-resolved.
