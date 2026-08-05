# Derived test surface — `libs/http` + `libs/contracts`

Exhaustive, adversarial test-requirements extraction from every source line in the two
seams that guarantee Proxy's external-call discipline (`libs/http`) and its wire-message
integrity (`libs/contracts`). Every item is one `[CATEGORY] <testable statement>`.

Categories: CAPABILITY (behavior it provides) · WIRING (integration/contract boundary) ·
EDGE (boundary/concurrency) · FAILURE (vendor error/timeout/malformed/cancel/race + what
SHOULD happen) · NUANCE (subtle expectation — retry semantics, cost accuracy, cancel-safety,
registry closure, contract versioning).

Read alongside `CLAUDE.md` hard rules "External calls" (single `call_external` seam) and
"Contracts" (`assert_registry_closed` closure), and the five laws (esp. Law 4
dynamic-never-hardcoded, which the transport-cancel resilience explicitly cites).

---

## `libs/http/external.py` — the single external-call seam (`call_external` + raw-client factories)

### `call_external` — bounded retry + cost telemetry wrapper
- [CAPABILITY] A successful `op()` on the first attempt returns `ExternalCallOutcome(value=<result>, attempts=1, total_cost_usd=unit_cost_usd*1)`.
- [CAPABILITY] The returned `value` is exactly the object `op()` produced, untouched (no wrapping/coercion of the payload).
- [CAPABILITY] `ExternalCallOutcome` is a frozen dataclass — mutation of `.value/.attempts/.total_cost_usd` after return raises `FrozenInstanceError`.
- [CAPABILITY] `max_retries` defaults to 3 (`_MAX_RETRIES`); an explicit `max_retries` argument overrides the bound per call.
- [EDGE] A transient `httpx.HTTPError` on attempts 1..(max-1) then success on the final attempt returns `attempts=<n>` reflecting the true count, not the max.
- [EDGE] Backoff is linear-in-attempt: `asyncio.sleep(_BASE_BACKOFF_S * attempt)` = 0.2s, 0.4s, 0.6s… — assert the sleep sequence (mock `asyncio.sleep`) matches `0.2*attempt` per retry, NOT exponential despite the docstring saying "exponential" (docstring/impl drift is itself a finding to flag).
- [EDGE] With `max_retries=1`, a first-attempt transient failure exhausts the loop immediately and re-raises the captured exception (no retry).
- [EDGE] `max_retries=0` never enters the loop; assert behavior (currently `last_exc is None` → the `assert last_exc is not None` fires an `AssertionError`, or the function returns nothing) — pin the actual contract for a zero/negative budget.
- [FAILURE] `httpx.HTTPError` (and every subclass: `ConnectError`, `ReadTimeout`, `PoolTimeout`, `RemoteProtocolError`, `HTTPStatusError`) is treated as transient and retried with backoff.
- [FAILURE] A bare `TimeoutError` (builtin / `asyncio.TimeoutError` alias in 3.12) is retried identically to `httpx.HTTPError`.
- [FAILURE] After `max_retries` transient failures, the LAST captured exception is re-raised (not a generic wrapper, not swallowed) — the caller sees the real vendor error.
- [FAILURE] A non-transient exception from `op()` (e.g. `ValueError`, `KeyError`, `RuntimeError`, an `anthropic.APIStatusError` if not an httpx subclass) is NOT caught — it propagates immediately on the first attempt with no retry and no cost recorded.
- [NUANCE] Cost telemetry (`_record_cost`) is invoked ONLY on success, AFTER the winning attempt — a call that exhausts all retries records NO cost (verify `total_cost_usd` is never charged for a fully-failed call).
- [NUANCE] `total_cost_usd = unit_cost_usd * attempts` — a call that succeeds on attempt 3 is charged 3× `unit_cost_usd` (every attempt is metered, including the failed ones). Assert this multiplier is exact for retries=2,3.
- [NUANCE] `unit_cost_usd` defaults to 0.0 — a call site that forgets to pass it records zero cost; assert callers that must meter (model, TTS, E2B) pass a real `unit_cost_usd` (cross-check call sites).
- [NUANCE] `_record_cost` currently only computes and returns; the docstring claims "in production this emits to the ops cost ledger" — assert whether a real ledger emission is wired (it is NOT in this file) — a gap to flag for live cost accounting.

### Transport-cancel resilience (the load-bearing Law-4 case)
- [CAPABILITY] An `asyncio.CancelledError` raised by `op()` when `current_task().cancelling() == 0` is treated as a transient transport blip and RETRIED with backoff (does not propagate).
- [CAPABILITY] An `asyncio.CancelledError` raised by `op()` when `current_task().cancelling() > 0` (a genuine caller `task.cancel()`) is RE-RAISED immediately — no retry, no backoff, no swallow (prompt shutdown).
- [FAILURE] A meeting-end drain that calls `task.cancel()` on a task blocked inside `call_external` cancels PROMPTLY — the cancel is honored within one iteration, not after `max_retries` backoffs.
- [FAILURE] A simulated HTTP/2 stream-reset / GOAWAY surfaced as a bare `CancelledError` (cancelling()==0) does NOT crash the wake/poll loop — it retries and can still succeed (the WS6 long-session regression).
- [EDGE] `current_task()` returns `None` (called outside a running loop / from a bare thread) — the `task is not None` guard means a `None` task falls through to the RETRY branch; assert this is the intended default (a cancel with no task-context is retried, not re-raised).
- [EDGE] A `CancelledError` on the FINAL attempt with cancelling()==0 exhausts the loop and re-raises the `CancelledError` as `last_exc` — assert the exhausted-transient-cancel still surfaces as `CancelledError`, not a different type.
- [NUANCE] `task.cancelling()` count is a 3.11+ API — assert the guard reads the LIVE count at the moment of the exception (re-evaluated each attempt), so a cancel that arrives mid-retry-sequence is honored on the next raise.
- [NUANCE] The retry-on-cancel must be cancellation-SAFE at the seam: verify no state is left half-written across a retried `CancelledError` (the wrapper holds no external side-effect state itself — but assert `op()` idempotency is a documented caller obligation).
- [EDGE] Backoff `asyncio.sleep` itself can be cancelled — a genuine cancel arriving DURING the backoff sleep must propagate (the sleep raises `CancelledError`, cancelling()>0 by then) — assert cancel during backoff is not swallowed.

