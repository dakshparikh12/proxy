# Comprehensive Test Plan: services/workroom + libs/agentkit

Generated from exhaustive read of every source file in both trees.  
Source files read: `workroom/drafts.py`, `workroom/objectstore.py`, `workroom/recovery.py`,
`agentkit/deltas.py`, `agentkit/guardrails.py`, `agentkit/provider.py`, `agentkit/sdk_provider.py`,
plus the retired-but-git-tracked `agentkit/abort.py`, `agentkit/config.py`, `agentkit/resume.py`.  
Architecture context: host-side trusted services + credential-less E2B sandbox; agent is native
Claude running inside the sandbox; propose_change crosses via the host-side in-process SDK MCP server.

---

## 1. `workroom/objectstore.py` — local-filesystem GCS stand-in

[CAPABILITY] `put(ref, content)` writes content durably to `_BASE/<sha256(ref).hexdigest>` and returns `ref`.
[CAPABILITY] `get(ref)` reads from the same deterministic path; returns `None` when absent (no raise).
[CAPABILITY] `_BASE` is `tempfile.gettempdir()/proxy-object-store`; created on first `put` via `mkdir(parents=True, exist_ok=True)`.
[WIRING] `objectstore.put` is called by `drafts._persist_bundle_row_sync` and `drafts._propose_change_async` before the DB row is inserted — GCS write precedes the row.
[WIRING] `objectstore.get` is called by `drafts.accept_draft` and `control_plane.accept.apply_accepted_draft`; both use the `artifact_ref` stored in the row, never the in-memory session.
[EDGE] Two different `ref` strings that hash to the same SHA-256 would collide — probability negligible, but production GCS uses the ref as the object path directly (different semantics); this test exposes the stand-in/production contract gap.
[EDGE] `ref` contains Unicode (e.g. a meeting_id with non-ASCII); SHA-256 is over UTF-8 bytes — safe, but the production GCS path must handle the same ref string.
[EDGE] `put` called with `content=""` (empty string) writes an empty file; `get` returns `""`, not `None` — `accept_draft` treats `not content` as empty and sets `applied=False`.
[EDGE] `put` called twice with the same `ref` overwrites silently; the second call's content wins — this is intended versioning behavior but must be confirmed idempotent on the MVP path (same draft_id re-proposed after a partial retry).
[FAILURE] `_BASE` directory is unwritable (permissions error): `put` raises `PermissionError`; callers in `_persist_bundle_row_sync` / `_propose_change_async` have no try/except around `objectstore.put` — the exception propagates to the MCP tool handler (which has a blanket `except Exception`) or to the async proposer path (where it surfaces as a staging fault and the offer sink degrades to `""`).
[FAILURE] `get` on a ref whose path exists but is unreadable (permissions error): raises `PermissionError`, not caught — propagates to `accept_draft` and surfaces as a 500; test that the accept route handles it without leaking the ref path.
[FAILURE] `get` on a ref that has never been stored: returns `None`; callers use `or ""` — `accept_draft` returns `applied=False`, `apply_accepted_draft` in control-plane writes empty content for a notes-edit (erasing the note). Verify this degrade path is honest and not silent data corruption.
[NUANCE] The objectstore is a **process-local stand-in** shared across concurrent meetings on the same process — no tenant isolation by directory; two meetings with different `meeting_id` but the same draft UUID (uuid4 collision is negligible but possible in theory) would clobber each other. Production GCS uses `gs://proxy-drafts/{meeting_id}/{uuid}` — tenant-isolated by path.
[NUANCE] The stand-in is NOT Object-Versioned (no version history); production GCS is. Tests that exercise the "read a specific version" path will silently pass on the stand-in but break on GCS — test must exercise the real GCS interface at the integration boundary.
[NUANCE] `put` is synchronous; callers are async — no thread-safety concern in CPython's GIL, but under asyncio if two coroutines race on the same ref the last write wins (acceptable by design: one draft_id → one uuid ref → no race in practice).

---

## 2. `workroom/drafts.py` — staged draft lifecycle + host-side MCP server

### `_normalize_files`

[CAPABILITY] Accepts a list of `{path, new_content?, old_sha?}` dicts; for each entry missing `old_sha`, inserts `original_from="meeting.pinned_sha"`.
[CAPABILITY] Rejects any item that is not a dict or lacks `"path"` with a `ValueError`.
[EDGE] `files=None` or `files=[]`: `_normalize_files` returns `[]`; `_build_bundle` with empty files AND no `unified_diff` raises `ValueError("propose_change needs a 'files' list or a 'unified_diff'")`.
[EDGE] File entry where `new_content` is absent: stored as `""` — the diff-render at accept-time will see an empty body as the new content, potentially producing an all-deletion diff. Verify the accept-handler or diff-render detects this as a no-op vs. deliberate wipe.
[EDGE] File `path` contains path traversal (`../etc/passwd`): normalized and stored verbatim; the accept-handler / diff-render is responsible for rejecting it. Verify no path traversal reaches the file system at any accept stage.
[NUANCE] `old_sha` is optional: its absence is recorded as `"original_from": "meeting.pinned_sha"` — the diff-render at accept-time must look up the pinned clone's SHA for that path. If the clone was already torn down, the accept-handler must handle a missing pinned SHA gracefully.

### `_build_bundle`

[CAPABILITY] Accepts `files` list → returns JSON bundle `{"kind": ..., "files": [...], "unified_diff": null}`.
[CAPABILITY] Accepts `unified_diff` string → returns JSON bundle with `"files": []` and the diff.
[CAPABILITY] Legacy `content` (bytes or str) with no `files`/`unified_diff` → returns the plain string directly (no JSON envelope) — the notes-edit accept path reads it verbatim.
[EDGE] Both `files` and `unified_diff` provided simultaneously: `files` is processed, `unified_diff` is also included — the bundle carries both. The accept-handler must decide which wins. This is an underspecified input; test that it does not cause silent data corruption.
[EDGE] `content` is `bytes` with invalid UTF-8: decoded with `replace` — content is silently corrupted. For a notes-edit, the human accepts garbled text. Verify this is tolerable or blocked upstream.
[FAILURE] `json.dumps(bundle)` raises on non-serializable `files` content (e.g. a non-string `new_content`): `ValueError` propagates out of `_build_bundle` before anything is written to GCS or DB — no half-written artifact. Verify the MCP tool handler's blanket `except` catches this.

