# libs/db — Exhaustive test-derivation for customer-deployable trust

Scope: `libs/db` (the single durable-state seam over ONE Cloud SQL Postgres) plus the
substrate schema in `migrations/versions/*` and the sibling `repo_maps` store
(`services/premeeting/src/premeeting/map_store.py`, which owns the `repo_maps` table that is
part of the durable substrate). Product frame: PRE-MEETING stores the repo "understanding"
(Postgres `repo_maps` + GCS); IN-MEETING per-meeting state (transcript/notes/drafts/cost).
`tenant_id` reaches `tenants` from every durable tenant-scoped table; **a cross-tenant read
is a P0 breach**.

Driver note (load-bearing across the whole lib): TWO drivers coexist. `database.py`, and every
`repos/*` module EXCEPT `connect.py`, use **asyncpg** (async, `$1` positional params).
`repos/connect.py` uses **raw psycopg3** (sync, `%s` params, autocommit connection). Any test
harness / conftest that mints connections must supply the RIGHT driver per module, and the
lib must never leak one driver's connection into the other's function.

---

## `src/db/config.py` — operational tunables + reaper-ratio safety gate

Product: reads `config/defaults.toml` for operational knobs (staleness, heartbeat, sandbox
TTL/timeout/MCP-port/JWT, STT refresh). Enforces the single most dangerous concurrency
invariant: STALE_AFTER_S ≥ 3× HEARTBEAT_S so a booting instance can never reap a live owner.

- [CAPABILITY] `load_defaults()` parses a valid `config/defaults.toml` and returns its dict.
- [CAPABILITY] `load_defaults()` is memoized (`lru_cache`) — repeated calls do not re-read the file (and a mid-process file edit is NOT observed until cache clear).
- [CAPABILITY] `_find_defaults()` walks UP from the module to the repo root and finds `config/defaults.toml` regardless of the process CWD (run from any working dir).
- [CAPABILITY] each accessor (`stale_after_s`, `heartbeat_s`, `sandbox_timeout_s`, `sandbox_ttl_s`, `sandbox_mcp_port`, `sandbox_jwt_ttl_s`, `sandbox_jwt_refresh_margin_s`, `stt_refresh_interval_s`) returns the value from the toml when present.
- [CAPABILITY] `reaper_ratio()` returns `stale_after_s / heartbeat_s`.
- [CAPABILITY] `assert_reaper_ratio()` passes (no raise) when STALE_AFTER_S ≥ 3× HEARTBEAT_S (default 40/10 = 4×).
- [EDGE] `_get` returns an `int` even when the toml value is a float/string-coercible number (`int(value)` cast); a value like `"40"` yields 40.
- [EDGE] a section present but a key missing falls back to `_FALLBACK[section][key]` (partial toml).
- [EDGE] a section entirely absent from the toml falls back cleanly (`data.get(section, {})`).
- [EDGE] boundary: STALE_AFTER_S exactly == 3× HEARTBEAT_S must PASS (predicate is `stale < 3*hb`, i.e. strictly-less fails, equal passes).
- [FAILURE] a MISSING `config/defaults.toml` returns `_FALLBACK` (no crash) and all accessors still return their conservative fallbacks.
- [FAILURE] a MALFORMED toml (`TOMLDecodeError`) returns `_FALLBACK`, not a partial/corrupt dict.
- [FAILURE] an unreadable file (`OSError`, e.g. perms) returns `_FALLBACK`.
- [FAILURE] `load_defaults()` returning a non-dict (defensive `isinstance(data, dict)` branch in `_get`) still yields the fallback rather than crashing.
- [FAILURE] `reaper_ratio()` raises `ReaperRatioError` when `heartbeat_s() <= 0` (div-by-zero guard).
- [FAILURE] `assert_reaper_ratio()` raises `ReaperRatioError` when `heartbeat_s() <= 0`.
- [FAILURE] `assert_reaper_ratio()` raises `ReaperRatioError` with a message naming BOTH values and the 3× requirement when STALE_AFTER_S < 3× HEARTBEAT_S (mis-config rejected fail-closed at boot, D-033).
- [NUANCE] the ReaperRatioError message must be actionable (contains the offending numbers + the `3×` rule + current ratio) — a human must be able to fix the config from the log alone.
- [NUANCE] env NEVER overrides these operational tunables (config is the single source of truth; env is for secrets/seats only) — assert no accessor reads `os.environ`.
- [WIRING] `assert_reaper_ratio()` MUST be invoked at boot / config-load before any reaper runs (verify the control-plane / harness boot path calls it — a mis-config that never calls the guard is silently unsafe).
- [WIRING] `stale_after_s()` feeds `database.sweep_stale_operation_runs` (the reaper window); a change here changes the reaper's live behavior — cross-check the two read the SAME value.

---

## `src/db/database.py` — the asyncpg pool + `Database` facade + reaper barrier

Product: the ONE connection-pool construction site over Cloud SQL Postgres; the durable-state
handle every service borrows; owns the operation-run reaper (boot bulk sweep + lazy-on-read)
that is the substrate's read barrier, and the per-scope sandbox keepalive markers.