### Raw-client factory functions (the sole legitimate construction home)
- [CAPABILITY] `anthropic_client(**kwargs)` returns an `AsyncAnthropic` instance, importing the SDK LAZILY (not at module import) — importing `http.external` must NOT import `anthropic`.
- [CAPABILITY] `http_client(**kwargs)` returns an `httpx.AsyncClient` (the only raw httpx construction in the product).
- [CAPABILITY] `gcs_bucket(bucket_name)` returns a `google.cloud.storage` bucket handle, importing `google.cloud.storage` LAZILY (boot stays offline; no client until a real bucket is asked for).
- [CAPABILITY] `e2b_sandbox_class()` returns the raw `AsyncSandbox` class, importing `e2b` LAZILY.
- [WIRING] `e2b_sandbox_class()` wire surface: the returned class supports `.create(template=, timeout=<seconds>, envs=<dict>, metadata=)`, instance `.kill()`/`.set_timeout(seconds)`/`.is_running()`, and classmethods `.connect(sandbox_id)`/`.list()` — assert callers use exactly these (CANONICAL §11.10).
- [FAILURE] `e2b_sandbox_class()` raises `ImportError` (honest degrade) when the `e2b` package is absent — the caller decides fatal (live deploy) vs no-op (fake-backed test); assert the ImportError is not swallowed here.
- [FAILURE] `gcs_bucket()` / `anthropic_client()` raise `ImportError` when their SDK is absent — assert the lazy import failure is the caller's to handle, never masked.
- [NUANCE] The `check_call_external` guard (`libs/ops`) is the enforcer that NO raw client (`AsyncAnthropic`/`Anthropic`/`httpx.AsyncClient`/`httpx.Client`/`storage.Client`/`Deepgram`/`ElevenLabs`/`Cartesia`/`AsyncSandbox.create|connect`) is constructed OUTSIDE `libs/http` — this seam is the ONLY home; assert the guard fails when a raw client is planted elsewhere (aliased, lazy-in-function, and attribute-form all caught).
- [NUANCE] `TYPE_CHECKING`-only import of `AsyncAnthropic` at top of file must never trigger a runtime import (assert `anthropic` absent at import time still lets `http.external` import cleanly).
- [WIRING] `call_external` is generic over `op`'s return type `T` — the outcome `.value` type is whatever `op` returns; assert type-preservation for a model response, an httpx `Response`, a GCS blob, an E2B sandbox.

---

## `libs/http/dispatch.py` — the ONE inbound dispatch funnel (six ordered steps, isolation by construction)

### `dispatch()` — the six-step funnel
- [CAPABILITY] A rate-ok, registered-type, valid, tenant-isolated message with all entity ids owned by the connection's tenant routes to EXACTLY the injected `ctx.handler` once.
- [CAPABILITY] ANY failure in steps 1–5 sends exactly one generic error via `conn.send_error` and routes NOTHING (handler never called).
- [WIRING] The funnel consumes `CHANNEL_REGISTRY` from `contracts.registry` — a type registered there is routable; a type absent there is `"Not found"`. This is the live coupling between the two libs.
- [WIRING] `DispatchCtx` injects `rate_limiter`, `store`, `handler` — `libs/http` carries NO hard dep on `db`; assert the live control_plane mount binds a repos-backed `Store` and the tests bind a fake `Store` through the same Protocol.
- [WIRING] The `Connection` Protocol requires `id`, `tenant_id`, `async send_error(str)` — assert the gateway's `Connection` and any live socket satisfy it structurally.

#### Step 0 — the `is_owner` fence (§12.10)
- [CAPABILITY] `is_owner=False` returns immediately, routing nothing AND sending no error (a reclaimed process must emit no side-effecting message and must not even leak an error frame).
- [FAILURE] A reclaimed (non-owner) process that receives a valid, owned, side-effecting `channel_action` performs NO side effect — assert silent refusal, not a generic error, not a route.
- [NUANCE] The fence runs BEFORE rate-limiting — assert a non-owner is not even counted against the rate limiter (order matters).

#### Step 1 — per-connection rate limit
- [CAPABILITY] Within the window, `check(conn.id)` returns True and the funnel proceeds; over the window it returns False → `"Slow down."` and routes nothing.
- [WIRING] The limiter is keyed on `conn.id` (per-connection), NOT `tenant_id` or `user_id` — assert two connections of the same tenant have independent budgets.
- [EDGE] The moving-window boundary: at exactly the Nth hit in `"60/minute"`, the 61st within the window is refused; a hit after the window rolls off is admitted again.
- [NUANCE] The limiter is the pinned `limits` `MovingWindowRateLimiter` over `MemoryStorage` — assert it is NOT a hand-rolled token bucket (CANONICAL §11.11); a Redis swap must sit behind the same `check()` call.
- [FAILURE] `"Slow down."` is the ONLY message on rate-limit — never "rate limit exceeded for tenant X" (no info leak).
- [EDGE] `MemoryStorage` is process-local — assert the rate limit does NOT survive a process restart and is NOT shared across replicas (a documented V0 limitation to test/flag for multi-instance deploy).

#### Step 2 — registry lookup by declared type
- [CAPABILITY] `raw["type"]` present and in `CHANNEL_REGISTRY` selects that model; absent or unregistered → `"Not found"`.
- [FAILURE] An unregistered/unknown type yields the GENERIC `"Not found"` — never "unknown type X" (no attacker-type echo, no info leak).
- [EDGE] `raw` with no `"type"` key at all → `declared_type is None` → `"Not found"` (no crash on missing discriminator).
- [EDGE] `raw["type"]` is a non-string (int, dict, list, None) → `str(declared_type)` lookup misses → `"Not found"` (no crash).
- [FAILURE] `raw["type"]` set to an OUTBOUND type (e.g. `"voice.speak"`) — assert it is `"Not found"` (outbound frames are not inbound-routable even though they are registered; note: they ARE in CHANNEL_REGISTRY, so verify the funnel does not route a spoofed outbound type to a handler that mis-handles it — a real adversarial case).

#### Step 3 — central Pydantic validation (ONCE)
- [CAPABILITY] `model.model_validate(raw)` produces the typed message; success proceeds to isolation.
- [FAILURE] A malformed body (wrong field types, `extra="forbid"` violation, missing required field) → `"Not found"` (validation is one generic refusal; never echoes the pydantic error to the wire).
- [FAILURE] A non-UUID `meeting_id` is rejected HERE, BEFORE any store lookup — this is what makes step 4 sound (the store is never queried on attacker-shaped input).
- [FAILURE] An over-length `arg` (>2000 chars) is a `ValidationError` → `"Not found"` (the field-cap DoS bound is enforced at validation).
- [NUANCE] Validation catches `Exception` broadly (`except Exception`) — assert even a non-`ValidationError` raised inside `model_validate` collapses to `"Not found"`, never leaks.
- [EDGE] Extra unknown fields in `raw` (the model is `extra="forbid"`) → `"Not found"`.

#### Step 4 — meeting/tenant isolation keyed on `meeting_id` presence
- [CAPABILITY] A message with a `meeting_id` whose owning tenant (from OUR store) equals `conn.tenant_id` passes; otherwise `"Not found"`.
- [FAILURE] `meeting_id` owned by a DIFFERENT tenant → `"Not found"` — IDENTICAL error to a `meeting_id` that does not exist at all (absent-vs-foreign are indistinguishable; the error is not a tenancy oracle). [P0]
- [FAILURE] A `meeting_id` that resolves to `None` (no owning tenant / absent) → `"Not found"`.
- [CAPABILITY] A message with NO `meeting_id` and `requires_meeting_scope` truthy (the default) hits the default-reject floor → `"Not found"`.
- [NUANCE] `getattr(msg, "requires_meeting_scope", True)` defaults True — no V0 message opts out; assert the floor stays a safety net (a future global message must explicitly set it False to bypass).
- [WIRING] `store.meeting_tenant(meeting_id: UUID)` is the ownership oracle — assert the live store reads OUR durable substrate (meeting→tenant), never the client `meeting_id` to self-authorize.
- [NUANCE] `_meeting_owned_by_conn` compares `str(owner_tenant) == str(conn.tenant_id)` — assert string coercion handles UUID vs str tenant ids consistently (no type-mismatch false-negative that would deny a legitimate owner).