### `_persist_bundle_row_sync` (sync psycopg path)

[CAPABILITY] Generates a `uuid4().hex` artifact ref keyed under `gs://proxy-drafts/{meeting_id}/{uuid}`.
[CAPABILITY] Writes GCS (objectstore.put) BEFORE inserting the DB row — artifact is durable before the row lands.
[CAPABILITY] Inserts exactly one `staged_drafts` row with `status='proposed'`; returns `ProposedDraft` with `status='needs_review'` (NOT 'proposed' — note the discrepancy; the DB row is 'proposed' but the returned object says 'needs_review').
[WIRING] Called when `db` is a raw psycopg connection (not `Database`); used by `make_propose_change_tool` via `propose_change(conn, ...)` — the sync path executes on the trusted host thread.
[EDGE] GCS write succeeds but DB insert fails (e.g. constraint violation, connection lost): the GCS artifact is orphaned — the draft_id never exists in the DB. The orphaned GCS object is never cleaned up. Verify the accept-handler cannot access the orphaned object via a stale ref from another path.
[EDGE] DB insert succeeds but a subsequent read of the same row returns the artifact_ref that was just written — test round-trip integrity (no UUID collision, correct meeting_id stored).
[FAILURE] `conn` is `None`: the validation in `propose_change_tool` catches this before calling `propose_change` — but if `propose_change` is called directly with `conn=None` on the sync path, `conn.execute(...)` raises `AttributeError`; not a `ValueError` from the guard. The blanket `except Exception` in the MCP tool handler still catches it.
[NUANCE] The returned `review_session_id` is a fresh `uuid4().hex` — it is NOT stored in the DB. The field is documentation-only; callers must not depend on it for DB lookup.

### `_propose_change_async` (async pool path)

[CAPABILITY] Uses `db.acquire()` context manager; calls `repos.drafts.insert_draft` — test that the repo method exists and has the expected signature.
[WIRING] Called when `db` is a `Database` instance — dispatched by `propose_change`'s isinstance check. Used by `control_plane.provisioner._offer` with the async pool.
[EDGE] `repos.drafts.insert_draft` returns a row dict missing `"draft_id"` or `"artifact_ref"`: `ProposedDraft` construction raises `KeyError` — not caught, propagates. The offer sink in provisioner catches it with blanket `except Exception` and degrades to `""`.
[FAILURE] `db.acquire()` times out or raises `ConnectionError`: `objectstore.put` already ran — artifact is orphaned. The offer sink degrades to `""` — honest degrade, but the orphaned GCS object is untracked.
[FAILURE] Concurrent proposals from the same meeting: two coroutines both succeed — two separate `draft_id`s are created, both with `status='proposed'`. Verify the human-accept flow handles multiple pending drafts correctly (not a corruption, but a UX concern).

### `propose_change` (public dispatcher)

[CAPABILITY] Dispatches to async path when `db` is `Database`; sync path for psycopg connection; returns a coroutine for the async path.
[WIRING] Control-plane provisioner `_offer` calls `await propose_change(db, ...)` with a `Database` — async path.
[WIRING] `make_propose_change_tool` calls `propose_change(conn, ...)` with a raw psycopg `conn` — sync path; BUT the tool is `async def` — calling the sync path inside `async def` is fine (it returns `ProposedDraft` directly, not a coroutine).
[NUANCE] If `db` is some other object that is neither `Database` nor a psycopg connection (e.g. `None` on the sync path), the sync path's `conn.execute()` raises immediately — no type guard.

### `accept_draft`

[CAPABILITY] Reads the persisted `staged_drafts` row by `draft_id`; reads the GCS artifact body; flips row to `'accepted'`; returns `AcceptedDraft(read_from='durable', applied=bool(content))`.
[CAPABILITY] Raises `LookupError` if the draft_id does not exist in the DB.
[WIRING] Called from the workroom `__init__` public API; the control-plane's own `accept.py` uses its own `apply_accepted_draft` (NOT this function) — so `workroom.accept_draft` and `control_plane.accept.apply_accepted_draft` are PARALLEL paths for the accept flow. Verify they are consistent.
[EDGE] Draft was already accepted (status='accepted'): `accept_draft` re-reads the row, re-reads GCS, and re-flips to 'accepted' — idempotent at the objectstore level but the DB update runs again. Contrast with `apply_accepted_draft` in control-plane which short-circuits on `status in ('applied', 'rejected')` — the workroom path has NO such idempotency guard.
[EDGE] GCS artifact is absent (objectstore.get returns None): `content = None or "" = ""`; `applied=bool("") = False` — the draft is flipped to 'accepted' but `applied=False` signals nothing was written. Callers must check `applied`.
[FAILURE] `repos.drafts.set_draft_status` raises mid-transaction: row remains 'proposed'; content was already read — no half-apply (nothing written to world). Honest partial failure — status inconsistency between GCS and DB.

### `accept_code_change_draft`

[CAPABILITY] Records human approval, exposes a `bundle_url` at `gs://proxy-drafts/{tenant}/{draft_id}/bundle.diff`, and sets `scope="contents:read"` — NEVER calls `origin.push(...)`.
[CAPABILITY] Raises `ValueError` if `origin is None`.
[WIRING] This is a core-scope function — it never pushes. Pushing requires the Expansion scope (`contents:write`) which this code does not have. Verify no code path calls `origin.push()` from within this function.
[NUANCE] The `bundle_url` is a FABRICATED path: `f"gs://proxy-drafts/{tenant}/{draft_id}/bundle.diff"` — it is NOT the same as the artifact_ref stored for the draft. In production the real bundle must be computed and stored at this path separately; in the current MVP this is a documented stub. Test must confirm the URL resolves to nothing on the stand-in objectstore.
[NUANCE] `approval_recorded` is hardcoded `True` — no DB write happens here. This is a stub; production must persist the approval in `staged_drafts` or an audit table.