### `open_pool` / DSN normalisation
- [CAPABILITY] `open_pool(dsn)` creates an asyncpg pool with min_size=2, max_size=20, max_inactive_connection_lifetime=30, command_timeout=10.
- [CAPABILITY] `_normalise_dsn` strips a `postgresql+psycopg://` SQLAlchemy driver suffix down to bare `postgresql://` (asyncpg needs the libpq URL).
- [CAPABILITY] `_normalise_dsn` passes a plain `postgresql://…` DSN through unchanged.
- [EDGE] `_normalise_dsn` on a Cloud SQL Unix-socket DSN (`host=/cloudsql/<proj>:<region>:<inst>`, no SSL params) is preserved verbatim (prod path — proxy terminates TLS).
- [EDGE] `_normalise_dsn` only strips the specific `postgresql+psycopg://` prefix — a `postgresql+asyncpg://` or other suffix is NOT accidentally mangled (document/verify behavior).
- [EDGE] pool sizing: ~2 Cloud Run instances × max_size 20 ≈ 40 connections must stay under the Cloud SQL max-connections limit (capacity assertion — the pool cannot be sized to exhaust the instance).
- [FAILURE] `command_timeout=10` — a statement that runs longer than 10s raises rather than hanging a borrowed connection forever (verify long-query cancellation).
- [FAILURE] `open_pool` against an unreachable DSN surfaces a connection error (does not hang past a bounded time).

### `Database` facade
- [CAPABILITY] `Database.connect(dsn)` opens a pool and returns a facade; auto-generates an `instance_id` (`proc-<uuid>`) when none is passed.
- [CAPABILITY] `Database.connect(dsn, instance_id=...)` uses the supplied instance_id (the value written to `operation_runs.created_by`).
- [CAPABILITY] `instance_id` and `pool` properties expose the underlying handles.
- [CAPABILITY] `repos` property returns a fresh `Repos` namespace bound to this facade.
- [CAPABILITY] `acquire()` is an async context manager that borrows and releases a pool connection.
- [CAPABILITY] `close()` closes the pool.
- [FAILURE] `Database.connect(None)` / empty DSN raises `ValueError("Database.connect requires a DSN")` (fail-fast, never a silent nil pool).
- [EDGE] `acquire()` returns the connection to the pool even when the caller's `async with` body raises (no connection leak on exception).
- [EDGE] pool exhaustion — the 21st concurrent `acquire()` (max_size=20) waits for a release rather than erroring, and eventually succeeds when a borrow returns.
- [NUANCE] `Repos` is constructed per `repos` access (new object each call) — a caller caching `db.repos` vs re-reading it must both work; assert no per-call state corruption.

### Sandbox keepalive markers (process-local)
- [CAPABILITY] `bump_activity(scope_id)` records `time.monotonic()` for that scope.
- [CAPABILITY] `last_activity_at(scope_id)` returns the last bump time, or `None` if never bumped.
- [EDGE] markers are PROCESS-LOCAL (a plain dict on the facade) — a different Cloud Run instance / a recycled process sees `None` (verify the TTL reconcile treats an unknown marker conservatively, does not falsely reap).
- [NUANCE] `bump_activity` is distinct from the fencing heartbeat: it refreshes the SANDBOX keepalive (spares a still-active scope from the TTL reconcile) while `last_heartbeat_at` proves OWNERSHIP — the two must not be conflated in tests or wiring.
- [WIRING] `libs.ops` reconcile reads `last_activity_at` (upward dependency lives in ops, not db) to spare a still-active scope; verify the marker written by the heartbeat loop is the one ops reads, keyed by the SAME scope_id.
- [EDGE] concurrency: `bump_activity` and `last_activity_at` on the same scope from overlapping coroutines must not corrupt the dict.

