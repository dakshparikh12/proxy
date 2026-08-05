# libs/ops — exhaustive test-derivation

Scope: the operational seams that keep Proxy customer-deployable in ANY meeting scenario —
the E2B sandbox provider + its lifecycle/reconcile, per-sandbox JWT-secret minting, the
broker-free Postgres claim/fence, the reconcile sweep (incl. tenant offboarding), the
chat-link capability tokens (read-only frontier + revocation), source-scrubbing structured
logging, and the build-time guards (naming, copy voice, banned tokens, call_external seam,
SDK-isolation triad, contracts closure, secret-binding drift).

Each item is one line `[CATEGORY] <precise testable statement>`. Categories:
CAPABILITY / WIRING / EDGE / FAILURE / NUANCE.

Cross-cutting product laws these seams enforce: Isolation (one tenant never shares
volume/process/index; per-meeting sandbox; per-sandbox secret), Human-control (read-only
capability, no accept), Grounded/Never-overstate (source scrubbing), Naming (no internal
names user-visible), Secrets (Secret Manager only), Single external seam, Cost/leak guard
(TTL reconcile + heartbeat bump).

---

## ops/sandbox.py — provision_sandbox (per-sandbox JWT secret minting, AC-INV-009)

- [CAPABILITY] `provision_sandbox(tenant=, meeting_id=)` returns a `ProvisionedSandbox` with tenant, meeting_id, sandbox_id, jwt_secret populated.
- [CAPABILITY] `jwt_secret` is produced by `secrets.token_urlsafe(32)` → a ~43-char, URL-safe, cryptographically random string (assert length ≥ 43 and charset URL-safe).
- [CAPABILITY] `hs256_secret` property returns the exact same value as `jwt_secret` (alternate spelling, no divergence).
- [CAPABILITY] `sandbox_id` has shape `sbx-<tenant>-<meeting_id>-<8 hex chars>` (assert format + the trailing `token_hex(4)` = 8 hex chars).
- [NUANCE] Two calls with the SAME (tenant, meeting_id) mint DIFFERENT `jwt_secret` values — no two sandboxes ever share a secret, even for the same meeting (core isolation invariant).
- [NUANCE] Two calls with the SAME (tenant, meeting_id) mint DIFFERENT `sandbox_id` values (the `token_hex(4)` suffix differs) — deterministic-id collision only lives in the provider, not here.
- [EDGE] Empty `tenant` / empty `meeting_id` still mints a valid distinct secret (never raises; id becomes `sbx--<mid>-<hex>` / `sbx-<t>--<hex>`).
- [NUANCE] `ProvisionedSandbox` is a frozen dataclass — the minted secret cannot be mutated after mint.
- [NUANCE] Secret randomness quality: over N mints, all `jwt_secret` values are unique (no PRNG-seed reuse; statistically prove no collisions across e.g. 10k mints).

## ops/sandbox_provider.py — the three idempotent verbs + lifecycle/reconcile

### provision / handle
- [CAPABILITY] `provision(meeting_id=)` returns a `SandboxHandle` with id `sbx-<meeting_id>`, the given timeout backstop, a per-sandbox `jwt_secret`, and tenant.
- [CAPABILITY] `provision` marks the sandbox alive (`_ALIVE[id]=True`) and records it in `_LIVE_BY_MEETING` + `_PROVISIONED_AT`.
- [CAPABILITY] The returned `SandboxHandle` is awaitable: `await provision(...)` yields the handle itself (async harness boundary).
- [CAPABILITY] `SandboxHandle` is also usable synchronously (`provision(...).id`) without awaiting (dual-path contract).
- [CAPABILITY] `handle.sandbox_id` == `handle.id` (back-compat alias).
- [CAPABILITY] `handle.session_id` == `handle.id` — the decoded in-sandbox JWT `session_id` must equal `env.SESSION_ID`; assert this equality is what the sandbox will check.
- [NUANCE] `timeout_s` defaults to `sandbox_timeout_s()` from libs/db config when not passed; an explicit `timeout_s` overrides it and is coerced to int.
- [NUANCE] The E2B-native `timeout_s` backstop is the last-resort self-expiry — assert it is threaded onto the handle so the real backend `create(timeout=...)` receives it (cost/leak backstop even if destroy + reconcile both miss).

### provision idempotency (§3.9 — the load-bearing broker-free assumption)
- [EDGE] `provision(meeting_id=X)` twice for a still-ALIVE meeting returns the EXISTING handle (identity equal) — never a second sandbox.
- [NUANCE] A repeat provision for a live meeting returns the handle with its ALREADY-MINTED secret UNCHANGED — no fresh secret on a redelivered join.
- [EDGE] `provision` after `destroy` (a cold/re-provisioned-after-destroy meeting) mints a FRESH DISTINCT secret (assert new secret != old secret; two sandboxes never share).
- [EDGE] `provision` after `destroy` allocates a fresh `_PROVISIONED_AT` (age clock resets).
- [NUANCE] A fresh provision `_ENDED_MEETINGS.discard(meeting_id)` — re-joining an ended meeting un-ends it so the cron won't immediately reap the new sandbox.
- [EDGE] `provision` when a stale (`_ALIVE[id]=False`) entry lingers in `_LIVE_BY_MEETING`: the not-alive guard forces a fresh provision (does NOT return the dead handle).

### secret_for (host-side sandbox→secret map, §3.5 / CANONICAL §12.9)
- [CAPABILITY] `secret_for(handle)` and `secret_for(sandbox_id_str)` both return the sandbox's per-sandbox jwt_secret.
- [CAPABILITY] `secret_for` returns `None` for a never-provisioned or destroyed sandbox id.
- [NUANCE] The host (never the sandbox) owns `_SECRET_BY_SANDBOX`; the module never exposes the whole map — only per-id lookup.
- [NUANCE] There is NO fleet-shared secret constant anywhere — grep-assert no module-level default secret exists (a shared secret would let exfiltrated in-sandbox repo code forge a token for ANOTHER sandbox = P0 breach).
- [NUANCE] A token/secret minted for sandbox A must never verify against sandbox B's secret (cross-sandbox forgery test using two live handles).