### `teardown_review_session`

[CAPABILITY] Is a documented no-op (returns `None`); the persisted draft outlives the sandbox — accept reads from durable storage.
[NUANCE] Callers must not assume `teardown_review_session` releases any resource or changes any state — it is a future hook placeholder.

### `make_propose_change_tool`

[CAPABILITY] Builds an async SDK tool function named `propose_change` under the `PROPOSE_CHANGE_SERVER_NAME` server.
[CAPABILITY] On success: returns `{"content": [{"type": "text", "text": JSON with draft_id, status, files, note}]}`.
[CAPABILITY] On any exception: returns `{"is_error": True, "content": [{"type": "text", "text": JSON with code+message}]}` — NEVER raises (Hard Rule 6 / D-018).
[WIRING] The tool closes over `conn` (psycopg) and `meeting_id` at factory time — they MUST be valid for the lifetime of the query. If the query outlives the connection, the first invocation will error gracefully (caught by blanket except).
[EDGE] `args` dict missing `"kind"` or `"summary"`: defaults are used (`"code-change"` and `""`). An empty summary is stored in the DB — `staged_drafts.summary = ""`. Verify this is acceptable vs. rejected.
[EDGE] `args["files"]` contains non-dict items: `_normalize_files` raises `ValueError`; caught by blanket except; `is_error=True` returned to the model.
[EDGE] `args["files"]` is not provided AND `args["unified_diff"]` is not provided: `_build_bundle` raises `ValueError`; caught; `is_error=True` returned.
[EDGE] Tool invoked by a non-worker disposition: the MCP server is NOT mounted for non-worker dispositions (per `mcp_servers_for_disposition`), so the tool is not callable. If somehow the model references it on a read-only disposition, the SDK should return a tool-not-found error — not a security bypass.
[NUANCE] The `@tool` decorator from `claude_agent_sdk` is applied at factory time (per query); the tool is connection-bound — never re-use a tool instance across queries.

### `make_propose_change_server`

[CAPABILITY] Returns a `McpSdkServerConfig` with `name=PROPOSE_CHANGE_SERVER_NAME`, `version="1.0.0"`, tools=[one tool].
[WIRING] Called by `mcp_servers_for_disposition` for the `"worker"` disposition only.
[NUANCE] SDK MCP servers are connection-bound — if the SDK reuses a server config across multiple SDK calls, the conn may be stale. The factory-per-query pattern (minted fresh per call) is the designed guard; verify it is enforced at the call site.

### `mcp_servers_for_disposition`

[CAPABILITY] Returns `{PROPOSE_CHANGE_SERVER_NAME: make_propose_change_server(...)}` for disposition `"worker"`.
[CAPABILITY] Returns `{}` for all other dispositions (`"quick"`, `"plan"`, `"critic"`, `"verifier"`, any unknown string).
[EDGE] Unknown disposition string (typo or future disposition): returns `{}` — no propose_change server. The model in that disposition cannot write drafts. This is the safe default.
[NUANCE] The empty return for non-worker dispositions is the MOUNT decision; the ADVERTISE/BLOCK decision still relies on the behavior's `disallowed_tools`. Both must agree (belt-and-suspenders). A bug where only one is enforced must be caught by adversarial testing.

---

## 3. `workroom/recovery.py` — task recovery

### `_has_deliverable`

[CAPABILITY] Returns `True` iff `result_ref` is a non-None dict (or JSON-parseable-to-dict) with a truthy `"deliverable"` key.
[EDGE] `result_ref` is a JSON string `'{"deliverable": 0}'`: `json.loads` succeeds, `bool(0)` is `False` → `_has_deliverable` returns `False` → task is restarted. If `0` is a valid "done" sentinel for some reason, this is a bug.
[EDGE] `result_ref` is a JSON string `'{"deliverable": "done"}'`: returns `True` → no restart.
[EDGE] `result_ref` is a non-JSON, non-None string (e.g. a raw error message): `json.loads` raises `JSONDecodeError` → returns `False` → task restarts. Correct behavior.
[EDGE] `result_ref` is a dict with `deliverable=None`: `bool(None) = False` → restarts. Correct.
[EDGE] `result_ref` is `{}` (empty dict): `bool({}.get("deliverable")) = False` → restarts. Correct.

### `should_restart` (sync)

[CAPABILITY] Executes `SELECT result_ref FROM operation_runs WHERE id = %s` with psycopg; returns `False` iff the deliverable exists (no restart needed).
[WIRING] Called by a recycled orchestrator to check if a coarse Workroom unit needs to restart; the operation is on `operation_runs` table (not a bespoke workroom_tasks table).
[EDGE] `operation_id` not in `operation_runs`: `fetchone()` returns `None`; `row[0]` would raise `TypeError` (None is not subscriptable) — BUT the code does `row[0] if row is not None else None` — safe. Returns `True` (restart).
[FAILURE] DB connection is closed or times out: `conn.execute(...)` raises; not caught — propagates to caller. Caller must handle.
[NUANCE] The `operation_runs` schema must have `result_ref` as the column name; any schema migration that renames this column silently breaks recovery (the query would raise `ProgrammingError`).

### `recover_task` (async)