### Operation-run reaper barrier (the most dangerous concurrency seam)
- [CAPABILITY] `sweep_stale_operation_runs()` sets status `running`→`interrupted` and stamps `completed_at=now()` for rows whose `last_heartbeat_at < now() - STALE_AFTER_S`.
- [CAPABILITY] `sweep_stale_operation_runs()` returns the count of rows reaped.
- [CAPABILITY] `get_operation_run(scope_id, operation_type)` runs the sweep first (reaper-on-read) then returns the newest matching row (`ORDER BY started_at DESC LIMIT 1`) as a dict, or `None`.
- [NUANCE/P0] **A BOOTING instance NEVER reaps a FRESH row** (D-033): a row whose heartbeat is within STALE_AFTER_S is untouched even while another instance boots and sweeps — prove a live sibling's fresh claim survives a parallel boot sweep (double-free = the worst substrate bug).
- [EDGE] boundary: a row with `last_heartbeat_at` EXACTLY at `now() - STALE_AFTER_S` — the `<` predicate means exactly-at-window is NOT reaped (only strictly older); assert the boundary.
- [EDGE] idempotency: a second `sweep_stale_operation_runs()` over the same state reaps 0 (no-op); an already-`interrupted`/`completed`/`failed` row is never re-touched (WHERE status='running' only).
- [EDGE] `get_operation_run` picks the MOST RECENT run when multiple runs exist for the same (scope_id, operation_type) — verify ordering with several historical rows.
- [EDGE] the reaper only touches `status='running'` rows — a `completed` run older than the window is left alone.
- [FAILURE] a sweep during a connection blip surfaces the error to the caller (boot barrier) rather than silently reaping nothing and returning success.
- [NUANCE] `stale_after_s()` is read fresh each sweep — a live config change (within the 3× guard) takes effect on the next sweep.
- [WIRING] `created_by` on reaped rows is the reaping? No — the sweep does NOT rewrite `created_by`; verify the original claimant id is preserved for post-mortem.
- [NUANCE] the reaper query is meeting/scope-agnostic (sweeps ALL tenants' stale runs at once) — confirm this is intended (it is an ops-plane coordination table, not tenant-owned) and that reaping one tenant's stale run never touches another tenant's fresh run except via the shared staleness predicate.
- [EDGE] concurrency: two instances calling `sweep_stale_operation_runs()` simultaneously on the same stale row — the UPDATE…WHERE status='running' is atomic per-row; both may match but the second sees 0 rows for that id (no double-completion side effect); assert count correctness under the race.

---

## `src/db/repos/repositories.py` — thin per-domain facades + transaction ownership

Product: the `Repos` namespace; wraps parameterised SQL, opening a connection per call and
owning the transaction boundary where atomicity matters.

- [CAPABILITY] `MeetingRepository.insert / update_bot_id / get_by_bot_id` delegate to `meetings.*` inside a borrowed connection.
- [CAPABILITY] `TranscriptRepository.flip_and_append` wraps `transcript.flip_and_append` in `conn.transaction()` (the comprehension flip is atomic with the note-delta append).
- [CAPABILITY] `TranscriptRepository.pending_segment_ids` / `backfill_segment_as_lost` delegate to the tenant-safe transcript primitives.
- [CAPABILITY] `NotesRepository.apply_delta` ALSO wraps `transcript.flip_and_append` in a transaction (same atomic seam surfaced under a notes name).
- [CAPABILITY] `SandboxRepository.stage_draft / get_draft` delegate to `drafts.*`.
- [CAPABILITY] `OperationRepository.get` delegates to `database.get_operation_run` (reaper-on-read).
- [CAPABILITY] `Repos` exposes `.meetings/.transcript/.notes/.sandbox/.operations` (thin classes) AND `.cost/.sessions/.webhooks` (raw modules for callers owning their own tx).
- [EDGE] `flip_and_append` transaction ROLLS BACK BOTH the segment status flip and the note-delta append together on any failure (segment never left half-comprehended) — force a failure mid-transaction and assert both revert.
- [NUANCE] the raw-module handles (`cost/sessions/webhooks`) require the CALLER to own the transaction — a caller that forgets to wrap a multi-statement flow gets no atomicity; document the boundary and test both wrapped and unwrapped paths behave as designed.
- [WIRING] `Repos` is re-instantiated on each `db.repos` access — no shared mutable state; assert two concurrent `db.repos.meetings.insert(...)` do not interfere.

---

## `src/db/repos/meetings.py` — meetings + repos binding (tenant/repo/pinned_sha)

Product: the meeting bound to (tenant, repo, pinned_sha=HEAD); resolves bot_id→meeting for
webhooks; binds the tenant's `repos` row on connect success so `POST /meetings` can find it.

### meetings
- [CAPABILITY] `insert_meeting` inserts (tenant_id, repo_id, meeting_url, pinned_sha, recall_bot_id, status, platform) and RETURNS the new row incl. generated `id`.
- [CAPABILITY] `insert_meeting` defaults `status='live'`; `platform=None` stores NULL (never a fabricated value).
- [CAPABILITY] `mark_ended` flips status→'ended' and stamps `ended_at=COALESCE(ended_at, now())`.
- [CAPABILITY] `update_bot_id` writes the launched Recall bot_id back onto the row and returns it.
- [CAPABILITY] `get_by_bot_id` resolves (id, tenant_id, repo_id, pinned_sha, recall_bot_id, meeting_url) from a bot_id.
- [CAPABILITY] `get_by_id` resolves (id, tenant_id, repo_id) from a meeting_id.
- [EDGE] `mark_ended` idempotency: a re-run on an already-`ended` meeting KEEPS the first `ended_at` (COALESCE never overwrites) — assert timestamp unchanged on second call.
- [EDGE] `mark_ended` / `update_bot_id` / `get_by_id` / `get_by_bot_id` return `None` when no meeting matches (fail-closed, never invent a row).
- [FAILURE] `insert_meeting` with a `repo_id` that does not exist violates the `repo_id REFERENCES repos(id)` FK — raises, not silent.
- [FAILURE] `insert_meeting` with a `tenant_id` not in `tenants` violates the FK — raises.
- [FAILURE] `insert_meeting` with `status` outside {live,ended,interrupted} violates the CHECK — raises (never a bad status lands).
- [NUANCE/P0] `get_by_bot_id` returns `tenant_id` — the DOWNSTREAM caller MUST scope every subsequent action to THAT tenant_id; verify a webhook resolving bot_id→meeting can never cross into another tenant's repo (the resolved tenant is authoritative).
- [NUANCE] the map load is keyed on the meeting's EXACT `pinned_sha` (never "latest") — `get_by_bot_id` returns pinned_sha so the boot path pins the map to the same commit; verify a repo push after the meeting started does not swap the map SHA.

### repos binding
- [CAPABILITY] `upsert_repo_for_tenant` does a single atomic `INSERT … ON CONFLICT (tenant_id, full_name) DO UPDATE` and returns the row.
- [CAPABILITY] on conflict it BACKFILLS only NULLs (`COALESCE` keeps a recorded default_branch / github_installation_id; a fabricated NULL never overwrites a real value).
- [CAPABILITY] `get_repo_for_tenant` resolves a repo by full_name WITHIN one tenant.
- [CAPABILITY] `get_repo_by_id` resolves (id, tenant_id, full_name, default_branch) from a repo_id.
- [EDGE] `upsert_repo_for_tenant` idempotency + race-freedom: two CONCURRENT connects for the same (tenant, full_name) produce exactly ONE row (relies on the `0010` UNIQUE index) — run concurrently and assert no duplicate.
- [EDGE] `full_name` is stored EXACTLY as passed (byte-for-byte) — `get_repo_for_tenant` matches it byte-for-byte AND it must equal the key the map was stored under (`repo_maps.repo` via `repo_name_from_url`); a whitespace/case mismatch strands the invite at 404.
- [NUANCE/P0] `get_repo_for_tenant` is tenant-filtered by construction: a repo owned by ANOTHER tenant resolves to `None` EXACTLY like a nonexistent repo — **no existence leak** across tenants (prove tenant B cannot detect tenant A owns "org/repo" by the read distinguishing the two).
- [NUANCE/P0] `get_repo_by_id` / `get_by_id` take a bare id with NO tenant filter — verify EVERY caller has already proven the id belongs to the caller's tenant server-side (an unscoped-id read is only safe if the id itself was tenant-authorized upstream); this is a cross-tenant-read audit point.
- [FAILURE] `upsert_repo_for_tenant` with a `tenant_id` not in `tenants` violates the FK — raises before any repo row is created.

---

## `src/db/repos/transcript.py` + `repos/notes.py` — the live-append plane (§3.3)

Product: the append-only `note_deltas` ledger + `transcript_segments` status plane; the
comprehension flip is atomic with the note-delta append; the close reconciler backfills gaps.
(`notes.py` is imported by `repositories.py`; the two overlap by design — the §3.3 store.)

### notes.py (append-only ledger + segment plane + boot reaper)
- [CAPABILITY] `append_delta` inserts one `note_deltas` row and returns (id, created_at) on a real insert.
- [CAPABILITY] `append_delta` returns `None` when `ON CONFLICT (meeting_id, window_start_s, entry_id, op) DO NOTHING` silently discarded a duplicate — caller distinguishes fresh vs no-op WITHOUT any exception (AC-STORE-02).
- [CAPABILITY] a distinct `op` for the same (meeting_id, window_start_s, entry_id) IS a new row (op is part of the key) — NOT discarded (AC-STORE-02-NEG).
- [CAPABILITY] `append_delta` json-encodes a dict/list payload; a str payload is passed through as already-serialised json.
- [CAPABILITY] `load_deltas` returns ALL rows for one meeting in ascending `id` (write) order — the load-bearing correctness axis for the deterministic left-fold (AC-STORE-05).
- [CAPABILITY] `insert_segment` appends a `transcript_segments` row; when `status=None` the column is OMITTED so Postgres applies its DEFAULT 'pending' (never a client-side 'pending' literal that could mask a wrong default) (AC-STORE-01).
- [CAPABILITY] `insert_segment` with an explicit status inserts that status.
- [CAPABILITY] `set_segment_status` flips one segment's status.
- [CAPABILITY] `count_segments` returns the row count for one meeting.
- [CAPABILITY] `reap_orphaned_meetings(pool)` marks `live` meetings→'interrupted' when their `meeting-harness` operation_run is `interrupted` OR `running`-but-stale (heartbeat older than the inlined `interval '5 minutes'`); returns the heal count.
- [EDGE] `append_delta` NULL `window_start_s`: because Postgres treats NULLs as DISTINCT in a unique index, `ON CONFLICT` does NOT dedupe NULL-window rows — a second append with the SAME (meeting_id, entry_id, op) but NULL window WILL create a duplicate here (contrast with `transcript.flip_and_append` which guards with `WHERE NOT EXISTS`). Assert this exact NULL-window behavior so callers know the ledger seam's dedupe limits.
- [EDGE] `load_deltas` ordering is stable and by `id` (bigserial write order) even when created_at ties — assert with rows inserted in the same clock tick.
- [EDGE] `count_segments` is the before/after invariant the close pass must preserve (equal counts prove no lifecycle DELETE/TRUNCATE) — assert count unchanged across a full close (AC-STORE-13).
- [EDGE] `reap_orphaned_meetings` boundary: a meeting whose harness heartbeat is EXACTLY 5 minutes old vs strictly older (`< now() - interval '5 minutes'`).
- [EDGE] `reap_orphaned_meetings` uses the JOIN key `operation_runs.scope_id = meetings.id::text` (the one documented cast) — verify the cast matches how the harness WROTE scope_id (a mismatch silently reaps nothing).
- [NUANCE] `reap_orphaned_meetings` MUST NOT key off a nonexistent `meetings.last_heartbeat_at` (that column does not exist) — the heartbeat lives on `operation_runs`; a regression re-introducing it would crash or reap wrongly.
- [NUANCE/P0] `load_deltas` / `count_segments` / `pending_segment_ids` are MEETING-scoped by construction (never a cross-meeting sweep); since a meeting reaches exactly one tenant, this is the tenant-isolation guarantee for the note plane — assert a query for meeting A never returns meeting B's deltas even within the same tenant, and never across tenants.
- [FAILURE] `append_delta` on a note_deltas row that violates the `op IN ('add','patch','close')` CHECK raises.
- [FAILURE] `insert_segment` on a status outside {pending,comprehended,gap} violates the CHECK — raises.
- [FAILURE] `reap_orphaned_meetings` takes the POOL (not a borrowed conn) — it is a boot barrier that owns its own statement; passing a plain connection must fail loudly rather than half-run.

### transcript.py (atomic comprehension flip + close backfill)
- [CAPABILITY] `flip_and_append` in ONE transaction: appends a `seg-<id>` note delta AND flips the segment status→'comprehended'.
- [CAPABILITY] `flip_and_append` looks up `meeting_id` from the segment, so the delta lands under the right meeting.
- [CAPABILITY] `flip_and_append` guards the insert with `WHERE NOT EXISTS (… entry_id='seg-<id>' AND op='add')` so a RE-CLAIMED / replayed apply of the same segment is a true no-op (NULL-window-blind idempotency, AC-COAL-18 double_applies_allowed: 0).
- [CAPABILITY] `pending_segment_ids` returns still-`pending` segment ids for one meeting in stable creation order (the close reconciler's read).
- [CAPABILITY] `backfill_segment_as_lost` flips a still-`pending` segment→'lost'; the `AND status='pending'` guard makes it idempotent and never overwrites an already-comprehended segment.
- [EDGE/P0] idempotency proof: call `flip_and_append` TWICE for the same segment — exactly ONE `seg-<id>` delta exists and the segment is comprehended once (the WHERE NOT EXISTS guard holds even though window_start_s is NULL).
- [EDGE] atomicity: force the second statement (the UPDATE) to fail after the INSERT — the whole transaction rolls back, leaving the segment 'pending' and NO orphan delta.
- [EDGE] `backfill_segment_as_lost` on an already-'comprehended' segment is a no-op (guard) — the honest gap path never fakes comprehension nor un-comprehends.
- [NUANCE] `flip_and_append` writes a MINIMAL faithful delta (`op='add'`, `{"delta": ...}`) — the RICH fold path (`scribe.pipeline`) uses `notes.append_delta` directly with a real window_start_s; verify the two writers coexist and the rich path rides the UNIQUE INDEX while this seam rides the WHERE NOT EXISTS.
- [NUANCE] the §3.3 `transcript_segments` table has NO `note` column (the 0001 `note` column was dropped in 0004) — a regression writing to `transcript_segments.note` must fail; the comprehension is recorded ONLY as a note_delta.

---

## `src/db/repos/cost.py` — meeting_cost (additive spend upsert)

Product: the single persisted spend row per meeting; every writer (model/cache/transport/e2b)
converges on one row via an INCREMENTING upsert — a recycled orchestrator reloads accrued
spend, never resets to 0.

- [CAPABILITY] `record_cost` inserts a fresh `meeting_cost` row on first write; ON CONFLICT (meeting_id) it ADDS each usd field to the existing value and re-stamps `updated_at`.
- [CAPABILITY] `get_cost` returns the full spend row (all 5 usd fields + started_at + updated_at) or `None`.
- [EDGE/P0] additivity/idempotency-of-accrual: N sequential `record_cost` calls SUM correctly (never overwrite) — a recycle mid-meeting that re-records must not double-count nor reset; assert cumulative total after several partial writes.
- [EDGE] concurrency: two overlapping `record_cost` for the same meeting_id — the `= existing + EXCLUDED` upsert is atomic per-statement; assert the final total equals the sum of both increments (no lost update) under real concurrent connections.
- [EDGE] a zero-cost call (all defaults 0.0) still upserts (creates the row / bumps updated_at) without changing totals.
- [FAILURE] `record_cost` with a `meeting_id` not in `meetings` violates the PK/FK (`meeting_id … REFERENCES meetings(id)`) — raises (spend can only attach to a real meeting).
- [NUANCE] `record_cost` is NOT wrapped in a repo transaction (it is a raw module) — the CALLER owns the tx; the single-statement upsert is self-atomic so a bare call is safe, but a multi-write caller must wrap; verify both.
- [NUANCE] `meeting_cost` reaches tenant transitively via `meeting_id REFERENCES meetings(id)` — confirm there is exactly one row per meeting (PK is meeting_id) and no cross-meeting bleed.

---

## `src/db/repos/drafts.py` — staged_drafts (human-in-the-loop offer plane; GCS pointer)

Product: a draft is durable the moment it is proposed; the full body lives in GCS
(Object-Versioned), the row carries the `artifact_ref` pointer so a human can accept it long
after the sandbox is torn down (Law 3: world-touching = staged draft behind a click).

- [CAPABILITY] `insert_draft` inserts (meeting_id, kind, summary, artifact_ref, status='proposed') and returns the row incl. generated draft_id + created_at.
- [CAPABILITY] `get_draft` resolves a draft by draft_id, or `None`.
- [CAPABILITY] `set_draft_status` updates one draft's status.
- [CAPABILITY] `list_drafts_for_meeting` returns EVERY draft for ONE meeting, oldest-first (`ORDER BY created_at ASC, draft_id ASC`).
- [NUANCE/P0] `list_drafts_for_meeting` is scoped to a SINGLE meeting_id on purpose (the `/m/{meeting_id}` home is ONE meeting's drafts, never a cross-meeting dashboard) — verify it NEVER widens beyond the one id, and that the caller has proven meeting→tenant server-side (a cross-tenant meeting_id would leak another tenant's drafts if the upstream check is skipped — audit that check).
- [EDGE] `list_drafts_for_meeting` ordering is deterministic even when created_at ties (secondary sort on draft_id).
- [EDGE] `get_draft` / `list_drafts_for_meeting` return `None` / `[]` for an unknown meeting (never invent).
- [NUANCE] `artifact_ref` is an OPAQUE GCS pointer stored as text — the row is durable even after the sandbox holding the body is gone; verify a draft accepted long after teardown can still resolve its GCS body (the GCS object must be object-versioned + retained beyond sandbox TTL). [WIRING to GCS — see cross-subsystem section.]
- [NUANCE] the credential boundary (Law 3): the row + GCS body are staged; NO push/send happens until a human click — verify `set_draft_status` is the ONLY transition and that a 'proposed' draft never auto-applies.
- [FAILURE] `insert_draft` with a `meeting_id` not in `meetings` violates the FK — raises.
- [EDGE] `set_draft_status` on an unknown draft_id is a 0-row UPDATE (no error, no invented row) — assert the caller treats 0 rows as "not found".

---

## `src/db/repos/sessions.py` — server-side session records

Product: server-side session behind a signed cookie; binds (user_id, tenant_id).

- [CAPABILITY] `create_session(user_id, tenant_id)` inserts a `sessions` row and returns the generated id.
- [CAPABILITY] `get_session(session_id)` returns (id, user_id, tenant_id) or `None`.
- [NUANCE/P0] the session row is the SOURCE of the caller's tenant_id for the whole request — `get_session` MUST return the tenant the session was created with; a tampered/forged session_id resolves to `None` (unknown), never to another tenant's row (the id is a uuid PK, not guessable — verify no enumeration leak).
- [FAILURE] `create_session` with a user_id/tenant_id not in their FK targets raises (a session can only bind a real user + tenant).
- [EDGE] `get_session` for a nonexistent/expired id returns `None` (fail-closed → caller must reject the request, never default to a tenant).
- [NUANCE] sessions carries no explicit expiry column in the schema — verify expiry/rotation is enforced elsewhere (cookie TTL / app layer), or flag it as a gap (a never-expiring server session is a security risk).

---

## `src/db/repos/identity.py` — tenants + users (A-009 tenant reachability)

Product: sign-in creates-or-loads a user keyed by email; each new user is bound to a tenant so
every downstream row reaches a tenant.

- [CAPABILITY] `upsert_user_by_email` returns the existing (id, tenant_id) when the user exists AND already has a tenant_id (no new tenant minted).
- [CAPABILITY] for a brand-new email it mints a NEW tenant (name=email) then inserts the user bound to it.
- [CAPABILITY] the user insert uses `ON CONFLICT (email) DO UPDATE SET tenant_id = COALESCE(users.tenant_id, EXCLUDED.tenant_id)` — an existing user with a NULL tenant gets backfilled; an existing user WITH a tenant keeps it.
- [EDGE/P0] concurrency: two simultaneous first-sign-ins for the SAME new email — must NOT create two tenants bound to the same user (email is UNIQUE, so the second INSERT conflicts; but the tenant INSERT happens BEFORE the conflicting user INSERT — verify an ORPHAN tenant row can be created by the loser of the race, and whether that is acceptable / cleaned up). This is a real race in the current SELECT-then-INSERT-tenant-then-upsert-user flow.
- [EDGE] a user that exists but has NULL tenant_id (legacy) is backfilled on next sign-in (the `existing["tenant_id"] is not None` guard falls through to the mint+upsert).
- [NUANCE/P0] each user reaches exactly ONE tenant — verify a user can NEVER be re-pointed to a different tenant on a later sign-in (COALESCE keeps the first tenant); a bug flipping tenant_id would move a user's whole data view across the isolation boundary.
- [NUANCE] a new tenant's `name` is the email (PII in tenants.name) — confirm that is intended and not logged/leaked.
- [FAILURE] the tenant INSERT failing (constraint) must not leave a user bound to a nonexistent tenant (the user insert has an FK on tenant_id).

---

## `src/db/repos/webhooks.py` — webhook_events (the ONLY callback-durability surface)

Product: idempotent INSERT deduped by delivery_guid; a duplicate delivery is a no-op; a
boot/periodic drain processes pending rows. There is no general in-Postgres event bus.

- [CAPABILITY] `insert_event` inserts a 'pending' row and returns `True` when newly inserted, `False` when `ON CONFLICT (delivery_guid) DO NOTHING` deduped it (at-least-once → exactly-once via the unique guid).
- [CAPABILITY] `_derive_provider` classifies 'github' when payload carries ref/after/repository/installation/commits; else 'recall' (default when ambiguous).
- [CAPABILITY] `_derive_sha` returns `after`/`sha` for a GitHub delivery, else `None`.
- [CAPABILITY] explicit `provider=`/`sha=` args override the derivation.
- [CAPABILITY] `list_pending` returns pending rows ordered by `created_at` (the drain order).
- [CAPABILITY] `mark_processed` flips a row→'processed' and stamps `processed_at=now()`.
- [EDGE/P0] dedupe: the SAME delivery_guid delivered twice inserts once — `insert_event` returns True then False; assert exactly one row and the drain processes it once (no double-side-effect like a duplicate re-index).
- [EDGE] concurrency: two instances INSERT the same delivery_guid simultaneously — exactly one wins (True), the other gets False; no duplicate row, no crash (ON CONFLICT handles the race).
- [EDGE] `_derive_provider` on an ambiguous payload (none of the github keys) defaults to 'recall' — verify a real Recall bot-status payload (event/type/bot_id) classifies correctly and never mis-files as github.
- [EDGE] `list_pending` orders by `created_at` (NOT id) — verify the drain FIFO holds even after the 0005 reconciliation retained created_at.
- [FAILURE] a provider value outside {github,recall} (e.g. an explicit bad `provider=`) violates the CHECK constraint — raises at write time (fail-closed on the schema), never a mis-classified row.
- [FAILURE] `insert_event` with a NULL payload path: `payload or {}` → `'{}'::jsonb` (payload is NOT NULL in the canonical schema) — assert a no-payload delivery still records rather than crashing on the NOT NULL.
- [NUANCE] the drain must be idempotent end-to-end: a row picked up but crashing before `mark_processed` stays 'pending' and is retried — verify a partial-drain does not lose the event nor double-apply its effect.
- [NUANCE] webhook_events.tenant_id is NULLABLE (0003) and the dedupe path does NOT use it — verify a delivery is resolved to its tenant by CONTENT (bot_id→meeting→tenant / installation→repo→tenant) at processing time, and that a cross-tenant guid collision (astronomically unlikely but) can't misroute (guids are provider-global unique).

---

## `src/db/config.py` sandbox JWT accessors — secret-adjacent tunables

- [NUANCE] `sandbox_jwt_ttl_s` (default 900) and `sandbox_jwt_refresh_margin_s` (default 300) govern the per-sandbox HS256 JWT lifecycle — verify TTL > refresh margin (a margin ≥ TTL would re-mint constantly / never present a valid token); assert the invariant.
- [NUANCE] these are TUNABLES, not the secret itself — verify the signing key comes from Secret Manager (not this config, not env-inline), per the secrets hard rule.

---

## `migrations/versions/*` — the canonical schema (Alembic, Postgres only)

Product: the single source of truth for the DDL; human-gated before PROD; each revision must
upgrade AND downgrade cleanly on a scratch DB.

- [CAPABILITY] `alembic upgrade head` applies 0001→0010 cleanly on an empty DB (full-chain smoke).
- [CAPABILITY] `alembic downgrade base` reverses the full chain cleanly (every `downgrade()` runs).
- [EDGE] `alembic upgrade head` then `downgrade -1` then `upgrade +1` per-revision round-trips each migration (0004, 0005, 0007, 0009, 0010 especially — the ones that drop/recreate or de-dupe).
- [NUANCE/migration-safety] 0004 DROPs and RECREATES note_deltas + transcript_segments at the §3.3 shape — on a DB with existing early-schema rows this is DESTRUCTIVE; verify the drop is intended (pre-prod) and that PROD application is human-gated.
- [NUANCE/migration-safety] 0005 is a FORWARD reconciliation (0001 never edited in place): provider added with server_default 'recall' then DROPPED so new writes must name it; payload backfilled to '{}' before NOT NULL; received_at backfilled from created_at; status/provider CHECKs added — verify each backfill on a DB seeded with legacy rows so `upgrade head` succeeds.
- [NUANCE/migration-safety] 0007 adds column DEFAULTs to NOT NULL append-plane columns so a bare `INSERT (tenant_id)` (the offboarding oracle's seed) succeeds WITHOUT relaxing NOT NULL — verify a real §3.3 insert still names every column (defaults never observed on the product path) and the replay UNIQUE INDEX is unaffected.
- [NUANCE/migration-safety] 0007 also creates `meeting_cost_telemetry` (distinct from `meeting_cost`) — verify which table each writer targets (a writer hitting the wrong one silently loses spend telemetry).
- [NUANCE/migration-safety] 0010 DELETEs duplicate repos rows (keep earliest created_at/ctid) BEFORE creating the UNIQUE index — verify on a DB seeded with duplicate (tenant_id, full_name) rows that de-dupe keeps the row meetings reference (no orphaned meeting via `repo_id`) and the index then creates cleanly.
- [NUANCE/P0 tenant-reachability] EVERY durable tenant-scoped table reaches `tenants`: tenants(root), users(FK), repos(FK), meetings(FK), sessions(FK), staged_drafts(via meeting_id), meeting_cost(via meeting_id), note_deltas(nullable FK), transcript_segments(nullable FK), connect_readiness(NOT NULL FK), repo_maps(NOT NULL FK in PK), webhook_events(nullable FK), meeting_cost_telemetry(nullable FK) — assert a schema audit that NO durable tenant-scoped table lacks a path to tenants (AC-TEN-001).
- [NUANCE] `operation_runs` deliberately has NO tenant_id (coordination store keyed by text scope_id, contract-pinned column set AC-SUB-001) — verify the pinned column set is exactly the 12 documented columns and no migration adds/removes one.
- [EDGE] `operation_runs_one_running_per_scope` PARTIAL UNIQUE INDEX (scope_id, operation_type) WHERE status='running' — assert only ONE 'running' row can exist per (scope, type) at a time (a second concurrent claim conflicts); a completed/interrupted row does not block a new claim.
- [EDGE] `note_deltas_source_window_uniq` (meeting_id, window_start_s, entry_id, op) — assert dedupe fires for equal keys AND that NULL window_start_s rows are treated as distinct (the documented NULL-blind-ness that `flip_and_append` compensates for).
- [EDGE] `repo_maps` PK (tenant_id, repo, sha) with tenant_id FIRST — assert `load_map(tenantB, repo, sha)` can NEVER return tenantA's row for the same (repo, sha) (PM-STORE-02).
- [FAILURE] applying an out-of-order / missing-parent revision fails loudly (the revision DAG down_revision chain 0001→…→0010 is intact — verify no dangling/duplicate revision ids).
- [NUANCE] all migrations are RAW `op.execute(DDL)` (no ORM autogenerate) so the column set stays the single source of truth matched byte-for-byte to the repo SQL — verify each repo's RETURNING/SELECT column list matches the live DDL (a drift = runtime KeyError).

---

## `repo_maps` store — `services/premeeting/src/premeeting/map_store.py` (part of the substrate)

Product: the pre-meeting system's ONE durable derived artifact — the dense `index.md` repo map,
stored in Postgres so it is readable from ANY instance and survives host recycle (the clone is a
rebuildable cache; the map is durable). Belongs to the substrate my tree covers.

- [CAPABILITY] `save_map` upserts map text for (tenant_id, repo, sha) — ON CONFLICT (tenant_id, repo, sha) overwrites `map` and re-stamps `built_at` (a re-verify at the same SHA produces a fresh map).
- [CAPABILITY] `load_map` reads the exact map bytes for (tenant_id, repo, sha), or `None` on a miss (fail-closed).
- [CAPABILITY] `load_latest_map` returns the most-recently-built (sha, map) for (tenant_id, repo) via `ORDER BY built_at DESC LIMIT 1`.
- [CAPABILITY] `MapStore` resolves a FRESH connection per call so a connect-trigger WRITE and a live-meeting READ on another instance hit the same durable row.
- [EDGE/P0] `load_map`/`load_latest_map` are ALWAYS filtered by tenant_id — prove tenant B calling `load_map(B, repo, sha)` returns `None` (or B's own) even when tenant A has a row for the identical (repo, sha); a cross-tenant map read is a P0 breach and unrepresentable at the PK.
- [EDGE] `save_map` byte-exact round-trip: `load_map` after `save_map` returns the identical map text (no truncation of a large `index.md`, no encoding mangling — text column, verify multi-MB maps).
- [EDGE] `load_latest_map` when two SHAs were built — returns the one with the newer `built_at` (a re-build at the SAME sha re-stamps built_at, so a fresh re-verify becomes "latest").
- [EDGE] `built_at` ties (two maps built in the same tick) — `ORDER BY built_at DESC` is non-deterministic on ties; verify the read is still tenant/repo-correct and flag if a deterministic tiebreak is needed.
- [FAILURE] `save_map` with a tenant_id not in `tenants` violates the FK — raises (a map always tenant-scoped).
- [NUANCE] the map SHA a meeting loads is the meeting's EXACT `pinned_sha` (via `load_map`, not `load_latest`) — verify the in-meeting path uses `load_map(pinned_sha)` so a push mid-meeting never swaps the resident understanding; `load_latest` is only for the "current map" convenience path.
- [WIRING] connect→index pipeline (`premeeting.pipeline` → `map_store.save`) is the WRITER; the in-meeting boot + Workroom mount are the READERS — the data crossing is the `index.md` text keyed by (tenant, repo, sha); verify writer's `repo` key == reader's `repo` key (both derived via `repo_name_from_url`) or the read misses.

---

## Cross-subsystem integration points

- [WIRING] control_plane boot (`server.py`) calls `open_pool(settings.database_url)` and stores `app.state.pool` — the single pool per instance; verify the DSN comes from Secret Manager and the pool is closed on shutdown (no leaked Cloud SQL connections across recycles).
- [WIRING] control_plane connect flow (`connect.py`) uses `db.repos.connect` via RAW psycopg3 autocommit connections (NOT asyncpg) — verify the connect readiness plane's driver/conn shape matches (`ensure_tenant`/`insert_install`/`mark_state`/`set_ready`/`set_not_ready`/`read_row` all sync, `%s` params) and never gets an asyncpg connection.
- [WIRING] webhook ingest (control_plane `github_webhook.py` + Recall callback) → `webhooks.insert_event`; the drain → `list_pending`/`mark_processed`; the boundary is the raw provider payload jsonb + the delivery_guid dedupe key.
- [WIRING] a GitHub push webhook resolves installation→repo→tenant and triggers a `repo_maps` REBUILD (map_store.save) at the pushed SHA — verify the rebuild writes under the SAME (tenant, repo) key the meeting will read, and that the pinned meeting keeps its old-SHA map (no swap mid-meeting).
- [WIRING] Recall bot-status webhook resolves `recall_bot_id`→`meetings.get_by_bot_id`→(tenant, repo, pinned_sha); the tenant returned is authoritative for every downstream write — the P0 cross-tenant audit point.
- [WIRING] meeting boot: `get_by_bot_id`/`get_by_id` → `get_repo_by_id` → `map_store.load_map(pinned_sha)` assembles the resident understanding + the in-meeting engine; verify the whole chain is tenant-consistent (every hop threads the same tenant_id).
- [WIRING] Workroom per-task code-intel mount uses `meetings.get_by_id(meeting_id)` (from `bundle.notes_ref`) to get (tenant_id, repo_id) to locate the per-tenant graph.db — verify a missing meeting fails CLOSED (mount degrades to no code_intel, never mounts another tenant's index).
- [WIRING] staged_drafts.artifact_ref ↔ GCS object-versioned body: the draft row is durable at proposal; the GCS body (written via `libs/http external.py` / the object store seam) must OUTLIVE the sandbox TTL so a human accept long after teardown resolves the body — verify GCS versioning + retention and that `artifact_ref` remains valid after the E2B sandbox is destroyed.
- [WIRING] cost writers (Scribe bare-Messages, the seam meter for wakes + Workroom) all converge on `cost.record_cost(meeting_id)` — verify all writers target `meeting_cost` (not `meeting_cost_telemetry`) so a recycled orchestrator reloads the true accrued spend.
- [WIRING] `libs.ops` reconcile (`reconcile.py`) reads `Database.last_activity_at(scope_id)` (the sandbox keepalive) + sweeps stale operation_runs — verify the ops→db dependency direction (ops depends on db, never the reverse) and that the reconcile spares a scope whose keepalive is fresh even if its operation_run heartbeat lags.
- [WIRING] tenant-offboarding sweep (`run_reconcile_sweep`, AC-INV-010) DELETEs an offboarded tenant's rows from EVERY durable tenant-scoped table via an unordered information_schema scan — verify it reaches ALL tenant-reachable tables (incl. note_deltas/transcript_segments/repo_maps/connect_readiness/meeting_cost_telemetry) and that 0007's defaults let a bare tenant-seed row exist to be swept; a table it CANNOT reach = a tenant-data-leak-after-offboard.
- [WIRING] `assert_reaper_ratio()` must be called on the boot path of every service that runs a reaper (control_plane + harness) BEFORE the first sweep — verify each boot calls it (a mis-config that skips the guard defeats the D-033 protection).
- [NUANCE/P0 — global isolation battery] For EVERY reader that takes a bare id without a tenant filter (`get_by_id`, `get_repo_by_id`, `get_draft`, `get_session`, `get_cost`, `get_operation_run`, `load_deltas`, `count_segments`), prove the CALLER established tenant ownership of that id upstream (server-side) — enumerate each call site and assert a forged/other-tenant id is rejected before the unscoped read, since the query itself does NOT enforce tenant.
- [NUANCE/P0 — dual-tenant integration test] Seed two full tenants (each: tenant, user, repo, meeting, note_deltas, transcript_segments, staged_drafts, meeting_cost, repo_map at same repo/sha) and run EVERY read as tenant B against tenant A's ids — assert every scoped read returns B's-only/None and every "unscoped-by-id" read is guarded by an upstream tenant check; ZERO cross-tenant rows returned anywhere.