### destroy (idempotent teardown)
- [CAPABILITY] `destroy(handle)` sets `_ALIVE[id]=False` and drops the secret from `_SECRET_BY_SANDBOX` (the secret dies with the sandbox — can never be reused).
- [CAPABILITY] `destroy` removes the meeting's entries from `_LIVE_BY_MEETING` and `_PROVISIONED_AT`.
- [CAPABILITY] `destroy` accepts a raw id string OR a handle (via `_key`).
- [EDGE] `destroy` on an already-destroyed / never-provisioned sandbox is a no-op (idempotent — never raises, tolerates 404).
- [CAPABILITY] `destroy(handle, backend=real)` returns an `_AsyncNone` whose await issues `backend.kill(sandbox_id=)`.
- [NUANCE] `destroy` host-side bookkeeping runs SYNCHRONOUSLY (before the awaitable) so a sync caller (destroy-on-close ordering) sees the teardown immediately.
- [EDGE] `destroy(...).__await__` when no backend: awaiting is a no-op that returns None.
- [FAILURE] A never-awaited `_AsyncNone` with a real backend coro must NOT emit an "un-awaited coroutine" warning (`__del__` closes the dropped coro) — assert no ResourceWarning on GC.
- [FAILURE] `backend.kill` raising a 404-equivalent: `_RealE2BBackend.kill` pops the instance first, so a second kill is a no-op — assert already-gone kill does not raise.

### health_check
- [CAPABILITY] `health_check(handle)` returns a `SandboxHealth` whose `.alive` reflects `_ALIVE`.
- [CAPABILITY] `SandboxHealth` is awaitable (`await health_check(h)` → bool) AND truthy-testable (`bool(health)` / `if health:`).
- [NUANCE] An UNKNOWN sandbox id defaults to alive=True (historical default) — assert an un-tracked id reads alive, a destroyed id reads not-alive.

### pre_provision (§3.9 meeting-creation trigger)
- [CAPABILITY] `pre_provision(join_event=)` provisions exactly ONE sandbox for the meeting the event names.
- [WIRING] `pre_provision` reads `meeting_id` and `tenant` from the join event dict and delegates to `provision`.
- [EDGE] `pre_provision` on a join event missing `meeting_id`/`tenant` defaults them to "" (never KeyError).
- [NUANCE] There is NO warm idle pool and no cold-boot mid-meeting — a sandbox is only spun on a create/join event (assert no standing keepalive pool state).

### heartbeat_bump (§3.9 anti-reap for silent builds)
- [CAPABILITY] `heartbeat_bump(handle, backend=)` extends the sandbox timeout via `backend.set_timeout`.
- [NUANCE] The default extension is `handle.timeout_s + sandbox_timeout_s()` — STRICTLY GREATER than `handle.timeout_s`, so a build silent longer than the original backstop window is NOT reaped (the exact §3.9 failure the bump prevents).
- [EDGE] An explicit `timeout_s=` overrides the default extension value.
- [EDGE] `heartbeat_bump` on a not-alive (`_ALIVE[id]=False`) sandbox is a no-op (returns immediately, never calls backend).
- [EDGE] `heartbeat_bump` with `backend=None` computes the extend value but is a safe no-op on the backend (assert no crash).
- [FAILURE] Adversarial: a bump that only RE-SETS the same deadline (extend_to == timeout_s) would silently let the sandbox reap — assert the computed extend_to strictly exceeds the original.
- [EDGE] Repeated bumps on a bump cadence are idempotent/safe (each pushes the deadline further; never shrinks it below the backstop).

### ensure_running (§3.9 — the ONE race-safe "get me a healthy sandbox NOW")
- [CAPABILITY] `ensure_running(db, meeting_id)` returns a healthy `SandboxHandle` for the meeting.
- [EDGE] Fast path: a healthy live sandbox already exists → returned WITHOUT taking the claim (no DB round-trip) — assert claim is not attempted.
- [EDGE] Cold path (no live sandbox): the caller races for the atomic operation_runs claim and provisions exactly once.
- [EDGE] Contended path: N concurrent `ensure_running` for the SAME meeting result in EXACTLY ONE provision; the losers return the winner's SAME handle (assert one sandbox, N identical handles).
- [FAILURE] A loser whose winner's handle never lands (winner errored before storing) falls through and provisions itself — assert no deadlock and eventual live handle.
- [EDGE] `_await_live_handle` polls bounded (50 attempts × 10ms) — never blocks forever; returns None on timeout so the loser provisions.
- [EDGE] Health that reads `gone`/not-alive re-provisions a FRESH sandbox (never a doomed restart of the dead one).
- [NUANCE] A `db` without an `acquire` capability (test double) defaults `_claim_provision` → won=True, so the single-caller path still provisions (assert the double-path).
- [CAPABILITY] `ensure_running(provision=factory)` uses the injected factory; default is the module `provision` verb.
- [EDGE] The factory may return a coroutine OR a plain handle — `_isawaitable` handles both (assert both factory shapes resolve to a handle).
- [WIRING] `_claim_provision` calls `claim.claim_meeting(db, meeting_id, SANDBOX_PROVISION_OP)` — the SAME Postgres arbiter the meeting-harness claim uses; assert `SANDBOX_PROVISION_OP == "sandbox-provision"` and it's a distinct operation_type from `meeting-harness`.