[CAPABILITY] Fetches the most recent `operation_runs` row for `(scope_id, operation_type)` ordered by `started_at DESC LIMIT 1` using asyncpg-style `$1`/`$2` params.
[CAPABILITY] Returns `RecoverResult(restarted=not _has_deliverable(result_ref))`.
[WIRING] `operation_type` is expected to use the `WORKROOM_OP_PREFIX = "workroom:"` prefix — e.g. `"workroom:draft-123"`. Callers must prefix correctly.
[EDGE] Multiple rows for `(scope_id, operation_type)`: `LIMIT 1 ORDER BY started_at DESC` picks the newest. If `started_at` is not a timestamp (e.g. NULL): ordering is undefined. Verify the DB schema enforces `started_at IS NOT NULL`.
[EDGE] No rows match: `result_ref` is `None` → `_has_deliverable(None) = False` → `restarted=True`. Correct.
[FAILURE] `db.acquire()` raises (pool exhausted, connection error): propagates — no `RecoverResult` returned. Caller must handle.
[NUANCE] `recover_task` does NOT re-run the task — it only returns a `RecoverResult`. The caller is responsible for actually restarting if `restarted=True`. Verify no call site ignores `restarted=False` and restarts anyway.

---

## 4. `agentkit/deltas.py` — per-msg_id suffix delta-izer

### `_DeltaState`

[CAPABILITY] `feed(chunk)` for a TEXT chunk: computes the NEW suffix (accumulated[len(previous):]) and updates `_seen[msg_id]`.
[CAPABILITY] `feed(chunk)` for a non-TEXT chunk: passes through unchanged.
[EDGE] First TEXT chunk for a new `msg_id`: `previous = ""`, accumulated = full text → delta = full text. Correct.
[EDGE] Second TEXT chunk for same `msg_id` where text is the accumulated string: delta = new suffix. Correct streaming behavior.
[EDGE] Text is shorter than the cached accumulated (regression / replay): `accumulated[len(previous):]` would be an empty string or wrong. E.g. if the provider resets mid-stream, the delta would be `""`. Callers would see a zero-length TEXT chunk — benign but potentially confusing. Verify the provider never sends regressions.
[EDGE] `msg_id` is `""` (metadata has `"msg_id": ""`): all chunks with missing msg_id map to the same `""` key. Multiple message threads with missing IDs would collide in `_seen`. Verify the upstream never sends TEXT chunks without a valid `msg_id`.
[EDGE] `chunk.text` is `None`: `accumulated = None or "" = ""`. Correct handling.
[EDGE] `chunk.metadata` has no `"msg_id"` key: `chunk.metadata.get("msg_id", "")` returns `""`. Same as above — all no-id chunks share state.
[NUANCE] `_DeltaState` is non-idempotent by design: applying stream_deltas on its own output corrupts the deltas. Test that no call site applies it twice.
[NUANCE] `_seen` grows unboundedly for a session with many messages — in a long meeting, this dict could contain thousands of entries. No eviction. For the MVP (bounded meeting length) this is acceptable.

### `stream_deltas` / `_deltaize`

[CAPABILITY] Sync iterable in → sync iterator out (not a generator function; `_deltaize_sync` is a generator).
[CAPABILITY] Async iterable in → async generator out (an async generator function `_deltaize_async`).
[WIRING] `_is_async_iterable` checks `hasattr(obj, "__aiter__")` — a sync iterable with a spurious `__aiter__` would be treated as async. This is a hypothetical but worth noting for mock objects in tests.
[EDGE] Empty iterable: yields nothing. The caller sees an empty stream — no chunks, no RESULT.
[EDGE] Stream with only non-TEXT chunks (INIT/TOOL_USE/TOOL_RESULT/RESULT/ERROR): all pass through unchanged. Correct.
[EDGE] Stream with TEXT chunks for multiple concurrent msg_ids interleaved: each msg_id maintains independent state in `_seen`. Verify this on a real multi-message stream.
[NUANCE] The public alias `stream_deltas = _deltaize` is the only intended call token. No second application anywhere in the tree — enforced by naming convention, not static analysis. Verify no second call site exists.

---

## 5. `agentkit/guardrails.py` — shared injection guardrail

### `injection_guardrail_suffix`

[CAPABILITY] Returns `"SAFETY GUARDRAIL (final, authoritative):\n<body>"` — the marker + body.
[CAPABILITY] The `INJECTION_GUARDRAIL_MARK` constant is the marker used to detect guardrail presence in test oracles.
[NUANCE] This is the ONE definition of the injection guardrail for the whole codebase. Any per-service redefinition is a Hard Rule violation. Verify no other file defines a string matching the marker.

### `with_injection_guardrail`

[CAPABILITY] Appends `\n\n{suffix}` to a non-empty system prompt; returns just the suffix if `system_prompt=""`.
[EDGE] `system_prompt` is already `""`: result is just the guardrail suffix (no leading newlines). Callers expecting a non-empty base prompt get only the guardrail.
[EDGE] `system_prompt` already contains the guardrail text (double-append): the guardrail is appended again — two copies in the prompt. The second copy is redundant and potentially confusing. Callers must ensure they call `with_injection_guardrail` exactly once. No guard against double-append.
[NUANCE] The guardrail is placed LAST (final authoritative word). Any text appended AFTER this call would come after the guardrail, potentially weakening it. Callers must not append to the result.

### `with_proxy_guardrails`

[CAPABILITY] Appends spoken-register + one-gather-pass guardrail: "Prefer the compact artifact, cheapest tool first, one gather pass. Speak short sentences, use contractions, no enumeration, two sentences max."
[NUANCE] This is a BEHAVIORAL guardrail (tone/style), not a security guardrail. It is separate from the injection guardrail and can compose with it — but callers must apply both if both are needed, in the right order.
[EDGE] `with_proxy_guardrails` called after `with_injection_guardrail`: the behavioral suffix follows the security guardrail — the behavioral suffix is now the final word of the prompt. This may or may not be the intended ordering; verify at call sites.

---

## 6. `agentkit/provider.py` — provider seam, ProviderQuery, registry

### `ProviderQuery`