#### Step 5 — entity → owner → tenant (the smuggle fix)
- [CAPABILITY] A `canvas_id`/`artifact_id` on the message is resolved to its OWN owning meeting via `store.entity_owning_meeting`, and THAT meeting's tenant is checked — never the client's `meeting_id`.
- [FAILURE] A smuggled `canvas_id` owned by another meeting (even with a valid own `meeting_id`) → `"Not found"` — the entity's true owner is checked, killing the smuggle bug. [P0]
- [FAILURE] `entity_owning_meeting(entity_id)` returns `None` (entity absent) → `"Not found"`.
- [EDGE] `getattr(msg, "canvas_id", None) or getattr(msg, "artifact_id", None)` — assert precedence: `canvas_id` wins when both present; a falsy `canvas_id` (None) falls back to `artifact_id`.
- [NUANCE] The entity check runs even AFTER the `meeting_id` check passed — assert a message with a valid own `meeting_id` but a foreign `canvas_id` is STILL refused (both gates independent).

#### Step 6 — route
- [CAPABILITY] `ctx.handler(conn, msg, ctx)` is awaited exactly once with the validated+isolated message.
- [FAILURE] If `ctx.handler` raises, the funnel does NOT catch it (the never-throw obligation lives in the handler, not the funnel) — assert the handler's own boundary (`handle_channel_action`) guarantees no raise; flag if the funnel is expected to shield a throwing handler.

#### Concurrency / ordering
- [EDGE] Two concurrent dispatches on the same `conn` share the rate-limiter state — assert the moving window counts both, no lost-update race in `strategy.hit`.
- [EDGE] Concurrent dispatches with interleaved `await` on `store.*` must not cross-contaminate isolation verdicts (each call resolves its own `meeting_id`/tenant).

### `resolve_entity_tenant()` — the AC-TEN-002 server-side resolution seam
- [CAPABILITY] For `entity_type="meeting"`, resolves the entity's owning tenant directly via `store.meeting_tenant`.
- [CAPABILITY] For `entity_type="canvas"`/`"artifact"` (anything not `"meeting"`), resolves owning meeting → tenant via two store hops.
- [CAPABILITY] Returns `{"allowed": True, "tenant_id": <owner>}` ONLY when the resolved owner equals the principal's tenant.
- [FAILURE] A non-UUID / un-parseable `entity_id` → `{"allowed": False, "tenant_id": None}` (never raises).
- [FAILURE] A cross-tenant read (owner ≠ principal) → `{"allowed": False, "tenant_id": None}` — never resolves into the foreign tenant's scope, hands back NO foreign tenant id. [P0]
- [FAILURE] `principal["tenant_id"]` is None → DENY (a principal with no tenant can authorize nothing).
- [FAILURE] `owner_tenant is None` (entity/meeting absent) → DENY.
- [EDGE] `entity_id` already a `UUID` instance is used directly; a str is coerced via `UUID(str(...))`; assert both paths resolve identically.
- [NUANCE] The DENY branch returns `tenant_id: None` even when a foreign tenant WAS resolved — assert the foreign id is never leaked back in the allowed=False case.

### `PerConnectionRateLimiter` / `DispatchCtx.build`
- [CAPABILITY] `PerConnectionRateLimiter.check(key)` records one hit and returns bool(within-window).
- [CAPABILITY] `DispatchCtx.build(store=, handler=, rate_limit="60/minute")` constructs the pinned `MovingWindowRateLimiter(MemoryStorage())` + `parse(rate_limit)`.
- [EDGE] `rate_limit` strings other than the default (`"2/second"`, `"100/hour"`) parse and enforce correctly via `limits.parse`.
- [FAILURE] A malformed `rate_limit` string passed to `parse()` raises at `build()` time (fail-fast at construction, not at first dispatch).
- [NUANCE] `PerConnectionRateLimiter` is a frozen dataclass — the `strategy`/`item` cannot be swapped after build.

---

## `libs/http/gateway.py` — WS upgrade auth (401 before the 101)

- [CAPABILITY] `authorize_upgrade` returns an authenticated `Connection(user_id, tenant_id)` when session resolves, origin allowed, and under the per-user cap.
- [CAPABILITY] The returned `Connection.tenant_id` is the SERVER-resolved session tenant — never a client field; this is the isolation lineage the funnel relies on.
- [FAILURE] No/invalid session (`resolve_session` returns None) → `RejectUpgrade(401)` raised BEFORE any socket accept (the socket never opens on an unauthenticated upgrade). [P0]
- [FAILURE] Disallowed origin (allowlist configured, origin not in it) → `RejectUpgrade(403)`.
- [FAILURE] At/over `MAX_CONN_PER_USER` (8) via `conn_limiter.count(user_id)` → `RejectUpgrade(429)`.
- [CAPABILITY] `_origin_allowed(origin, None)` (no allowlist configured, dev) → always True; a configured allowlist requires exact membership.
- [EDGE] `origin` header absent (`None`) with a configured allowlist → not in allowlist → `403`.
- [EDGE] `conn_limiter is None` disables the cap entirely — assert unbounded connections allowed when no limiter injected (documented dev behavior).
- [EDGE] Ordering: session (401) is checked BEFORE origin (403) BEFORE cap (429) — assert a request failing multiple checks returns the FIRST failure's status (fail-closed order).
- [WIRING] `resolve_session` is injected (`control_plane.session.resolve_session` over `app.state.db` live; a fake in tests) — `libs/http` never imports the sessions table plumbing.
- [WIRING] `authorize_upgrade` reads `request.cookies` and `request.headers` defensively (`getattr(..., {}) or {}`) — assert a request object missing either attribute does not crash (falls to empty → 401 via no session).
- [NUANCE] `RejectUpgrade.status` carries the wire status; assert the live mount maps it to the actual HTTP response BEFORE the 101 handshake (never a half-open socket).
- [NUANCE] `Connection.id` defaults to a fresh `uuid4().hex` per connection — assert two connections get distinct rate-limit keys.
- [EDGE] `session["user_id"]` / `session["tenant_id"]` missing keys (malformed session dict) → `KeyError` — assert whether this is intended to propagate (fail-closed) or should be a 401 (a resolver contract to pin).
- [CAPABILITY] `Connection.send_error` base is a no-op sink (usable handler-free); the live socket overrides it — assert the funnel only ever passes the two generic strings.

---

## `libs/http/registry.py` (HTTP) — the connect-API typed auth surface + route classification