### list_sandboxes / mark_meeting_ended / reconcile (§3.8/§3.9 cost + leak backstop)
- [CAPABILITY] `list_sandboxes()` yields only currently-alive handles (the cron's orphan-candidate view; = E2B `list` in prod).
- [EDGE] `list_sandboxes()` excludes destroyed sandboxes even if their meeting entry lingered.
- [CAPABILITY] `mark_meeting_ended(meeting_id)` records the meeting in `_ENDED_MEETINGS` so the reconcile reaps any surviving sandbox.
- [CAPABILITY] `_is_orphan_or_past_ttl` returns True iff the meeting ENDED or age > `sandbox_ttl_s()`.
- [EDGE] A sandbox whose meeting is in `_ENDED_MEETINGS` is reapable even if under TTL.
- [EDGE] A sandbox with no `_PROVISIONED_AT` entry (unknown start) is treated as NOT past-TTL (returns False — never reaped on missing age).
- [EDGE] Exactly-at-TTL boundary: age == ttl is NOT reaped (strictly `>`); age = ttl + ε is reaped.
- [CAPABILITY] `reconcile_sandboxes()` destroys every orphaned/past-TTL live sandbox and returns the reaped count.
- [EDGE] `reconcile_sandboxes` is idempotent — a second run over the reaped state finds nothing (returns 0).
- [FAILURE] A sandbox that survived explicit close (destroy missed) IS reaped by the cron (the cost backstop — leaked-sandbox runaway-spend guard).
- [NUANCE] There is NO sandbox registry TABLE, NO warm pool, NO FSM (assert the module keeps only in-process dicts + the E2B list analogue).
- [FAILURE] `reconcile_sandboxes` where `destroy` raises on one handle must not abort the whole sweep (per current code destroy tolerates 404; adversarially inject a raising backend and assert the loop's resilience is as designed / documented).

### _RealE2BBackend (the live E2B seam — behind call_external)
- [WIRING] `create()` resolves the E2B class via `libs.http.external.e2b_sandbox_class()` (the SOLE raw-client home) — THIS module never imports `e2b`.
- [WIRING] Every backend op (`create`/`kill`/`set_timeout`/`is_running`) routes through `http.call_external(..., service="e2b")` — retry + cost telemetry (no raw client here).
- [NUANCE] `create` passes `envs` (where per-sandbox `JWT_SECRET` + `SESSION_ID` land, §3.5) and `metadata` — assert the JWT secret + claim id are threaded into `envs`, not logged.
- [NUANCE] `create` threads the `network=` egress kwarg ONLY when provided; when omitted it is NOT sent (would inherit E2B default-ALLOW outbound = egress leak). Assert default-DENY egress requires the caller to pass `network`.
- [FAILURE] Egress: a non-allowlisted host must be UNREACHABLE from the sandbox (the `network={denyOut, allowOut}` default-deny + curated allow-list, §3.10) — this is a Phase-3 live-infra test but the host-side threading of `network=` must be asserted now.
- [FAILURE] Absent the `e2b` package, `e2b_sandbox_class()` raises `ImportError` at first use → caller degrades honestly to the in-process substrate view (assert honest-degrade, never a silent fake success).
- [CAPABILITY] `kill` pops the instance then calls `.kill()`; a missing instance returns without calling (404 idempotent).
- [CAPABILITY] `set_timeout` / `is_running` no-op on a missing instance (set_timeout returns, is_running returns False).
- [EDGE] `create` returns the backend's `sandbox_id` (falls back to the requested id if the instance lacks the attr).
- [NUANCE] E2B template id is the constant `proxy-workroom` — the actual template BAKE (Node workspace-mcp sidecar + ast-grep) is a Phase-3 deploy residual (flagged, not faked); assert the id is passed through, don't assert the bake.

### test-isolation
- [NUANCE] `_reset_for_test()` clears ALL five in-process dicts/sets — assert no cross-test warm state can leak a secret/handle between tests (isolation of the isolation-tester itself).

## ops/claim.py — broker-free cross-process coordination (Postgres = sole arbiter)

- [CAPABILITY] `claim_meeting(Database, scope_id, op)` returns a coroutine that on await returns the new row id when the caller WINS the claim.
- [CAPABILITY] `claim_meeting(raw_conn, scope_id=, operation_type=)` (sync) returns the row id on win.
- [CAPABILITY] A concurrent duplicate claim on the same (scope_id, operation_type) with status='running' returns None (ON CONFLICT DO NOTHING on the partial unique index).
- [EDGE] Exactly one of N concurrent claimers wins; the rest get None (the core double-provision / double-run guard).
- [EDGE] A non-'running' row (interrupted/completed/failed) NEVER blocks a re-claim — a reaped scope can be re-claimed (assert insert succeeds after the prior row is interrupted).
- [NUANCE] `scope_id` is cast to text (`meeting_id::text`) at the single call site (§5.2) — assert a UUID/int meeting id claims correctly against the text `scope_id` column.
- [NUANCE] `created_by` defaults to `db.instance_id` (async) or the passed instance_id (sync) — assert the owning instance is recorded.
- [WIRING] The async path uses `db.acquire()` + `conn.fetchrow` (asyncpg `$1` params); the sync path uses `conn.execute` (psycopg `%s` params) — assert both param styles hit the SAME partial-unique-index semantics.
- [CAPABILITY] `sweep_stale_on_read(conn)` flips every 'running' row whose `last_heartbeat_at` is older than `stale_after_s()` to 'interrupted'; returns the count.
- [CAPABILITY] `sweep_stale_on_read(conn, scope_id=X)` scopes the sweep to one meeting.
- [EDGE] A fresh (recently-heartbeat) running row is NOT swept (only rows past `stale_after_s()`).
- [EDGE] `sweep_stale_on_read` is idempotent — a second pass finds nothing stale (count 0).
- [FAILURE] Reaper-on-read is the crash-recovery path: an owner that crashed (stops heartbeating) has its row swept so a replacement can re-claim — assert a stale row unblocks a new claim.
- [NUANCE] `MIN_REAPER_RATIO` (libs/db): `stale_after_s() >= 3 × heartbeat_s()` — a live owner must miss ≥3 beats before being reapable; assert `assert_reaper_ratio()` fails-closed on unsafe config (double-freeing a live meeting is the most dangerous seam).
- [CAPABILITY] `with_meeting_lock(db, key)` holds `pg_advisory_xact_lock(hashtext(key), 0)` for a per-meeting critical section, released on transaction exit.
- [EDGE] Two `with_meeting_lock` on the same key serialise (second blocks until first exits); different keys run concurrently.
- [NUANCE] No in-memory lock and no message broker anywhere in this path (assert the ONLY coordination primitive is Postgres).

## ops/operation_run.py — with_operation_run + the ownership fence

- [CAPABILITY] `with_operation_run(Database, scope, op)` (async) claims a 'running' row on entry, heartbeats it, and flips it to 'completed' on clean exit.
- [CAPABILITY] On an exception inside the block, the row is flipped to 'failed' with `error=repr(exc)` and the exception re-raised.
- [FAILURE] Entry when the scope is already owned (claim returns None) raises `RuntimeError("operation already owned: ...")` — assert the message names scope + op.
- [CAPABILITY] `OperationHandle.heartbeat()` is a FENCE: the UPDATE is gated on `status='running'`; a 1-row update keeps `is_owner=True`, a 0-row update flips `is_owner=False`.
- [FAILURE] A reclaimed/reaped row (no longer 'running') drives `heartbeat()` → is_owner=False → the zombie must self-terminate and emit nothing (the split-brain guard — assert a fenced-out owner produces NO output).
- [CAPABILITY] `_heartbeat_loop` beats every `heartbeat_s()` tick and, while owner, calls `bump_activity` to keep the sandbox alive; on losing the fence it stops (self-terminate).
- [CAPABILITY] `check_pause()` surfaces `pause_requested` so a running build can be paused/aborted (returns False when the row isn't 'running').
- [CAPABILITY] `bump_activity()` refreshes the scope's sandbox keepalive (distinct from the ownership heartbeat) — keeps E2B alive during silent token-less agent work.
- [EDGE] `_finish` is gated on `status='running'` — a finalizer NEVER clobbers a row a replacement already re-owns (assert a reaped-then-reclaimed row isn't overwritten to completed by the old owner's exit).
- [EDGE] `_cancel` cancels the heartbeat task on exit and suppresses `CancelledError` (assert clean teardown, no dangling task).
- [FAILURE] Cancellation: a `BaseException` (incl. CancelledError) inside the block still cancels the heartbeat, finalizes as 'failed', and re-raises (assert no leaked heartbeat task on cancel).
- [CAPABILITY] `with_operation_run(raw_conn, scope, op)` (sync mirror) claims on entry, finalizes to 'completed' on exit (gated on status='running').
- [EDGE] Sync `_SyncOperationHandle.heartbeat()` rowcount-0 clears ownership (mirror of async fence).
- [CAPABILITY] `_rowcount("UPDATE 1")` → 1, `"UPDATE 0"` → 0, malformed tag → 0 (parser correctness).
- [NUANCE] `op_type=` is accepted as an alias for `operation_type` (assert alias resolves).
- [NUANCE] Sync path `created_by` is `proc-<uuid4>` (a distinct per-process owner) — assert two sync procs never claim the same running row.

## ops/reconcile.py — run_reconcile_sweep (the ONE idempotent, token-gated reconcile)

### async persisted sweep (three isolated steps, §3.8)
- [CAPABILITY] `run_reconcile_sweep(Database)` runs the three steps in order (stale-harnesses → meeting-sandboxes → notes-retention) and returns `{"steps": [...], "errors": [...]}`.
- [FAILURE] Per-step isolation: each step runs in its OWN try/except — a raising step is captured as `"<name>: <exc>"` in `errors` and NEVER aborts the remaining steps (assert step 2 failure still runs step 3).
- [EDGE] Idempotent: running the async sweep twice over the same state yields the same end state (every step idempotent).
- [WIRING] `_step_stale_harnesses` calls `db.sweep_stale_operation_runs()` (the redelivered-join dedup reaper).
- [WIRING] `_step_meeting_sandboxes` calls `sandbox_provider.reconcile_sandboxes()` (§3.9 — list live, kill orphans; does NOT touch the DB).
- [NUANCE] `_step_notes_retention` is an intentional idempotent no-op in V0 (the append-only `note_deltas` ledger is the source of truth; the Tier-2 mirror + retention were cut) — assert it never raises and keeps the three-step shape.

### sync token-gated /internal/reconcile
- [CAPABILITY] `run_reconcile_sweep(conn, token=valid)` sweeps stale rows and returns the count of rows still 'running' (idempotent end state).
- [FAILURE] `run_reconcile_sweep(conn, token=invalid)` or absent token raises `PermissionError` — the public /internal/reconcile endpoint is refused without a valid token.
- [NUANCE] Token compare is `hmac.compare_digest` (constant-time, B4) — a naked `==` would leak the token byte-by-byte via timing; assert the compare is constant-time (no early-return on prefix mismatch).
- [NUANCE] Token source is `INTERNAL_RECONCILE_TOKEN` env (Secret Manager in prod) with a dev literal fallback — assert prod binds the secret (boot hard-gate) and the dev literal never reaches prod.
- [EDGE] A non-string / empty token is refused (returns False before compare).
- [EDGE] Sync sweep idempotency: a second call over the same substrate returns the same running-row count (nothing stale left to flip).

### tenant offboarding sweep (isolation P0 — deleting a tenant)
- [CAPABILITY] `run_reconcile_sweep(conn=, tenant=T, gcs=, reason=)` deletes the tenant's Postgres rows across EVERY table carrying a `tenant`/`tenant_id` column and returns `{tenant, reason, rows_deleted}`.
- [WIRING] `_tenant_scoped_columns` reads `information_schema.columns` for `tenant`/`tenant_id` in schema `public` — assert every tenant-scoped table is discovered dynamically (a new tenant table is auto-covered).
- [NUANCE] The DELETE compares `{column}::text = %s` — a text tenant id never mis-casts against a uuid column (matches nothing correctly, never raises).
- [NUANCE] SQL is composed with `psycopg.sql.Identifier` (no string interpolation of table/column) — assert NO SQL-injection surface even though names come from the catalog.
- [CAPABILITY] When `gcs` is provided, `gcs.delete_prefix("tenants/<tenant>/")` drops every object under the tenant's GCS prefix.
- [FAILURE] Offboarding is the tenant-isolation teardown: assert AFTER offboarding tenant A, NO row with A's tenant remains in ANY tenant-scoped table and no A object remains in GCS (cross-tenant residue = P0 breach).
- [EDGE] Offboarding is idempotent — a second sweep over the already-deleted tenant returns rows_deleted=0 and doesn't raise.
- [EDGE] Offboarding a tenant with no rows returns rows_deleted=0 (no-op success).
- [NUANCE] Offboarding tenant A must NOT delete tenant B's rows/objects (assert cross-tenant safety of the DELETE and the prefix).

### dispatch resolution
- [NUANCE] `run_reconcile_sweep` first-arg type selects the path: Database→async; else raw conn; `tenant=`→offboard; else token-gated. Assert each dispatch branch is reached correctly (e.g. a raw conn WITHOUT tenant AND without token → PermissionError, not an offboard).
- [EDGE] `target` vs `conn=` kwargs: `handle = target if target is not None else conn` — assert both call shapes resolve the same connection.

## ops/capability.py — chat-link capability tokens (read-only frontier, AC-INV-012 / §2.8 / §4.6)

### mint + structural read-only frontier
- [CAPABILITY] `mint_capability_token(meeting_id=, scope=, ttl_seconds=)` returns a `CapabilityToken` (a structured object, NOT a bare JWT string).
- [NUANCE] `CapabilityToken` exposes NO `jti` and is not a string — the read-only frontier is checked STRUCTURALLY against its fields, not by trusting an opaque bearer blob (assert it's a frozen dataclass).
- [CAPABILITY] The token captures the meeting's CURRENT epoch at mint (embedded in the signed body).
- [CAPABILITY] `authorize(token=, action="notes:read", meeting_id=own)` grants ONLY `notes:read` on the token's own meeting.
- [FAILURE] `authorize` REFUSES `draft:accept` (and every other action) — a capability token can NEVER grant accept or any world-touching action (Human-control law; assert accept is refused even with an otherwise-valid token).
- [NUANCE] Fail-closed order: signature → revocation → expiry → meeting → scope/action (assert each stage rejects in that order; e.g. a tampered token is `invalid_token` before any other check).

### signature / tamper
- [FAILURE] A token with a tampered `meeting_id`/`scope`/`expires_at`/`epoch` fails `_valid_signature` (the HMAC covers the WHOLE body incl. epoch) → refused as `invalid_token`.
- [EDGE] A malformed (non-hex / empty) `signature` returns False from `_valid_signature` (never crashes, never grants) — assert `compare_digest` handles length mismatch.
- [NUANCE] `_sign` uses `hmac.compare_digest` for verification (constant-time; no timing oracle on the signature).
- [NUANCE] The signing key is per-process random (`secrets.token_bytes(32)`) — forgery is infeasible without a fleet-shared secret (assert no shared-key constant).

### shared-state / import-identity trap (§12.9)
- [NUANCE] The signing key + revoked-set + epoch-map are anchored in ONE `sys.modules` sentinel so BOTH import identities (`ops.capability` and `libs.ops.src.ops.capability`) share the SAME state — assert a token minted via one identity authorizes/revokes via the OTHER (mint on close-line path, verify on read route).
- [FAILURE] Regression: if the shared-state sentinel breaks, a token minted on one path would be invisible to the other (a valid token wrongly refused, or a revoked token wrongly honored) — explicitly test cross-identity mint+revoke agreement.

### expiry
- [FAILURE] A token past `expires_at` is refused as `expired` (assert time-boundary: `time.time() >= expires_at` refuses).
- [EDGE] Exactly-at-expiry (`now == expires_at`) is refused (`>=`, not `>`).
- [CAPABILITY] Short-TTL: a 0- or negative-ttl token is immediately expired (assert mint with ttl≤0 never authorizes).

### wrong-meeting
- [FAILURE] `authorize(token_for_A, meeting_id=B)` is refused as `wrong_meeting` — a token scoped to one meeting NEVER reads another meeting's notes (cross-meeting isolation; assert both string + non-string meeting ids compared as str).

### per-token revocation (§2.8 revoked_tokens)
- [CAPABILITY] `revoke_capability_token(token)` records its signature; `authorize` thereafter refuses it as `revoked` even though sig/TTL/meeting are otherwise valid.
- [CAPABILITY] `is_revoked(token)` returns True for an individually-revoked token.
- [EDGE] Revocation persists for the process lifetime (assert a revoked token stays refused across repeated authorize calls).

### per-meeting epoch revocation (§2.8 en-masse)
- [CAPABILITY] `bump_meeting_epoch(meeting_id)` increments the meeting's epoch and returns the new value.
- [FAILURE] After a bump, EVERY token minted before the bump for THAT meeting is refused (embedded epoch < current epoch) — assert mass revocation.
- [NUANCE] `bump_meeting_epoch(A)` does NOT revoke tokens for meeting B (per-meeting; assert B's tokens still authorize).
- [CAPABILITY] A fresh mint AFTER the bump carries the new epoch and IS honored (assert the bump doesn't brick future mints).
- [EDGE] `is_revoked` returns True when `token.epoch < _current_epoch(meeting)` even without an individual revoke.
- [EDGE] Multiple bumps monotonically increase the epoch (assert two bumps → epoch 2, both old tokens refused).

### route-facing string adapter (§4.6 — the URL token)
- [CAPABILITY] `encode_capability_token` → base64url(JSON body + signature); `decode_capability_token` round-trips back to an equal token.
- [FAILURE] `decode_capability_token` NEVER throws — bad base64, bad JSON, non-dict body, missing/wrong-typed fields all return `None` (the public read route can't be 500'd by a hostile token string).
- [EDGE] `decode_capability_token(None)` and `decode_capability_token("")` return None.
- [FAILURE] A re-encoded/edited URL string (fields changed) is caught by `authorize`'s signature check — the string form adds NO trust (assert an edited encoded token is refused).
- [CAPABILITY] `verify_capability_token(token_str, meeting_id)` returns a granting `AuthzDecision` ONLY for a valid/unexpired/unrevoked/same-meeting `notes:read` token; returns None for EVERY other case (missing/garbage/wrong-meeting/expired/tampered/revoked).
- [FAILURE] `verify_capability_token` NEVER throws on a hostile string (decodes to None → returns None → route falls to 404/session).
- [EDGE] `verify_capability_token(None, ...)` returns None.
- [NUANCE] `AuthzDecision.__bool__` returns `.allowed` — assert `if decision:` truthiness matches the grant.
- [NUANCE] `epoch` defaults to 0 when absent from the decoded body (back-compat with pre-epoch tokens; assert `body.get("epoch", 0)`).

## ops/logging.py — structured JSON logging + source scrubbing (AC-OBS-001/009)

- [CAPABILITY] `configure_logging()` renders every stdout line as a single structlog JSON object (level + iso timestamp + scrub + JSON renderer).
- [CAPABILITY] `_scrub_source_processor` redacts raw customer source from EVERY string value in the event dict (before render).
- [FAILURE] Grounded/Never-overstate + isolation: a clone/parse log line carrying source bytes (a `def foo(...)`, `class Bar:`, `import x`, `return y` and everything after) is replaced with `[redacted-source]` — assert source bytes NEVER reach stdout.
- [EDGE] The `_SOURCE_MARKERS` regex is DOTALL and matches from the code marker onward — assert multi-line source blocks are fully redacted (not just the first line).
- [EDGE] Non-string event values (ints, dicts) are left untouched (assert only str values are scrubbed).
- [EDGE] A benign string with no source marker passes through unredacted (assert no over-redaction of normal log messages).
- [NUANCE] Adversarial: source that DOESN'T start with the four markers (e.g. a bare expression, a docstring) may slip through — assert the redaction coverage is understood/documented (potential gap to test against real cal.com source samples).
- [CAPABILITY] `get_logger(name)` returns a bound structlog logger.

## lint/naming.py — the user-visible naming lint (§14 AC-CON-002)

- [CAPABILITY] `check_user_visible_strings(mapping)` flags any value containing an internal name (Orchestrator / Scribe / workroom); exit_code 1 on any hit, 0 clean.
- [NUANCE] Matching is word-boundary + case-INSENSITIVE (`\bworkroom\b`) — assert "Workroom", "WORKROOM", "workroom" all flagged; "workrooms"/"workroommate" (no boundary) behavior asserted.
- [CAPABILITY] `scan_source` AST-scans `services/` + `libs/` for internal names in user-visible SINK calls only (via `copy_guide.is_user_visible_sink`) — a `_log.warning("...Scribe...")` internal line is NOT flagged.
- [NUANCE] Docstrings that legitimately name the internal terms are skipped (assert `_docstring_ids` excludes them — a docstring recording "no workroom" isn't a false positive).
- [NUANCE] The lint skips itself (`naming.py`) and `.git` (assert self-scan doesn't trip on its own `_INTERNAL_NAMES`).
- [CAPABILITY] `check()` returns 0 clean, raises `AssertionError` naming every `path:line` leak (fail-closed); `main()` prints and exits non-zero.
- [FAILURE] A user-visible `st.error("Orchestrator failed")` fails the build with its file:line (assert the merge-blocking guard catches a real leak).
- [EDGE] A file with a syntax/OS error is skipped (assert it doesn't abort the whole scan).
- [WIRING] Shares the ONE `is_user_visible_sink` classifier with copy_guide — assert a single definition of "user-visible" across both guards (no divergence).

## lint/copy_guide.py — user-visible copy voice + honesty shapes (Doc 08 §2.1/§2.3)

- [CAPABILITY] `check_copy(mapping)` flags any user-visible value matching a banned pattern (as-an-ai / filler / exclamation-theatre) naming key + pattern id.
- [FAILURE] "As an AI…" self-reference in a spoken/toast line fails the build (assert `\bas an ai\b` case-insensitive match).
- [FAILURE] Filler ("Certainly!", "Great question!", "Absolutely!", …) fails the build (assert the filler regex + trailing `!`).
- [FAILURE] Any exclamation mark in user-visible copy fails the build (exclamation-theatre; assert a bare `!` trips it).
- [CAPABILITY] `check_honesty_shapes()` asserts the three shapes (recuse / unknown / partial) exist as non-empty canonical seed strings; missing/empty → non-zero.
- [FAILURE] A missing/renamed honesty shape in `copy_seeds.json` fails the check naming the shape (assert `REQUIRED_HONESTY_SHAPES` all present).
- [FAILURE] An unreadable/malformed seed artifact returns exit 1 with "seed artifact unreadable" (assert never a crash).
- [NUANCE] The seed artifact is the SINGLE source — banned regexes + honesty strings live in `copy_seeds.json`, NOT inline (assert editing the JSON changes behavior; code carries no literal patterns).
- [CAPABILITY] `is_user_visible_sink` distinguishes UI receivers (`st.*`, sidebar, col, container, tab, expander, …) from logger receivers (`log`/`logger`/`logging` substrings) for ambiguous methods (warning/error/info) and always treats bare toast/say/speak as visible.
- [EDGE] `log.warning("Certainly!")` is NOT flagged (logger receiver); `st.warning("Certainly!")` IS (UI receiver) — assert the receiver disambiguation.
- [EDGE] `_receiver_root` resolves the left-most name of a chain (`st.sidebar.error` → 'st') — assert nested-layout receivers still classified as UI.
- [NUANCE] Docstrings quoting a banned pattern to record the ban are skipped (assert `_docstring_ids` exclusion — "no As an AI" docstring isn't a false positive).
- [NUANCE] The guard skips itself (`copy_guide.py`) and `.git`.
- [CAPABILITY] `check()` combines honesty-shape + committed-copy scan; raises AssertionError on any problem (fail-closed); `main()` exits non-zero.
- [WIRING] Runs beside `lint.naming` with identical invocation in `.pre-commit-config.yaml` and `.github/workflows/guards.yml` — assert guard parity (same invocation both layers).

## ops/check_banned_strings.py — dead-token resurrection guard (Doc 00 §10, AC-CI-007)

- [CAPABILITY] `scan(root)` AST-scans `services/`+`libs/` for banned tokens in live string literals, class/func names, identifiers, attribute names — returns file:line hits.
- [NUANCE] Banned set is assembled from fragments so the contiguous dead string never appears in THIS file (else its own list would trip other dead-token scanners) — assert the tokens reconstruct correctly (session_transcripts, ManagedResource, "warm pool", TILE_ADDRESS, meeting_cost_entries, workroom_tasks, close_jobs).
- [FAILURE] Reintroducing a banned token as product code (e.g. a `close_jobs` table name or a `ManagedResource` class) fails the build naming file:line (assert a real resurrection is caught).
- [NUANCE] Docstrings/comments naming a dead token to record "we do NOT use X" are skipped (comments absent from AST; docstrings excluded) — assert no false positive on the deliberate mentions.
- [NUANCE] `GCE-per-meeting` is INTENTIONALLY absent from the ban (revived by A-007) — assert it is NOT flagged.
- [EDGE] A partial/substring match inside a larger identifier IS flagged (`token in text`) — assert intended broadness (e.g. `close_jobs_v2`).
- [EDGE] The guard skips itself + `.git`; syntax-error files are skipped.

## ops/check_call_external.py — single external-seam guard (§14, D-002)

- [CAPABILITY] `raw_client_sites_outside_seam(root)` AST-scans `services/`+`libs/` for raw vendor-client CONSTRUCTIONS outside `libs/http`; returns file:line offenders.
- [NUANCE] The E2B `AsyncSandbox.create(...)` / `.connect(...)` are flagged as raw client constructions (the sandbox backend must live behind the seam) — assert an E2B construction outside libs/http fails.
- [CAPABILITY] Bare + aliased imports are tracked (`from anthropic import AsyncAnthropic as _AA` then `_AA(...)` is caught) via `_ImportBindings`.
- [CAPABILITY] Module-attr constructions caught: `httpx.AsyncClient`/`httpx.Client`, `storage.Client` (google.cloud.storage GCS), `recall.Client`, `deepgram.DeepgramClient`.
- [NUANCE] `google` canonicalizes to `storage` so `from google.cloud import storage; storage.Client()` is caught.
- [NUANCE] `TYPE_CHECKING`-only vendor TYPE imports are never flagged (imports, not constructions — no ast.Call) — assert a type-only import in a non-seam file passes.
- [EDGE] A construction inside `libs/http` (the seam) is NEVER flagged (assert the seam home is exempt via `_in_seam`).
- [FAILURE] A raw Anthropic/httpx/GCS/E2B client constructed in `services/*` (outside seam) fails the build — assert the retry+cost-telemetry bypass is caught (the CON-004 regression class).
- [EDGE] Bare `Client`/`AsyncClient` un-imported names are NOT flagged as standalone (too generic; only via module-attr or tracked import) — assert no false positive on an unrelated `Client(...)`.
- [EDGE] Guard skips itself + `.git`; syntax-error files skipped.

## ops/check_sdk_isolation_triad.py — lethal-trifecta containment (§10, CANONICAL §11.11)

- [CAPABILITY] `query_sites_missing_triad(root)` finds bare `query(...)` SDK call sites whose module LACKS all three triad markers (`SDK_LOCAL_TOOLS`, `disallowed_tools`, `permission_mode`).
- [NUANCE] Only a BARE `query(...)` (ast.Name) is a site — `x.query(...)` (a DB cursor method) and `def query` are NOT flagged (assert the disambiguation).
- [FAILURE] A bare `query()` in a module missing the triad fails the build naming file:line — a single untriaged query() is how repo/agent code reaches the HOST filesystem (assert the containment floor).
- [CAPABILITY] A `query()` site whose module carries all three markers passes.
- [NUANCE] V0 has zero real query() sites (the Workroom agent is a later build) — assert the gate passes honestly today and goes load-bearing on the first untriaged query().
- [EDGE] Guard skips itself + `.git`; syntax-error files skipped.

## ops/check_field_contract.py — contracts-registry closure (§10, §12)

- [CAPABILITY] `check()` imports `contracts.registry.assert_registry_closed` and runs the real closure check (MessageType enum ⇄ CHANNEL_REGISTRY set-equal).
- [FAILURE] A produced-but-unconsumed (or consumed-but-unregistered) message type fails the build naming the orphan (assert real drift is caught — this IMPORTS product contracts, not a text scan).
- [WIRING] Exercises the same `assert_registry_closed` the constitution names as the contracts guard — assert the executable half matches the rule.

## ops/check_secret_bindings.py — secret-binding drift gate (§7)

- [CAPABILITY] `parse_terraform_secrets` collects every Secret Manager `secret_id` from `infra/*.tf` (locals for_each map keys `"database-url" = 32` AND `toset([...])` entries).
- [CAPABILITY] `parse_deploy_config` collects secret handles from `.env.example` env keys (including commented `# KEY=` optional bindings).
- [NUANCE] `_canonical` normalizes both sides to lowercase-dashed and strips a trailing `-path` (a pathed env key maps to its underlying secret id) — assert `DATABASE_URL` ⇔ `database-url` and `PRIVATE_KEY_PATH` ⇔ `private-key`.
- [FAILURE] A secret declared in Terraform but NOT bound in the deploy config fails the gate naming it (the exact Doc 00 §7 boot-crash: "secret added to the module but not the deploy crashed prod at boot").
- [NUANCE] The gate is DIRECTIONAL: a deploy env key with no Terraform secret is NOT drift (model seats/regions/local paths are non-secret config) — assert the reverse direction doesn't false-positive.
- [EDGE] When `infra/` is absent (a checkout without the Terraform module) `main()` is a no-op success — assert graceful skip.
- [CAPABILITY] `check(terraform_secrets=, deploy_secrets=)` raises `SecretBindingDrift` naming both drift directions (unit form).
- [NUANCE] Secrets law: assert this proves secrets flow ONLY from Secret Manager (Terraform-declared) and every one is bound at deploy — no hard-coded credential path.

---

## Cross-subsystem integration points

- [WIRING] sandbox_provider.ensure_running → claim.claim_meeting(db, meeting_id, "sandbox-provision"): the sandbox provision and the meeting-harness run share the SAME Postgres operation_runs arbiter but DISTINCT operation_types — assert a sandbox claim and a harness claim for the same meeting DON'T collide (different op rows) yet both dedup within their own type.
- [WIRING] reconcile.run_reconcile_sweep (async) → sandbox_provider.reconcile_sandboxes() + db.sweep_stale_operation_runs(): the cron reaps BOTH orphaned sandboxes (cost/leak) AND stale operation_runs (redelivered-join dedup) in one idempotent, per-step-isolated sweep — assert a sandbox-reap failure never blocks the stale-harness reap.
- [WIRING] operation_run._heartbeat_loop → OperationHandle.heartbeat() (fence) + bump_activity() → db.bump_activity → sandbox keepalive: the SAME heartbeat tick that fences ownership also keeps the E2B sandbox alive during silent build work — assert a fenced-OUT owner (lost the row) STOPS bumping so its sandbox becomes reapable by TTL (no zombie keeps a leaked sandbox alive).
- [WIRING] sandbox_provider.provision → sandbox.provision_sandbox (mint secret) → _SECRET_BY_SANDBOX (host map) → _RealE2BBackend.create(envs={JWT_SECRET, SESSION_ID}): the per-sandbox secret + claim id flow host→sandbox via E2B `envs`; the host keeps the authoritative map, the sandbox verifies its JWT `session_id` == `env.SESSION_ID` — assert the full chain and that the secret is never logged (logging.py scrub) and never shared across sandboxes.
- [WIRING] capability.mint (control-plane close-line path) ⇄ capability.verify_capability_token (GET /m/{meeting_id} read route): mint and verify MUST resolve the SAME sys.modules-anchored signing key/revoked-set/epoch across import identities — assert an end-to-end mint→encode→URL→decode→authorize round-trip grants, and a revoke/epoch-bump between mint and read refuses.
- [WIRING] reconcile offboard sweep → _tenant_scoped_columns (information_schema) + gcs.delete_prefix + (implicitly) sandbox_provider live sandboxes: full tenant offboarding must purge Postgres rows AND GCS objects; assert a live sandbox for an offboarded tenant is also reaped (mark_meeting_ended / TTL) so no compute residue survives — cross-tenant residue anywhere is a P0 breach.
- [WIRING] lint.naming ⇄ lint.copy_guide.is_user_visible_sink: ONE shared classifier defines "user-visible" for BOTH the naming guard (no internal names) and the copy guard (voice + honesty) — assert they never diverge (a sink one guard scans the other must too).
- [WIRING] check_call_external + check_sdk_isolation_triad together bound the sandbox's reach: call_external forces the E2B backend behind the retry+cost seam, the triad forces every SDK query() to keep tools in the sandbox — together they are the containment floor for "in-sandbox repo code can't reach the host / can't bypass cost telemetry / can't egress." Assert both gates are green AND that the ONLY E2B construction site is libs/http.
- [WIRING] check_secret_bindings ↔ reconcile._valid_internal_token (INTERNAL_RECONCILE_TOKEN) ↔ sandbox JWT_SECRET: every runtime secret this lib consumes (internal-reconcile token, per-sandbox JWT secret material, DSN) must be a Terraform-declared, deploy-bound Secret Manager secret — assert the drift gate covers INTERNAL_RECONCILE_TOKEN and any sandbox-secret config so nothing crashes at boot.
- [NUANCE] Config coupling (libs/db.config): sandbox_timeout_s / sandbox_ttl_s / stale_after_s / heartbeat_s drive provider backstop, TTL reconcile, the reaper window, and the fence cadence respectively — assert assert_reaper_ratio() (stale ≥ 3× heartbeat) is enforced at boot so a mis-config can never let a parallel boot reap a LIVE meeting's row (double-free) nor let a silent build's sandbox be reaped before heartbeat_bump extends it.
- [NUANCE] Isolation end-to-end (the deployability crux): across two concurrent tenants/meetings, assert NO shared secret, NO shared sandbox handle, NO shared operation_runs row, NO shared capability-token grant, NO cross-tenant row/GCS object after offboarding, and NO source bytes in logs — this is the union of the per-file isolation items and is the single most important customer-trust property.