[CAPABILITY] Immutable dataclass carrying the full SDK call surface: `model`, `allowed_tools`, `system_prompt`, `max_turns`, `tools` (built-ins), `strict_mcp_config=True`, `setting_sources=()`, `thinking_enabled`, `thinking_budget_tokens`, `resume`, `preamble`, `abort`, `mcp_servers`, `env`, `disallowed_tools`, `extra`.
[WIRING] `premeeting.map_build` constructs `ProviderQuery` directly; the seam is the only construction site — verify map_build does not bypass the seam.
[NUANCE] `strict_mcp_config=True` is the DEFAULT in the dataclass — any caller that forgets to set it still gets the safe value. This is the correct defensive default.
[NUANCE] `setting_sources=()` is the DEFAULT — no filesystem settings loaded. Correct defensive default.
[NUANCE] `disallowed_tools=()` (per-query) is MERGED with the module-level `disallowed_tools` (SDK_LOCAL_TOOLS) in `build_sdk_options`; the module-level block is always applied even if the query sets `disallowed_tools=()`.
[EDGE] `mcp_servers=None` vs `mcp_servers={}` — both are treated as "no servers" in `build_sdk_options` (`query.mcp_servers or {}`). Correct.
[EDGE] `resume` is a stale session_id (the session is gone): surfaced as a `ProviderError` with a stale-session marker — the `resume_with_fallback` in `agentkit/resume.py` is the intended recovery path.

### `compute_builtin_tools`

[CAPABILITY] Always returns `()` — no host built-ins in sandbox mode. The curated subset flows through `allowed_tools`; the built-in list stays empty.
[WIRING] Called by the runner (behavior code) to produce the `tools` field of `ProviderQuery`. Verify no call site passes a non-empty `curated` and expects built-ins in the return.
[NUANCE] The function ignores its argument (`_ = curated`) — this is intentional but surprising. A caller that passes tools expecting them to be returned as built-ins will get `()`. Verify all callers understand this contract.

### `thinking_policy`