### `protected()` / `AuthzCtx`
- [CAPABILITY] `protected(resolve_session)` yields an `AuthzCtx(user_id, tenant_id)` with a NON-NULL `tenant_id` by construction (safe as a DB filter by type).
- [FAILURE] Anonymous caller (`resolve_session` → None) → `HTTPException(401)`.
- [FAILURE] A session with no tenant (`tenant_id is None`) → `HTTPException(403)` BEFORE `AuthzCtx` is constructed (so a null tenant can never reach a query).
- [WIRING] The handler declaring `ctx = protected(...)` receives ONLY the `AuthzCtx` — never the raw `Request`; "read tenant from body" is unrepresentable in the signature.
- [NUANCE] The `_dep` is stamped with `PROTECTED_DEP_MARKER` so `declares_protected_dep` recognizes it structurally (not by name) — assert the marker survives `Depends()` wrapping and is walkable in the dependant tree.

### `public()` / `PublicAuthzCtx`
- [CAPABILITY] `public()` yields `PublicAuthzCtx(user_id=None, tenant_id=None)` — nullable tenant BY DESIGN so it cannot be dropped into a query filter by accident.
- [NUANCE] A `None` `tenant_id` used as a DB filter would widen a query to EVERY tenant (a cross-tenant read) — assert the type-level nullability forces an explicit non-null check first (a compile/type-check obligation, testable via mypy `--strict`).
- [WIRING] A route using `public()` MUST be in `PUBLIC_ROUTES` — the enumeration test enforces this.

### `PUBLIC_ROUTES` allowlist + route classification
- [NUANCE] `PUBLIC_ROUTES` is the ONLY unauthenticated-reachable set — assert EVERY app route classifies as `protected`|`public`|`internal`|`ws`|`framework`, never `raw` (the route-enumeration test is the guard).
- [FAILURE] A route registering an HTTP surface with no wrapper and not on the allowlist classifies as `raw` — the failure class; assert `classify_route` returns `"raw"` for it. [P0]
- [CAPABILITY] Each allowlisted route earns its exemption by a scoped grant (HMAC / capability token / no-meeting-yet poll) — assert the webhook routes are HMAC-gated, `/m/{meeting_id}` requires a capability token, `/health` carries no tenant data.
- [WIRING] `POST /webhooks/recall` and `POST /webhooks/github` are public ONLY because HMAC-gated — cross-check with `webhook.py` verifiers that the gate actually runs before any durable write.
- [CAPABILITY] `route_key(route)` yields `"METHOD /path"` for HTTP routes, `None` for WS/method-less routes; HEAD is stripped (framework twin of GET).
- [EDGE] A route with multiple verbs → `route_key` uses `sorted(...)[0]` — assert the deterministic key for a multi-method route matches the allowlist entry.
- [CAPABILITY] `declares_protected_dep` walks the FULL dependant graph (BFS with a `seen` set) — a `protected()` nested deep in a sub-dependency is still found.
- [EDGE] `declares_protected_dep` on a route with a cyclic/self-referential dependant tree terminates (the `seen: set[int]` by `id()` prevents infinite loop).
- [CAPABILITY] `is_internal_scoped` recognizes `INTERNAL_SCOPED_MARKER` stamped via `mark_internal_scoped` — `/internal/*` routes are scoped by `X-Internal-Token`, NOT the session cookie.
- [CAPABILITY] `is_websocket_route` recognizes `WebSocketRoute`/`APIWebSocketRoute` by type name (WS auth is at UPGRADE, not per-message).
- [CAPABILITY] `is_framework_route` recognizes `/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc` — exposes no tenant data.
- [NUANCE] `classify_route` precedence: framework → ws → protected → internal → public → raw. Assert the order: a WS route that ALSO has a protected dep classifies as `ws` (auth-at-upgrade wins) — a subtle mis-classification risk.
- [EDGE] A route in `PUBLIC_ROUTES` that ALSO declares `protected()` classifies as `protected` (dual-mode `/m/{meeting_id}` served to a signed-in member) — assert the dual-mode route is not accidentally down-graded to public.

---

## `libs/http/webhook.py` — Recall + GitHub HMAC signature verifiers (public routes earn their exemption)

### `verify_recall_signature` (Svix HMAC-SHA256, base64)
- [CAPABILITY] Recomputes `HMAC-SHA256(key, f"{id}.{timestamp}.{raw_body}")` base64-encoded and returns True on the first matching `v1,<sig>` entry.
- [CAPABILITY] Signature verified over the RAW request BODY BYTES exactly as received — a re-serialised dict must FAIL (JSON round-trip reorders/rewrites bytes). [P0]
- [CAPABILITY] Constant-time compare via `hmac.compare_digest`, OR-accumulated over ALL candidates so loop timing does not leak which entry matched (no short-circuit branch on first match).
- [CAPABILITY] Multiple space-delimited `v1,<sig>` entries (secret rotation) — the delivery is valid iff ANY entry matches.
- [CAPABILITY] Header names accepted in either spelling: `webhook-id`/`svix-id`, `webhook-timestamp`/`svix-timestamp`, `webhook-signature`/`svix-signature`, case-insensitively (`_lower_headers`).
- [FAILURE] Empty/unset `secret` → `WebhookVerificationError` (status 401) — fail-closed, NEVER accept an unverifiable delivery. [P0]
- [FAILURE] Missing id, timestamp, OR signature header → `WebhookVerificationError("missing signature headers")` (401).
- [FAILURE] Signature header present but no `v1` entry (only non-v1 versions, or entries with no comma) → `WebhookVerificationError("no v1 signature present")` (401).
- [FAILURE] All candidates mismatch → `WebhookVerificationError("bad signature")` (401).
- [FAILURE] A tampered body (one byte flipped) → signature mismatch → 401 (the id+timestamp are in the signed content, so a signature cannot be replayed under a different id).
- [FAILURE] `_secret_key` on a `whsec_<malformed-base64>` → `WebhookVerificationError("malformed signing secret")` — never crashes the request path with a `binascii.Error`.
- [CAPABILITY] `_secret_key` on a secret WITHOUT the `whsec_` prefix treats it as raw UTF-8 key bytes (defensive fallback for a non-Svix secret).
- [EDGE] `_candidate_signatures` ignores entries without a comma (malformed, not a match) and keeps only `version == "v1"`.
- [EDGE] `raw_body` as `bytearray` vs `bytes` — both coerced via `bytes(raw_body)`; assert identical verdict.
- [NUANCE] The verifier is WIRED AHEAD of the durable `webhook_events` insert so a forged delivery can never dedupe-poison the table — assert the live route calls verify BEFORE any DB write. [P0]
- [FAILURE] Empty `raw_body` (b"") with a valid signature over the empty body verifies; a signature computed over a non-empty body fails — assert the empty-body case is handled, not a false-accept.

### `verify_github_signature` (GitHub `X-Hub-Signature-256`, hex, raw-UTF-8 key)
- [CAPABILITY] Recomputes `HMAC-SHA256(secret.utf-8, raw_body)` as a lowercase-hex digest and constant-time-compares against `sha256=<hex>`.
- [CAPABILITY] Key is raw UTF-8 secret bytes — NOT base64-decoded (distinct from the Svix `whsec_` path) — assert the two verifiers are not conflated.
- [FAILURE] Empty/unset secret → `WebhookVerificationError` (401) fail-closed.
- [FAILURE] Missing `x-hub-signature-256` header → `WebhookVerificationError("missing signature header")`.
- [FAILURE] Header not starting with `sha256=` → `WebhookVerificationError("malformed signature header")`.
- [FAILURE] Digest mismatch → `WebhookVerificationError("bad signature")` (401).
- [CAPABILITY] Verified over the exact raw body bytes (a re-serialised body fails); header lookup is case-insensitive.
- [NUANCE] This gates the GitHub push freshness ingress (§3.6) — a forged push must never trigger a repo-map rebuild; assert verify runs before the rebuild is enqueued. [P0]

### `WebhookVerificationError`
- [CAPABILITY] Carries `status_code = 401` and a `.detail` — assert `safe_error_handler` collapses the detail to a fixed `"Unauthorized"` body (no internal detail to the caller).

---

## `libs/http/safe_error.py` — external callers never see an internal error string