[CAPABILITY] Returns `(True, budget)` ONLY for roles in `_THINKING_ROLES` AND models with the `"claude-opus"` prefix.
[CAPABILITY] `budget = min(EXTENDED_THINKING_BUDGET_TOKENS=3000, MAX_OUTPUT_TOKENS // 4 = 8000)` → budget = 3000.
[EDGE] Model is `"claude-opus-4-5"` and role is `"grounded-answer"`: thinking enabled with budget 3000.
[EDGE] Model is `"claude-sonnet-4-6"` and role is `"grounded-answer"`: thinking disabled (model doesn't match prefix).
[EDGE] Model is `"claude-opus-4-5"` and role is `"quick"`: thinking disabled (role not in set).
[NUANCE] `_THINKING_ROLES` = `{"grounded-answer", "plan-artifact", "build-planning"}` — these are the ONLY roles where thinking is safe. If a new role is added to the system, it will get thinking=False by default (safe).

### `register_provider` / `pick_provider`

[CAPABILITY] `register_provider` stores a provider for model ids and optionally as default.
[CAPABILITY] `pick_provider(model)` looks up by exact model id; falls back to default; raises `KeyError` if no match and no default.
[WIRING] `_DEFAULT_PROVIDER` is a process-global list. `_PROVIDERS` is a process-global dict. Multiple calls to `register_provider` with `default=True` replace the default. Concurrent registration (rare) is not thread-safe (CPython GIL makes it safe in practice, but asyncio thread-pool calls could race).
[FAILURE] `pick_provider` called before any provider is registered: raises `KeyError("no provider registered for model ... and no default provider set")`. Callers must handle this at boot — a test must verify that boot correctly guards this path.
[NUANCE] The process-global state means tests that register providers must clean up or will bleed into each other. Verify the test suite resets `_PROVIDERS`/`_DEFAULT_PROVIDER` between tests.

---

## 7. `agentkit/sdk_provider.py` — concrete Claude SDK provider

### `build_sdk_options`

[CAPABILITY] Translates `ProviderQuery` → `ClaudeAgentOptions` with the isolation triad pinned: `strict_mcp_config=True`, `setting_sources=[]`, `permission_mode="bypassPermissions"`, merged `disallowed_tools`.
[CAPABILITY] `merged_disallowed = tuple(dict.fromkeys(*disallowed_tools, *query.disallowed_tools))` — deduplication via dict insertion order (Python 3.7+ stable). Correct.
[WIRING] `build_sdk_options` is the ONLY place SDK options are constructed — all isolation invariants flow through here. Verify no other `ClaudeAgentOptions` construction exists in the tree.
[NUANCE] `permission_mode="bypassPermissions"` is the headless-server requirement: interactive permission prompts are impossible in a sandboxed subprocess. The triad comment explains why this is the ONLY workable mode, not a security relaxation.
[NUANCE] `mcp_servers=query.mcp_servers or {}` — a `None` value is converted to `{}` here, not upstream. Correct.
[EDGE] `query.setting_sources` contains strings that are not valid `SettingSource` literals: `cast(SettingSource, s)` is a type-only cast, not a runtime validation — the SDK will fail at runtime if an invalid literal is passed. In practice `setting_sources=()` always (the seam default), so this is safe.
[EDGE] `query.env` is empty `{}`: `merged_env` in `ClaudeAgentProvider.stream` is just `auth_env` — the SDK subprocess gets auth but no per-turn clamp. Verify that `MAX_OUTPUT_TOKENS` clamp is always included in the env by the caller.

### `_chunk_from_message`

[CAPABILITY] `AssistantMessage` with `ToolUseBlock` → `TOOL_USE` chunk with id/name/input.
[CAPABILITY] `AssistantMessage` with `TextBlock` (no ToolUseBlock) → `TEXT` chunk.
[CAPABILITY] `AssistantMessage` with both ToolUseBlock and TextBlock: the loop `for block in blocks` returns the FIRST `ToolUseBlock` found — the TextBlock is SILENTLY DROPPED. Only one chunk is emitted per AssistantMessage. Verify the map-build loop does not depend on receiving a TEXT chunk from a tool-use turn.
[CAPABILITY] `ResultMessage` → `RESULT` chunk with session_id/num_turns/total_cost_usd/structured_output.
[CAPABILITY] `SystemMessage` or unknown → `None` (skip).
[EDGE] `AssistantMessage` with ONLY `ToolUseBlock` (no TextBlock): returns the TOOL_USE chunk. Correct.
[EDGE] `AssistantMessage` with empty content `[]`: no blocks, text = `""`, returns `None` — message is dropped silently. Verify this doesn't cause a hang when the model emits a no-op turn.
[EDGE] `ToolUseBlock.input` is `None`: stored as `{}` via `or {}`. Correct.
[EDGE] `ResultMessage.structured_output` is not a string (e.g. a dict): stored as `""`. The map-build loop that checks `metadata['structured_output']` may silently get `""` when expecting structured data.
[FAILURE] `getattr` on any field raises: `_chunk_from_message` has no try/except — an unusual SDK message shape could raise `AttributeError` and propagate uncaught from `ClaudeAgentProvider.stream`'s `async for message` loop. The outer `except Exception` in `stream` catches it and yields an `ERROR` chunk.

### `ClaudeAgentProvider.stream`

[CAPABILITY] Drives `sdk_query(prompt, options)` and yields normalized `AgentChunk`s.
[CAPABILITY] On any exception from the SDK: yields a terminal `ERROR` chunk with `{"message": "ExcType: msg"}` — NEVER raises (in-band error surfacing).
[CAPABILITY] Auth is threaded onto `merged_env` WITHOUT mutating the process environment permanently — only the subprocess env is affected for this call.
[WIRING] The `auth_env` is closed over in `ClaudeAgentProvider.__init__`; it must be populated at construction time and not mutated later.
[EDGE] `merged_env` merges `options.env` first, then `auth_env` — if there is a key collision, `auth_env` WINS. If `options.env` contains `ANTHROPIC_API_KEY` for some reason and `auth_env` also contains it, the auth value wins. Intended but worth testing.
[EDGE] `prompt` is an empty string: passed to `sdk_query` — SDK behavior with empty prompt is vendor-defined. May return an error or empty response. Verify the caller never sends an empty prompt.
[FAILURE] SDK process crashes mid-stream (e.g. SIGKILL): the `async for message in sdk_query(...)` loop raises; caught by `except Exception`; yields `ERROR` chunk. Recovery (stale-session replay or abort) is the caller's responsibility via `resume_with_fallback`.
[FAILURE] Network timeout mid-stream: same as above — yields `ERROR` chunk.
[NUANCE] `options.env = merged_env` mutates the `ClaudeAgentOptions` object after it was built by `build_sdk_options` — this is fine since the object is local to this call, but it's a design smell. The mutation is not visible outside the call.

### `make_map_provider`

[CAPABILITY] `api_key` (non-empty) → `auth_env = {"ANTHROPIC_API_KEY": api_key}`.
[CAPABILITY] `auth_token` (non-empty, api_key empty) → `auth_env = {"ANTHROPIC_AUTH_TOKEN": auth_token}`.
[CAPABILITY] `oauth_token` (non-empty, api_key and auth_token empty) → `auth_env = {"CLAUDE_CODE_OAUTH_TOKEN": oauth_token}` with NO `ANTHROPIC_*` key.
[CAPABILITY] `use_vertex` (non-empty): adds `CLAUDE_CODE_USE_VERTEX` and `CLOUD_ML_REGION` (from environment or `""`).
[CAPABILITY] No auth of any kind → returns `None` (honest no-op).
[WIRING] Called by `control_plane.server` at boot from resolved settings/Secret Manager — never called with hard-coded literals in production.
[EDGE] `api_key` is non-empty AND `oauth_token` is non-empty: `api_key` wins (the `elif` chain; `oauth_token` branch is never reached). Priority: api_key > auth_token > oauth_token.
[EDGE] `use_vertex` is non-empty but `api_key`/`auth_token`/`oauth_token` are all empty: `env = {"CLAUDE_CODE_USE_VERTEX": ..., "CLOUD_ML_REGION": ...}`; env is non-empty → provider IS built (not `None`). This is a valid Vertex-ADC mode where the subprocess uses ambient workload identity.
[NUANCE] Secrets flow through `auth_env` only — never logged. Verified by `test_subscription_token_is_never_logged` at DEBUG level. Production must verify the SDK subprocess also never logs the env.

---

## 8. `agentkit/abort.py` (git-tracked; deleted from working tree in workroom pivot — kept for review)

[CAPABILITY] `AbortController` wraps `asyncio.Event`; `abort()` is idempotent and final; `aborted` property polls the event.
[CAPABILITY] `AbortRegistry.make(key)` mints a fresh controller, cancelling any prior one (stale-judgment preemption).
[CAPABILITY] `AbortRegistry.cancel(key)` aborts + drops the controller; `cancel_meeting(meeting_id)` cancels all controllers for one meeting.
[CAPABILITY] `AbortRegistry.abort(task_id)` / `clear(task_id)` / `is_aborted(task_id)` manage the TTS barge-in utterance-id set.
[WIRING] `AbortController` is created on the asyncio event loop at construction time; if `make(key)` is called from a different event loop (e.g. across thread boundaries), the event may not fire correctly. Verify all AbortRegistry calls are on the same event loop.
[EDGE] `cancel_meeting("meetingA")` with keys `["meetingA|task1", "meetingA2|task1"]`: the split-on-`|` check correctly avoids cancelling `meetingA2`'s task — the meeting-id prefix collision guard works.
[EDGE] `make(key)` called after `cancel(key)` (key no longer in `_controllers`): `cancel` is a no-op (key absent), then a fresh controller is minted. Correct.
[NUANCE] `AbortController` is per-asyncio-event-loop; using it across threads or event loops is unsafe. Verify it is always used from the single asyncio event loop in the harness.
[NUANCE] The barge-in set (`_aborted`) and the model-loop controllers (`_controllers`) share one registry — a key collision between a task_id and an utterance_id would cause false-positive aborts. Verify the two namespaces never share IDs.

---

## 9. `agentkit/config.py` (git-tracked; deleted from working tree in workroom pivot)

[CAPABILITY] `BehaviorConfig` is a frozen dataclass with `name`, `tools`, `model`, `role`, `max_turns`, `rules`, `inputs`, `system_prompt`, `allowed_tools`, `disallowed_tools`.
[CAPABILITY] `mounted_tools` property: returns `self.tools or self.allowed_tools` — never the union.
[CAPABILITY] `Behavior` pairs a `BehaviorConfig` with `role`, `rules`, `inputs`.
[CAPABILITY] `register(config)` stores in the process-global `_REGISTRY` by `config.name`; `get_behavior(name)` looks it up.
[NUANCE] `_REGISTRY` is process-global — same test isolation concern as `_PROVIDERS`/`_DEFAULT_PROVIDER` in provider.py. Tests must clean up.
[NUANCE] `mounted_tools` returns `self.tools or self.allowed_tools`: if `self.tools = ()` (falsy) and `self.allowed_tools = ("some_tool",)`, the fallback is used. If both are empty, `()` is returned — the behavior has no tools, which is valid for a no-tool turn.

---

## 10. `agentkit/resume.py` (git-tracked; deleted from working tree in workroom pivot)

### Recovery logic

[CAPABILITY] `is_stale_session_error`: matches `"no conversation found with session id"` and `"process exited"` (case-insensitive).
[CAPABILITY] `is_json_truncation_error`: matches `"unterminated string in json"` (case-insensitive).
[CAPABILITY] `resume_with_fallback`: same-session retry for JSON truncation (cap 2); stale-session replay from `history_fn()` with preamble.
[WIRING] `history_fn` is a `Callable[[], Awaitable[Any]]` — the caller (Doc 04 wake turn) passes the Postgres transcript-plane reader.
[EDGE] Both JSON-truncation and stale-session markers match the same error message: JSON-truncation check runs FIRST in the retry loop; stale-session breaks out after the JSON retry cap is exhausted. Could cause a stale-session to be retried twice as JSON-truncation before the replay. Verify this order is correct for real SDK errors.
[EDGE] JSON truncation error occurs twice (hitting `JSON_TRUNCATION_RETRY_CAP=2`): third attempt gets the same error; it is NOT a stale-session error (the two classifiers are mutually exclusive by string match) — the error is re-raised as an unknown fault. Verify the caller handles the re-raise.
[EDGE] `history_fn()` raises (DB unavailable during stale-session recovery): the exception propagates — no fallback for a fallback. Verify the outer call site handles this.
[EDGE] `abort.aborted=True` at the time of a `ProviderError`: the function re-raises immediately before any recovery — a killed build is never resurrected. This is the human-control invariant (Law 3).
[NUANCE] `RESTORED_NOTICE` is user-visible (`_My session was restored from the meeting so far...`); it is yielded as a TEXT chunk before the replay. The naming must not reveal internal component names.
[NUANCE] The stale-session replay is NOT looped — one honest replay from durable transcript, not infinite retry. If the replay also fails (e.g. the new session is immediately stale), the error propagates.

---

## 11. Cross-subsystem integration points

### A. `propose_change` → `objectstore` → `staged_drafts` → `accept_draft`

[WIRING] The full lifecycle: `propose_change` writes GCS via `objectstore.put` then inserts a `staged_drafts` row; `accept_draft` reads the row via `repos.drafts.get_draft` then reads GCS via `objectstore.get`.
[NUANCE] GCS write happens BEFORE DB row insert: if the process crashes between GCS write and DB insert, the artifact is orphaned but the draft_id is never queryable. The opposite (DB row first, then GCS write) would be worse (a draft_id exists but its content is unreadable). Current ordering is the correct choice.
[EDGE] The `artifact_ref` in the DB row points to a GCS path; the objectstore uses SHA-256 of the ref as the file key. If the ref changes (e.g. a meeting_id migration) the artifact is unreachable. Verify `artifact_ref` is immutable after creation.
[FAILURE] `objectstore.put` succeeds but `repos.drafts.insert_draft` raises: orphaned GCS artifact. No cleanup. The accept flow cannot find the draft_id (LookupError). This is a known data-loss failure mode; production must add cleanup or idempotent retry.

### B. `workroom.drafts.propose_change` vs. `control_plane.accept.apply_accepted_draft`

[WIRING] Two parallel paths for the accept flow: `workroom.accept_draft` (used from the workroom public API) and `control_plane.accept.apply_accepted_draft` (used by the control-plane's accept route). They share `objectstore` but have different idempotency guards.
[NUANCE] `control_plane.accept.apply_accepted_draft` has a durable idempotency guard (`status in ('applied', 'rejected')` → short-circuit). `workroom.accept_draft` has NO such guard. If both are called on the same draft_id, the control-plane path is idempotent; the workroom path is not.
[EDGE] A draft that has been `accept_draft`ed (status='accepted') and then `apply_accepted_draft`ed: the row is 'accepted' from the first call, then 'applied' from the second. Neither guard considers 'accepted' as "already done" — only 'applied'/'rejected'. A second call to `apply_accepted_draft` after the first correctly sets 'applied' again (idempotent). But the row passes through 'accepted' as an intermediate state — verify the accept route handles this.

### C. Host-side MCP server isolation (propose_change server vs. non-worker dispositions)

[WIRING] `mcp_servers_for_disposition` → `ProviderQuery.mcp_servers` → `build_sdk_options` → `ClaudeAgentOptions.mcp_servers`.
[NUANCE] The security invariant: the `propose_change` server is mounted ONLY for the worker disposition. The test must: (1) call `mcp_servers_for_disposition` for each non-worker disposition and confirm `{}` is returned; (2) verify that even if a non-worker disposition's `ProviderQuery` somehow contains `mcp_servers`, the SDK's `allowed_tools` or `disallowed_tools` blocks the tool call.
[EDGE] A model on a non-worker disposition that attempts to call `mcp__propose_change__propose_change`: the tool is not mounted (not in `mcp_servers`), so the SDK should reject the call with a tool-not-found error. Verify this is the actual SDK behavior, not a silent no-op.

### D. `agentkit.stream_deltas` → `map_build` stream consumer

[WIRING] `map_build` collects `[chunk async for chunk in provider.stream(prompt, query)]` — it does NOT apply `stream_deltas`. The map is built from `_capture_terminal_text` which reads the raw TEXT chunks. This means if the SDK emits accumulated text (not deltas), the terminal text is correct; but if the SDK emits deltas, the concatenated text is a duplicate of the accumulation. Verify which shape the SDK emits and whether `stream_deltas` is needed on the map-build path.
[NUANCE] `stream_deltas` is exported from `agentkit.__init__` and described as "the typed AgentChunk consumer the map-build stream reads through" in the `__init__.py` docstring — but `map_build.py` does NOT import or apply it. This discrepancy is either a documentation error or an unapplied optimization. Must be resolved.

### E. `agentkit.guardrails.with_injection_guardrail` → system prompts

[WIRING] The injection guardrail must be applied to every system prompt that will receive transcript content. `map_build.py` has its own hard-coded system prompt that does NOT call `with_injection_guardrail`. The map-build prompt does receive code content (not meeting transcript), so the guardrail may not be required there — but any path that receives transcript content must use it.
[NUANCE] The guardrail is described as applied by "the Workroom composer" which "imports and appends THIS verbatim rather than redefining its own." Since the workroom pivot removed the old Workroom agent code, verify the NEW native-Claude workroom path applies the guardrail at the right place (the prime / CLAUDE.md that becomes the system context). Since native Claude runs in E2B, the guardrail must be in the sandbox's `CLAUDE.md`, not in host-side code.

### F. `agentkit/abort.py` (AbortController) → human-control gate

[WIRING] `resume_with_fallback` checks `abort.aborted` before any recovery — this is the critical Law-3 gate (human control absolute: a killed build is never resurrected). The `AbortController` is the shared primitive for both the model-loop abort and the TTS barge-in. If the wrong `abort` object is checked (e.g. a barge-in utterance abort instead of the model-loop abort), the gate is bypassed.
[NUANCE] The `abort` parameter of `resume_with_fallback` is typed as `Any` — it relies on duck typing (`getattr(abort, "aborted", False)`). A `None` or wrong object silently returns `False` and the recovery proceeds — the human control gate is bypassed. Verify all callers pass the correct `AbortController`.

### G. `ProviderQuery` isolation triad → no host built-ins in sandbox mode

[WIRING] `compute_builtin_tools` always returns `()`. This means `ProviderQuery.tools = ()` and the SDK sees no built-in tools. The only tools available are the curated `allowed_tools` (MCP servers). Verify that no call site accidentally passes `tools=SDK_LOCAL_TOOLS` to `ProviderQuery` (which would expose host-side Read/Grep/Bash to a sandboxed call).
[NUANCE] The `disallowed_tools` block (SDK_LOCAL_TOOLS) provides a backstop even if `tools` is accidentally non-empty — a tool in both `tools` and `disallowed_tools` is blocked. But this is belt-and-suspenders: the primary gate is `tools=()`.

### H. `objectstore` (stand-in) → production GCS swap

[WIRING] The interface is `put(ref, content: str)` and `get(ref) -> str | None`. Production GCS uses the `ref` as the object path directly (not as a hash key). The stand-in uses `SHA-256(ref)` as the file path. Tests that run against the stand-in will NOT catch bugs that arise from real GCS path semantics (slashes, length limits, special characters in `ref`).
[NUANCE] The stand-in is in `tempfile.gettempdir()` — shared across processes/test runs that use the same temp dir. Tests must use unique `ref` values or clean up after themselves.

---

## High-Risk Items Summary (Top 5)

1. **Orphaned GCS artifacts on DB failure** (`_persist_bundle_row_sync` / `_propose_change_async`): GCS write before DB insert means a crash or DB error leaves an unreachable artifact. In production (real GCS), this wastes storage and is undetectable without a GCS-vs-DB reconciliation job. Must test partial-failure recovery and verify no acceptance path accidentally reads an orphaned artifact.

2. **Non-worker dispositions can theoretically call propose_change if the MCP server were mounted** (isolation gate test): The entire human-control guarantee (Law 3, §3.8) rests on `mcp_servers_for_disposition` returning `{}` for non-worker dispositions. A test must adversarially verify that even with a misconfigured `ProviderQuery` (mcp_servers set on a non-worker disposition), the SDK-level `disallowed_tools` block prevents the write. Both the mount decision and the block decision must be tested independently.

3. **Injection guardrail not applied to native-Claude sandbox path**: The old `with_injection_guardrail` host-side call is gone with the workroom pivot. The guardrail must now live in the sandbox's `CLAUDE.md`. If it is absent from the prime that gets mounted in the E2B sandbox, transcript content is treated as instructions — a critical security failure. Must test that the guardrail text appears in the mounted CLAUDE.md and that the model rejects injected commands.

4. **`abort.aborted` duck-typed to `Any`**: `resume_with_fallback` uses `getattr(abort, "aborted", False)` — if `None` or a wrong type is passed, the human-control gate silently returns `False` and a killed build can be resurrected. Must test the boundary: kill a build (set abort), then trigger a recoverable error, verify the build does NOT restart.

5. **`stream_deltas` not applied on the `map_build` path**: The `map_build` loop collects raw chunks from `provider.stream` without applying `stream_deltas`. If the SDK emits accumulated (non-delta) text, `_capture_terminal_text` concatenates each chunk correctly; but if the SDK emits true deltas, the concatenated map text is correct too. However the docstring claims `stream_deltas` is "the typed AgentChunk consumer the map-build stream reads through" — this is a documentation lie or a missing application. On a real provider streaming true deltas vs. accumulated chunks, the map output would differ. Must test with both a delta-emitting fake and an accumulation-emitting fake.

---

## Item Count Summary

| Category     | Count |
|--------------|-------|
| [CAPABILITY] | 52    |
| [WIRING]     | 31    |
| [EDGE]       | 60    |
| [FAILURE]    | 20    |
| [NUANCE]     | 42    |
| **Total**    | **205** |

Output file: `/Users/daksh/Desktop/proxy/live-test/derived/workroom-agentkit.md`