- [CAPABILITY] `RequestValidationError` → 422 with `{"error": "invalid request", "issues": [...]}` — the caller's OWN bad input is safe to echo (tells them how to fix it).
- [CAPABILITY] Any other exception → the per-status `_FALLBACK` body (400/401/403/404/409/422/429/500/503) with NO detail from the exception.
- [FAILURE] A bare `Exception` (no `status_code`) → 500 `"Service temporarily unavailable"` — never leaks `str(exc)` (stack detail, DB error, table name). [P0]
- [FAILURE] An exception with a NON-int `status_code` (e.g. a str) → coerced to 500 (`if not isinstance(status, int)`).
- [FAILURE] An exception with a status NOT in `_FALLBACK` (e.g. 418, 502) → generic `"Request failed"` (an unknown status must not leak an internal string either).
- [CAPABILITY] `_jsonable` coerces non-JSON-scalar values in pydantic `.errors()` (a `ctx` exception object, `bytes` input) to `str` so the issues body always serialises — while still describing only the caller's bad input.
- [EDGE] `_jsonable` on nested dict/list/tuple recurses; on a scalar/None returns as-is; on anything else → `str(...)`.
- [WIRING] `install_safe_error_handler(app)` registers the handler for `RequestValidationError`, `HTTPException`, Starlette `HTTPException`, AND bare `Exception` — assert all four bindings are installed so the fallback (not Starlette's default `{"detail": ...}`) is what a caller sees.
- [NUANCE] `HTTPException` fallback body must be the fixed `_FALLBACK[status]`, NOT Starlette's default `{"detail": ...}` — assert the explicit registration overrides the framework default.
- [FAILURE] A validation error containing a `bytes` input value serialises (does not 500 the error handler itself) — the handler must never itself raise while collapsing an error.

---

## `libs/http/handlers/channel_action.py` — the ONE inbound handler (never-throw boundary)

- [CAPABILITY] A structurally-sound frame (has `surface` AND `action`) is a no-op SUCCESS (no live fulfilling service bound yet; the funnel provably ran end-to-end).
- [FAILURE] A frame missing `surface` or `action` (only reachable via a funnel-bypass hand-built object) → generic `"Not found"` (no shape/type leak).
- [FAILURE] ANY exception inside the handler is caught (`except Exception`) and turned into a `"Not found"` refusal — the handler RETURNS, never raises (§14 never-throw tool boundary). [P0]
- [NUANCE] The handler must never crash the funnel — assert a `conn` whose `send_error` itself raises is still contained (or flag if a raising `send_error` escapes).
- [WIRING] This is the seam where a live fulfilling service binds; assert that when a real service IS bound (via `register_handler(..., replace=True)`), the never-throw contract still holds around it.

---

## `libs/contracts/registry.py` — the message-type registry + full-graph closure

### `ProxyMessage` base + auto-registration
- [CAPABILITY] Every `ProxyMessage` subclass with a `type` field default auto-registers into `CHANNEL_REGISTRY` at import via `__pydantic_init_subclass__`.
- [CAPABILITY] `model_config = ConfigDict(extra="forbid")` — an extra field on any wire message is a `ValidationError` (no silent extra-field passthrough).
- [FAILURE] Two subclasses declaring the SAME `type` value → `ValueError("duplicate ProxyMessage type registered")` at import (exactly-one-model-per-type). [P0]
- [EDGE] A subclass with NO `type` field, or a `type` field with `default is None`, is NOT registered (the guard returns early) — assert such a class does not pollute the registry.
- [NUANCE] The registry key is the enum `.value` when the default is an Enum, else `str(default)` — assert a `Literal["channel_action"]` default registers under `"channel_action"`.

### `assert_registry_closed()` — the four-part closure (boot fail-fast + CI)
- [CAPABILITY] (1) Set-equality: `MessageType` enum values == `CHANNEL_REGISTRY` keys — a model without an enum entry OR an enum entry without a model raises `AssertionError` naming both diffs. [P0]
- [CAPABILITY] (2) Every INBOUND type has EXACTLY ONE handler in `MESSAGE_HANDLERS` — an unhandled inbound raises.
- [CAPABILITY] (2b) An inbound whose handler slot holds a list/tuple/set (multi-handler) raises `inbound-not-exactly-one-handler`.
- [CAPABILITY] (3) Every OUTBOUND type has AT LEAST ONE projector in `MESSAGE_PROJECTORS` — an unprojected outbound raises.
- [CAPABILITY] (4) No `SIGNAL_SURFACE_EVENTS` name leaked into `CHANNEL_REGISTRY` — a signal-surface event registered as a client message raises. [P0]
- [FAILURE] A produced-but-unhandled inbound, an unprojected outbound frame, or a leaked signal-surface event FAILS THE BUILD (AssertionError) — assert each of the four violations fires distinctly and names itself.
- [NUANCE] The exception type is ALWAYS `AssertionError` (the boot path + `field-contract` guard + every `pytest.raises(AssertionError)` depend on it) — assert no violation ever raises a different type.
- [EDGE] `assert_registry_closed(message_type=<injected union/Literal>)` runs the set-equality probe ONLY (skips the coverage checks) — the orphan-rejection test semantics; assert the arg-less shipped call checks ALL four.
- [WIRING] Called at BOOT (fail-fast) and in CI via `libs/ops/check_field_contract.py` — assert both entry points invoke the arg-less form.
- [NUANCE] `SIGNAL_SURFACE_EVENTS` (transcript/roster/speaking/boundary/barge-in/bot-status/meeting-end/channel-report) are in-process/transport events — assert none is ever a `ProxyMessage`; a leak is a closure failure. [P0]

### `register_handler` / `register_projector` / `register_producer`
- [CAPABILITY] `register_handler(inbound_type, handler)` binds the single handler; re-binding the SAME callable is idempotent.
- [FAILURE] Binding a DIFFERENT handler to an already-handled inbound WITHOUT `replace=True` → `ValueError` (exactly-one-handler invariant).
- [CAPABILITY] `register_handler(..., replace=True)` is the sanctioned swap the live host uses to replace the default stub with the real capability-gated handler — the map is OVERWRITTEN (never appended), so the type still has EXACTLY ONE handler and closure stays green. [P0]
- [FAILURE] `register_handler` on an OUTBOUND type → `ValueError("not an inbound type")`.
- [CAPABILITY] `register_projector` appends a projector (dedup: same projector not added twice); `register_producer` records an emitter (dedup).
- [FAILURE] `register_projector` on an INBOUND type → `ValueError("not an outbound type")`.
- [NUANCE] The import-time defaults wire `_default_channel_action_handler` for CHANNEL_ACTION and `_render_frame_projector` + `register_producer("backend.render-frame")` for every OUTBOUND — assert closure is green at import BEFORE any live host wiring runs.

### `validate_inbound_message` — the central untrusted-input validator
- [CAPABILITY] Returns a validated `ProxyMessage` for a well-formed, registered, in-bounds payload.
- [FAILURE] A non-dict payload → `TypeError("inbound message must be a JSON object")`.
- [FAILURE] A missing/unknown `type` discriminator → `ValueError("unregistered message type")`.
- [FAILURE] A malformed/oversized body → `ValueError("invalid ... message")` wrapping the `ValidationError` (bounded fields reject over-length free text).
- [NUANCE] This is the CENTRAL validator (§4.3) — assert `dispatch()` and the live gateway both route untrusted input through it (or the equivalent `model.model_validate` path), never a hand-rolled parse.

### The per-FIELD produce/consume field-diff (§4.8 — un-trimmed)
- [CAPABILITY] `collect_produced_fields()` walks the REAL `model_fields` of `AgentChunk` (via `_FIELD_DIFF_CONTRACT_MODELS`) AND every registered `CHANNEL_REGISTRY` frame — never a hand-list.
- [CAPABILITY] `collect_consumed_fields()` unions (1) the AST sweep of live service source (`sweep_consumer_reads`) for standalone contracts + (2) the whole-wire field-set of every registered frame.
- [CAPABILITY] `assert_contract_fields_consumed(strict=True)` RAISES `AssertionError` naming every orphan; `strict=False` returns the list.
- [FAILURE] A produced-but-never-consumed field (a frame field no consumer reads, minus the `_FIELD_DIFF_ALLOWLIST`) is a named violation. [P0]
- [FAILURE] A consumed-but-never-produced field (a consumer reads a name the model no longer carries — the OLD-name half of a rename drift) is a named violation, NEVER allow-listed. [P0]
- [NUANCE] A rename (`chunk.type`→`chunk.kind`) surfaces BOTH directions: `.kind` consumed-but-never-produced AND (if last reader) `.type` produced-but-never-consumed — assert both appear in the report (the drift class this gate exists for: AgentChunk `.kind`↔`.type`, envelope `verified|draft`↔`EnvelopeStatus`, `dm`↔`dm_available`).
- [NUANCE] `_FIELD_DIFF_ALLOWLIST` is currently EMPTY (frozenset()) — assert the DoD "empty or explicitly allow-listed" is satisfied by the empty set; a non-empty entry must name the field AND why it is construction-threaded.
- [NUANCE] The allow-list exempts ONLY the produced-but-unconsumed direction — the consumed-but-never-produced direction is NEVER allow-listed (a consumer reading a phantom name is always real drift).
- [EDGE] `collect_consumed_fields` is FAIL-SOFT: a missing/unreadable source tree (deployed wheel) → the sweep returns `{}` and only the whole-wire frames populate; the gate never crashes a production import.
- [NUANCE] `contracts/__init__.py` calls `collect_consumed_fields()` at import inside a `try/except Exception: pass` — assert a sweep failure NEVER breaks package import (the field-diff is CI/test-only).
- [WIRING] `MESSAGE_FIELD_PRODUCERS`/`MESSAGE_FIELD_CONSUMERS` are the records the doc-08 acceptance reads — assert they mirror the live model shape after `collect_*` (single source of truth, CANONICAL §11.5).
- [NUANCE] `assert_fields_consumed` is order-deterministic (sorts orphans) — assert the violation list is stable across runs.

### `MessageType` enum + INBOUND/OUTBOUND partitions
- [CAPABILITY] `INBOUND == {CHANNEL_ACTION}`; `OUTBOUND == all MessageType − INBOUND` (9 outbound render frames).
- [NUANCE] The tile is OUTBOUND-ONLY — `channel_action` is the sole inbound; assert no inbound tile/connect type exists (the pre-canonical shape is deleted).
- [EDGE] `_closure_values` handles an enum class, a Literal via `get_args`, or an iterable of enum members — assert all three produce the same value-set for the closure comparison.

---

## `libs/contracts/channel.py` — the concrete `ProxyMessage` bodies (inbound + outbound frames)

### `ChannelAction` (the sole inbound)
- [CAPABILITY] `type` fixed `Literal["channel_action"]`; registers under `"channel_action"`.
- [FAILURE] A non-UUID `meeting_id` → `ValidationError` (rejected before any DB lookup — the isolation soundness guarantee). [P0]
- [CAPABILITY] `surface` is `ActionSurface = Literal["voice","chat","canvas","screen"]` — `"tile"` is EXCLUDED (a human cannot click a video stream, §12.9); an inbound `surface="tile"` → `ValidationError`.
- [CAPABILITY] `action` is a closed `Literal` of exactly {share_screen, stop_share, walkthrough_on, walkthrough_off, catch_me_up, where_are_we, shorter, capabilities, show_your_work} — any out-of-set value → `ValidationError` (never an if/else fall-through).
- [FAILURE] `arg` over 2000 chars → `ValidationError` (the field-cap DoS bound). [P0]
- [CAPABILITY] `canvas_id` is `Optional[UUID]` (default None) — declared with `Optional`, not `|`, so `get_origin` stays Union for the isolation inspector; assert the funnel still treats it as a UUID entity id.
- [CAPABILITY] `arg` accepts None (default) and any string ≤2000; assert both a present and absent `arg` validate.
- [NUANCE] The socket-level payload cap is a SEPARATE bound owned by the gateway; this model owns only the field cap — assert both bounds exist independently.

### Outbound render frames (each is a registered `ProxyMessage`, serialized whole by `send()`)
- [CAPABILITY] `ResponseStart{meeting_id: UUID}`, `ResponseEnd{meeting_id: UUID}` register under their `response.start`/`response.end` types.
- [CAPABILITY] `ResponseChunk{response_id: Optional[UUID], chunk: str≤8000}` — `chunk` over 8000 → ValidationError.
- [CAPABILITY] `VoiceSpeak{text: str≤8000}` — a TTS text delta; over-length → ValidationError (never a bare `speak` dict).
- [CAPABILITY] `CanvasPatch{patch: str≤100000}` — the structured render payload capped to bound render DoS; over-length → ValidationError.
- [CAPABILITY] `ToolStart{line: str≤200}` — the humanized tool name for the tile "working…" line.
- [CAPABILITY] `TileState{state: Literal[8 states]}` — EXACTLY the eight §2.2 tile states {listening, listening-to, working, checking, has-something, speaking, muted, reaction}; a ninth/ad-hoc state → ValidationError (the renderer can never draw a self-decided state). [P0]
- [CAPABILITY] `NoteLine{text: str≤2000}`, `DraftCard{draft_id: UUID, summary: str≤2000}` register under `note.line`/`draft.card`.
- [NUANCE] `DraftCard` links to the `/m/` accept route via `draft_id`, never a raw URI — assert no URL field exists (human-control-absolute: the accept is a click on a staged route).
- [WIRING] `for _frame_model in CHANNEL_REGISTRY.values(): register_field_consumer(...)` at module import registers every frame's fields as whole-wire consumed — assert a NEW frame field is consumed the moment it exists (can never self-orphan).
- [NUANCE] Every projected frame is a registered `ProxyMessage` instance serialized via `model_dump()` — assert `send()` never emits a hand-built dict or an unregistered `"speak"` type.
- [EDGE] `response_id`/`canvas_id` `Optional[UUID]` fields — a null value serialises to null on the wire; assert the surface renderer tolerates the absent id.

---

## `libs/contracts/chunks.py` — `AgentChunk` streaming union + `ChunkType` discriminator

- [CAPABILITY] `ChunkType` is a `Literal["INIT","TEXT","TOOL_USE","TOOL_RESULT","RESULT","ERROR"]` — `get_args(ChunkType)` yields the six members for the contract oracle.
- [CAPABILITY] `ChunkType.TEXT == "TEXT"` etc. — attribute access works alongside `get_args` introspection (set via the module-level loop).
- [CAPABILITY] `AgentChunk{type: ChunkType, text: str|None=None, metadata: dict=default_factory}`.
- [NUANCE] `text` is `str | None` (default None), NOT `str = ''` (A16 / C-CHUNKNULL) — the SEALED shape; only TEXT carries text, every other variant has None; assert this cannot be narrowed to `str=''` without editing a sealed test. [P0]
- [NUANCE] `None` is a safe SUPERSET of `''`: the sole text consumer (`agentkit.deltas.stream_deltas`, `accumulated = chunk.text or ""`) coalesces None→"" — assert non-TEXT chunks never reach that read with a meaningful body.
- [WIRING] `AgentChunk` is the surviving STANDALONE field-diff contract (`_FIELD_DIFF_CONTRACT_MODELS`) — its consumer reads are AST-swept from live source (provider.py `chunk.type`, projector.py, session.py), NOT hand-listed; assert a `.type`→`.kind` rename fails the field-diff.
- [CAPABILITY] `AGENT_CHUNK_METADATA_KEYS` maps each variant to its expected metadata keys: INIT{session_id,tools,mcp_servers}, TEXT{msg_id}, TOOL_USE{id,name,input}, TOOL_RESULT{tool_use_id,is_error,structured}, RESULT{session_id,num_turns,total_cost_usd,structured_output}, ERROR{message}.
- [NUANCE] RESULT metadata carries `total_cost_usd` — the cost-meter seam; assert a RESULT chunk's cost is the same figure `call_external`'s telemetry would meter (cross-subsystem cost consistency).
- [FAILURE] An `AgentChunk` with an out-of-set `type` (not one of the six) → `ValidationError`.
- [EDGE] `metadata` defaults to a fresh dict per instance (`default_factory=dict`) — assert two chunks do not share a mutable metadata dict.

---

## `libs/contracts/contract_reads.py` — AST sweep deriving REAL consumer field-reads

- [CAPABILITY] `sweep_consumer_reads()` walks every `services/*/src` + `libs/*/src` module (EXCLUDING `libs/contracts`) and returns `{contract_name: {field_read}}` for `_SWEPT_CONTRACTS` (currently `{"AgentChunk"}`).
- [CAPABILITY] Binds a local to a contract via three grounded signals: (1) annotated param/assignment (incl. unwrapped `AsyncIterator[AgentChunk]`/`list[AgentChunk]`/`AgentChunk|None`/`Optional[AgentChunk]`), (2) `isinstance` narrowing, (3) direct construction `x = AgentChunk(...)`.
- [CAPABILITY] Records `<bound-name>.<attr>` READS (`ast.Attribute` in `Load` ctx) as consumed fields; a Store/Del (write) is NOT counted.
- [NUANCE] A constructor keyword site (`AgentChunk(type=..., text=...)`) is a PRODUCER, not a read — assert keyword args are NOT counted as consumer reads (else the diff is a tautology). [P0]
- [NUANCE] `_BASEMODEL_API` (pydantic `model_dump`/`model_validate`/etc.) attribute reads are EXCLUDED — computed off `BaseModel` so the exclusion never drifts; assert a `chunk.model_dump()` is not recorded as a field read.
- [EDGE] Dunder / underscore-prefixed attrs (`chunk._private`, `chunk.__class__`) are never counted as a produced field.
- [EDGE] `libs/contracts` itself is excluded from the sweep (the declaration home; a read there is not a downstream consumer).
- [FAILURE] A syntactically-unparseable module → `SyntaxError` caught and SKIPPED (the no-redeclaration sweep surfaces it separately) — the sweep never crashes on a bad file.
- [EDGE] `_annotation_names` recovers the element type from wrappers AND from a string forward-ref annotation (`"AgentChunk"`) by re-parsing it; assert a forward-ref-annotated param binds.
- [EDGE] `_contract_in_annotation` returns the contract ONLY when EXACTLY ONE swept contract is named (a union of two contracts is skipped, not mis-bound).
- [EDGE] `isinstance` narrowing binds `x` inside the `if` body ONLY, restores the prior binding in the `else`/after (assert a read after the block does not falsely count).
- [EDGE] Nested function scopes inherit outer bindings; a fresh scope restores the outer `_bindings` on exit (assert a binding in an inner function does not leak to a sibling).
- [EDGE] `async for chunk in <annotated-stream>` binds the loop target to the element type (assert the streaming consumer's `chunk.text` read is captured).
- [NUANCE] `_REPO_ROOT` is derived from `__file__.parents[4]` — assert the sweep root resolves correctly whether run from source or a checkout (a wrong root silently returns {} and hides drift).
- [FAILURE] If the repo layout moves (parents[4] no longer the root), the sweep silently returns nothing → the field-diff passes VACUOUSLY — assert a non-empty AgentChunk consumer set is found on the real tree (a guard against a vacuous-pass regression). [P0]

---

## `libs/contracts/bundle.py` · `material_change.py` · `notes.py` · `readiness.py` — the cross-service data contracts

- [CAPABILITY] `Bundle{ask, speaker, timestamp: datetime, notes_ref: UUID, transcript_tail: str="", task_id: UUID}` — the 04→05 ask handoff.
- [NUANCE] `notes_ref` is a UUID HANDLE, never an embedded notes object (Truth-is-live: notes fetched fresh, never carried) — assert no notes body field exists on Bundle. [P0]
- [NUANCE] `transcript_tail` is a single STRING, not a list (CANONICAL §11.5 D-026) — assert the type is `str`, defaulting `""`.
- [CAPABILITY] `MaterialChangeKind` is a CLOSED `StrEnum` of exactly seven kinds {claim-landed-checkable, decision-forming, decision-final, contradiction, action-item, question-open, question-closed}.
- [NUANCE] The enum is CLOSED (00-FOUNDATION §46) — the dropped 'note' shorthand is NOT a member; decision/question are expanded forming/final and open/closed variants, never combined — assert no 8th kind, no combined member. [P0]
- [CAPABILITY] `NoteDelta{op: Literal["add","patch","close"], note_id: UUID|None=None, body: dict=default_factory}` — 'note' folded into 'add'.
- [EDGE] `NoteDelta` with `op="patch"`/`"close"` and `note_id=None` — assert whether a null note_id on a non-add op is valid (currently permitted by the type; a semantic gap to flag).
- [CAPABILITY] `Readiness = Literal["connecting","cloning","indexing","ready","not_ready"]` — 'mapping' is Expansion, ABSENT here; a `"mapping"` status → ValidationError.
- [CAPABILITY] `ReadinessReport{status: Readiness, coverage_pct: float=0.0, gaps: list[str]=default_factory}`.
- [NUANCE] These four are plain `BaseModel` (NOT `ProxyMessage`) — they are NOT in `CHANNEL_REGISTRY` and NOT subject to the WS closure; assert they never leak into `assert_registry_closed`.

---

## `libs/contracts/__init__.py` + `libs/http/__init__.py` — package import surface

- [WIRING] Importing `contracts` fires `channel` import → every `ProxyMessage` self-registers → `CHANNEL_REGISTRY` populated BEFORE `assert_registry_closed` can be called; assert import order is deterministic.
- [NUANCE] `contracts/__init__` calls `collect_consumed_fields()` at import inside `try/except Exception: pass` — assert a deployed wheel (no source tree) imports cleanly with only the whole-wire consumer set populated.
- [CAPABILITY] Both `__init__` `__all__` lists match the actually-exported names — assert every re-export resolves (a rename that breaks a re-export fails the import).
- [WIRING] `libs/http` imports `contracts.registry.CHANNEL_REGISTRY` in `dispatch.py` — this is the ONE cross-lib dependency; assert `contracts` does NOT depend on `http` (no cycle).

---

## Cross-subsystem integration points

### Subsystems that route EXTERNAL calls through the `libs/http` seam (`call_external` + factories)
- **`services/premeeting/github_auth.py`** — GitHub App auth/clone via `_call_external` (service="github"); the pre-meeting connect→clone path.
- **`services/premeeting/pipeline.py` + `comprehension.py`** — clone + repo-map build external calls (GitHub, GCS store).
- **`services/in-meeting/transport/recall.py`** — Recall bot lifecycle (join/leave/output-media/status) via `_call_external` (service="recall"); the meeting transport.
- **`services/in-meeting/transport/tts.py`** — Cartesia TTS synthesis via `call_external` (service="tts"); the speak path.
- **`services/in-meeting/transport/external.py` + `seams.py`** — the transport's external seam wrappers (STT/Recall).
- **`services/in-meeting/speak.py` + `workroom.py`** — meeting speech + sandbox interaction.
- **`services/control-plane/provisioner.py` + `meetings.py` + `relay.py`** — E2B sandbox provisioning (`AsyncSandbox.create/kill/set_timeout/is_running`) + host relay to meeting; via `call_external`.
- **`services/control-plane/github_webhook.py` + `webhook_routes.py`** — GitHub push ingress verification (uses `verify_github_signature`) and Recall webhook (uses `verify_recall_signature`).
- **`services/control-plane/gateway_route.py`** — mounts `authorize_upgrade` (WS auth) + drives `dispatch()` (the funnel) against a repos-backed `Store`.
- **`services/control-plane/app.py`** — installs `safe_error_handler`, mounts `protected()`/`public()` routes, classified by `classify_route` in the route-enumeration security test.
- **`libs/ops/sandbox_provider.py`** — the E2B sandbox provider wraps every sandbox op (`create/kill/set_timeout/is_running`) through `_http.call_external` (service="e2b").
- **`libs/agentkit`** — the native-Claude execution layer; the Anthropic model client is constructed ONLY via `http.external.anthropic_client` (through the SDK provider).
- **`libs/ops/check_call_external.py`** — the ENFORCER guard: AST-scans `services/`+`libs/` and fails the build if ANY raw client (Anthropic/httpx/GCS/Recall/STT/TTS/E2B) is constructed outside `libs/http`.

### Subsystems that PRODUCE / CONSUME the `libs/contracts` types
- **`services/control-plane/gateway_route.py`** — consumes `CHANNEL_REGISTRY` + `dispatch`; the funnel validates inbound `ChannelAction`, routes to `handle_channel_action`.
- **`services/control-plane/connect.py`** — produces/consumes readiness + connect contracts (`ReadinessReport`/`Readiness`).
- **`services/in-meeting/transport/signals.py`** — the SIGNAL-SURFACE events (transcript/roster/speaking/boundary/barge-in/bot-status/meeting-end/channel-report) that MUST stay OUT of `CHANNEL_REGISTRY` (the §11.8 non-leak invariant).
- **`libs/agentkit/sdk_provider.py`** — produces `AgentChunk` streaming chunks (INIT/TEXT/TOOL_USE/TOOL_RESULT/RESULT/ERROR) with `AGENT_CHUNK_METADATA_KEYS`; the standalone field-diff contract.
- **in-meeting engine / projectors** — CONSUME `AgentChunk` (`chunk.type`/`chunk.text`/`chunk.metadata`) via `agentkit.deltas.stream_deltas`; project outbound render frames (`ResponseStart/Chunk/End`, `VoiceSpeak`, `CanvasPatch`, `ToolStart`, `TileState`, `NoteLine`, `DraftCard`).
- **`libs/ops/check_field_contract.py`** — the ENFORCER guard: calls `assert_registry_closed()` (boot + CI) so a produced-but-unregistered type / unhandled inbound / unprojected outbound / leaked signal event fails the build.
- **`libs/contracts/contract_reads.py`** — sweeps EVERY `services/*/src`+`libs/*/src` for real `AgentChunk` field reads — the whole product is its consumer surface for the field-diff.
- **Bundle / MaterialChangeKind / NoteDelta / ReadinessReport** — the 03→04→05 cross-phase data contracts (Scribe→Orchestrator→Workroom in the pre-canonical naming; now the pre-meeting→meeting handoffs), plain `BaseModel`s outside the WS closure.

### The two enforcement guards that must stay green for customer deploy
1. `check_call_external` (single-seam) — NO raw vendor client outside `libs/http`; the retry+cost wrapper is unbypassable.
2. `assert_registry_closed` + `assert_contract_fields_consumed` (closure + field-diff) — every wire type registered, every inbound handled, every outbound projected, no signal-event leak, no produce/consume drift.
