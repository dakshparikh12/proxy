# Premeeting Subsystem — Exhaustive Test Requirements
> Generated 2026-08-04. Source: every .py file in services/premeeting/src/premeeting/ read in full.
> Architecture: connect → clone (cloner.py) → map-build (map_build.py + symbol_map.py + comprehension.py + understanding.py) → store (map_store.py) → verify (verify.py) → ready (readiness.py / pipeline.py); push-refresh via refresh.py; downstream handoff via repo_context.py.

---

## 1. github_auth.py — JWT mint + token delivery

### [CAPABILITY] build_app_jwt signs a valid RS256 JWT with correct claims
- `iss` = app_id string, `iat` backdated 60s for clock skew, `exp` = iat + 540s (under GitHub's 10-min ceiling)
- Uses PKCS1v15 + SHA256 (RS256) — the exact algorithm GitHub requires

### [CAPABILITY] InstallationTokenMinter.mint posts to /app/installations/<id>/access_tokens and returns the token string
- Bearer header carries the App JWT; Accept: application/vnd.github+json
- Returns ONLY the token string — never a dict, never the full response

### [CAPABILITY] Token never cached on the instance — a new JWT+token is minted per .mint() call
- Two sequential .mint() calls produce two separate HTTP POSTs (no instance-level cache)

### [WIRING] mint() goes through call_external seam (libs.http)
- The raw HTTP client is constructed inside libs.http.external.http_client only
- No raw httpx/requests client lives in github_auth.py itself

### [WIRING] AuthError typed exception propagates up to pipeline.run_pipeline() → honest not_ready
- A 4xx, 5xx, or transport fault raises AuthError, never returns a partial result
- pipeline catches AuthError and returns PipelineResult(ready=False, reasons=["auth: ..."])

### [EDGE] Non-RSA private key raises AuthError
- An EC or DSA key in PEM form → `isinstance(key, RSAPrivateKey)` is False → AuthError raised before any sign

### [EDGE] GitHub returns HTTP 422 (invalid installation_id) → AuthError("token mint returned HTTP 422")
### [EDGE] GitHub returns HTTP 401 (expired JWT / bad key) → AuthError with status code named
### [EDGE] Network timeout / connection reset mid-POST → outer except BLE001 wraps → AuthError("token mint failed: ...")
### [EDGE] Response body missing "token" key → AuthError("token mint response carried no token")
### [EDGE] JWT iat/exp clock skew — iat is NOW-60s; a GitHub server 30s ahead still accepts it

### [FAILURE] Token mint failure → pipeline returns not_ready naming "auth: ..." — never a hang, never a silent empty clone
### [FAILURE] App private key has wrong PEM passphrase → load_pem_private_key raises → AuthError propagated

### [NUANCE] Token string is NEVER logged — the .mint() return value must not appear in any log output
### [NUANCE] Only the token string is extracted from the response body (body["token"]); the rest of the response is discarded
### [NUANCE] JWT TTL is 9 min (540s), backdated 60s — live token arrives at GitHub with ≥60s and ≤9min of valid window

---

## 2. cloner.py — per-tenant clone + delta-pull

### [CAPABILITY] Cloner.clone() materialises a clean work-tree at <volume>/<tenant>/repos/<repo>/checkout
- .git dir is at checkout.parent/.git (bare layout with core.bare=false + core.worktree pointing at checkout)
- checkout/ has NO .git subdirectory — walk is clean for map-build skeleton

### [CAPABILITY] Private repo clones via authenticated URL (x-access-token:<token>@github.com/…)
- build_authenticated_url injects token as userinfo on https:// URLs only
- ssh:// or file:// URLs are returned unchanged (no injection attempted)

### [CAPABILITY] Blobless clone triggered above _BLOBLESS_FILE_THRESHOLD (100k files) via file_count_provider seam
- A file_count_provider returning > 100k → clone_args includes --filter=blob:none
- file_count_provider absent (default) → normal clone, no blobless flag

### [CAPABILITY] ExclusionManager.scan_after_clone runs after every successful clone
- A .env file in the repo is excluded before any map-build read touches it

### [CAPABILITY] pull_delta does a fetch+fast-forward, never a full re-clone
- Uses git fetch + update-ref (to move HEAD) + checkout -f — never git clone
- Returns the same checkout path on success

### [CAPABILITY] pull_delta re-points origin at a freshly-authenticated URL before each fetch (re-minted token)

### [WIRING] run_git is the only git subprocess entry — cloner never calls subprocess directly
### [WIRING] Token is REDACTED at the record boundary in gitio.run_git before interception/logging

### [EDGE] Existing repo dir is cleaned (shutil.rmtree) before each fresh clone — idempotent re-clone
### [EDGE] sha parameter specified → git checkout <sha> -- . materialises that exact commit (not HEAD)
### [EDGE] sha=None → "HEAD" used as target → tip of default branch
### [EDGE] git clone fails (unreachable upstream, bad credentials) → returncode != 0 → empty checkout returned (no raise)
### [EDGE] checkout exists but is empty → pipeline detects not clone_path.exists() or not any(clone_path.iterdir()) → not_ready
### [EDGE] pull_delta called with no known checkout (not in self._by_url and clone_path=None) → returns None
### [EDGE] pull_delta fetch fails (network down) → run_git returns non-zero → checkout returned (partial state) — verify catches gap
### [EDGE] Token contains special URL characters (e.g. %) → urlunparse re-encodes correctly
### [EDGE] repo_url ends with .git → repo_name_from_url strips .git suffix → dir name without .git

### [FAILURE] Clone of a large repo (>1GB) mid-transfer network drop → returncode != 0 → empty checkout → not_ready (named)
### [FAILURE] Disk full during clone → OSError → checkout partially populated → verify catches missing dirs → not_ready

### [NUANCE] Token is NEVER in any logged argv — test: pass a fake interceptor; assert token not in any recorded arg
### [NUANCE] run_git refuses "push" — even if a caller mistakenly adds "push", RuntimeError is raised before exec
### [NUANCE] Tenant isolation: two tenants with the same repo URL land in separate paths (tenant_repo_dir isolation)

---

## 3. exclusions.py — secret-path filtering + value redaction

### [CAPABILITY] Default glob patterns exclude .env, .env.*, *.env, secrets.*, credentials, *.pem, *.key, id_rsa, id_rsa.*
### [CAPABILITY] Policy globs (caller-supplied) are merged with defaults — a tenant's custom secret globs also excluded
### [CAPABILITY] AWS AKIA key pattern (AKIA[0-9A-Z]{16}) is collected from source files during scan
### [CAPABILITY] secret|token|password|api_key|access_key name-anchored pattern collects high-entropy RHS values
### [CAPABILITY] Connection URI userinfo pattern (scheme://user:pass@host) collects full userinfo block
### [CAPABILITY] redact() replaces every collected secret value with [REDACTED] in any text passed through it
### [CAPABILITY] is_excluded(rel) returns True for secret-path files — used by symbol_map, verify, repo_context

### [WIRING] scan_after_clone called immediately after git checkout in Cloner.clone()
### [WIRING] scan_after_pull called after pull_delta (delta set only, not full rescan)
### [WIRING] verify_map calls exclusions.is_excluded + exclusions.secret_values() for PM-VERIFY-03
### [WIRING] verify_comprehension calls exclusions.redact(cleaned) before returning the final text
### [WIRING] repo_context._scanned_exclusions() creates a fresh ExclusionManager per meeting session

### [EDGE] A source file contains an AKIA key inside a comment → key still collected and redacted on read
### [EDGE] A value that is a plain snake_case code identifier (e.g. token_normalize_func) → _is_code_identifier returns True → NOT collected as a secret
### [EDGE] An ALL-CAPS blob (AKIAABCDEFGHIJKLMNOP) → _is_code_identifier returns False → IS collected
### [EDGE] Connection URI without password (scheme://host/db) → no match (requires colon in userinfo) → not collected
### [EDGE] File too large to read (OSError on read_text) → _collect_secret_values catches OSError silently
### [EDGE] Policy glob added after scan_after_clone → is_excluded() uses the live _policy_globs → late-added policy works
### [EDGE] Two tenants with identically-named secret files → ExclusionManager instances are per-pipeline-run, never shared

### [FAILURE] gitleaks integration absent (None) → scan proceeds without it, no crash
### [FAILURE] scan_after_pull with changed_files=None → treated as empty list → no crash

### [NUANCE] Secret values collected are set-of-strings; redact() replaces ALL occurrences in the passed text
### [NUANCE] A secret path is excluded even if gitleaks is not present (path-glob exclusion is independent)
### [NUANCE] _matches_glob handles directory-glob patterns (ending /) as prefix matches

---

## 4. gitio.py — git subprocess seam

### [CAPABILITY] redact_argv masks x-access-token:<token>@ → x-access-token:***@ in every argv element
### [CAPABILITY] run_git refuses push — raises RuntimeError before subprocess.run if "push" in args
### [CAPABILITY] list_tracked_files returns all git-tracked repo-relative paths via git ls-files
### [CAPABILITY] head_sha returns the 40-char HEAD SHA at the checkout

### [WIRING] Interceptor receives REDACTED argv (safe_argv), never the raw argv with the live token
### [WIRING] list_tracked_files is used by verify_map._uncovered_top_dirs to identify required top-level dirs

### [EDGE] redact_argv on an argv with no token → strings unchanged (no substitution side effects)
### [EDGE] Multiple token occurrences in a single argv element → all replaced (regex is global)
### [EDGE] list_tracked_files called on a path that is NOT the work-tree top-level → rev-parse --show-toplevel returns different path → returns None
### [EDGE] head_sha on a non-existent path → early return None
### [EDGE] git ls-files on a very large repo (100k+ files) → stdout may be several MB → handled as plain string split

### [FAILURE] git binary not on PATH → subprocess.run raises FileNotFoundError → propagated to caller (Cloner catches returncode != 0 implicitly — actually does NOT catch this)
### [FAILURE] git rev-parse fails (malformed .git) → returncode != 0 → list_tracked_files returns None → verify degrades gracefully

### [NUANCE] "push" guard is on args list membership check — "push-upstream" or "--push" would not trigger it (only exact "push")

---

## 5. paths.py — per-tenant volume rooting

### [CAPABILITY] volume_root() returns PROXY_TENANT_VOLUME_ROOT env override if set
### [CAPABILITY] Falls back to /tenants if writable, then to tempfile.gettempdir()/proxy-tenants
### [CAPABILITY] tenant_repo_dir(tenant_id, repo_name) → <root>/<tenant>/repos/<repo_name>
### [CAPABILITY] Blank tenant_id raises ValueError — prevents collapse to shared volume root

### [WIRING] Used by Cloner.clone() and refresh_on_push() to locate the tenant's checkout path

### [EDGE] tenant_id with spaces or slashes → path components → possible path traversal (blank check only guards empty string, not slash)
### [EDGE] repo_name_from_url strips trailing .git and takes the last path segment
### [EDGE] repo_url with no slash (bare name) → rsplit returns the whole string → safe
### [EDGE] /tenants exists but is not writable → OSError or os.access returns False → fallback to temp

### [NUANCE] Two tenants with the same repo name land at different root dirs — isolation is at the tenant root level

---

## 6. symbol_map.py — deterministic tree-sitter map (Part 1)

### [CAPABILITY] build_symbol_map produces a ranked, groundable symbol map with real file:line — no model call, no hallucination
### [CAPABILITY] build_navigation_map produces a compact area/entry-point navigation aid (no ranked-signatures body)
### [CAPABILITY] _scan_repo walks every non-.git, non-excluded, parseable source file and extracts def/ref tags
### [CAPABILITY] _rank_tags builds a MultiDiGraph (referencer→definer edges) and runs personalized PageRank
### [CAPABILITY] _fit_to_budget binary-searches the tag count to stay within budget_tokens (~11k)
### [CAPABILITY] _architecture_header outputs the # Symbol map heading + ## Where things live + ## Entry points + ## Ranked signatures
### [CAPABILITY] _navigation_header outputs the # Navigation map heading + ## Where things live + ## Entry points (no ranked-signatures)
### [CAPABILITY] REQUIRED_MAP_MARKERS and REQUIRED_NAV_MARKERS are the verify gate's shape check anchors
### [CAPABILITY] Files > 1MB (_MAX_FILE_BYTES) are skipped — blob/generated-bundle guard
### [CAPABILITY] Rendered lines are clamped to _MAX_LINE_CHARS (100 chars) — minified-file budget guard
### [CAPABILITY] _is_non_domain filters test/scripts/docs/vendor/archive dirs from entry-point ranking
### [CAPABILITY] _fallback_rank used when graph has no cross-file edges (tiny single-file repos)
### [CAPABILITY] PageRank non-convergence degrades to uniform rank (never crashes)
### [CAPABILITY] _run_captures handles both new (dict-shaped) and old (list-of-pairs) tree-sitter API shapes

### [WIRING] Called by map_build.build_map() for the deterministic Part-1 base
### [WIRING] Called by map_build.build_understanding_map() as build_navigation_map() for the comprehension pass's nav aid
### [WIRING] REQUIRED_MAP_MARKERS / REQUIRED_NAV_MARKERS imported and used by verify.py

### [EDGE] Repo with no parseable source files → "no parseable source symbols found" stub → verify passes (special-cased)
### [EDGE] Repo with only one language file → no cross-file refs → _fallback_rank → map still renders
### [EDGE] A file with UTF-8 errors (binary-ish text) → read_text(errors="replace") → no crash
### [EDGE] A tree-sitter grammar query parse error on one file → BLE001 catch → file skipped, rest continues
### [EDGE] TreeContext.format() raises on a malformed file → BLE001 catch → file skipped
### [EDGE] budget_tokens = 0 or very small → binary search degrades to rendering at least ranked_tags[:1]
### [EDGE] All files are in non-domain dirs → entry-point list backfills from non-domain set
### [EDGE] Language with no query file in _QUERY_DIR → _tags_for_file yields nothing → file skipped silently
### [EDGE] Very large repo (100k files, >1M symbols) → _scan_repo iterates all, PageRank may be slow — no hard timeout
### [EDGE] Two tree-sitter API versions coexist (upgrade scenario) → _run_captures handles both shapes

### [FAILURE] networkx not installed → nx.pagerank import fails → caught by outer try/except → uniform-rank fallback
### [FAILURE] scipy backend missing (pagerank may need it for large graphs) → PowerIterationFailedConvergence caught → uniform rank

### [NUANCE] REQUIRED_MAP_MARKERS = ("# Symbol map", "Where things live", "Entry points", "Ranked signatures") — ALL four must be present for verify to pass for a symbol-map artifact
### [NUANCE] REQUIRED_NAV_MARKERS = ("# Navigation map", "Where things live", "Entry points") — three markers for nav-aid artifact
### [NUANCE] Non-domain dir filtering uses _NON_DOMAIN_TOP_DIRS frozenset + archive/deprecated/legacy substring check — a dir named "archive" is excluded from entry-point hints
### [NUANCE] The 1-based line number correction: tree-sitter rows are 0-based; Tag.line = node.start_point[0] + 1
### [NUANCE] Aider's edge weight formula: mul * sqrt(num_refs), where mul is 10x for long snake/camel identifiers, 0.1x for _private, 0.1x for >5-definer names

---

## 7. comprehension.py — Part 2 holistic comprehension pass + verification

### [CAPABILITY] build_comprehension provisions a read-only E2B sandbox, runs ONE bounded native-Claude turn (claude-agent-sdk), reads the written understanding back, and returns a verified ComprehensionResult
### [CAPABILITY] The comprehension prompt forbids pasted code, exact line numbers, and invented components — qualitative, holistic mental model only
### [CAPABILITY] verify_comprehension grounds every file:line claim the prose makes against the real clone — drops ungrounded ones
### [CAPABILITY] extract_file_line_claims matches "path:line" patterns but NOT bare filenames, versions (3.14), or symbol refs (ctx.invoke)
### [CAPABILITY] _resolve_in_clone accepts exact path OR unique-basename OR unique-tail-suffix match — a GO internal package abbreviated path is still grounded
### [CAPABILITY] Wholesale rejection when < half the claims ground (a mostly-fabricated doc is worse than none)
### [CAPABILITY] Wholesale rejection when verified text < 400 chars (too thin after stripping)
### [CAPABILITY] _strip_bad_claims removes backtick-framed, paren-framed, or bare ungrounded path:line tokens
### [CAPABILITY] exclusions.redact(cleaned) runs on the verified prose — inline secret values in the comprehension are scrubbed
### [CAPABILITY] Sandbox lifetime = _SETUP_BUDGET_S (420) + _ASK_TIMEOUT_S (600) + _SANDBOX_LIFETIME_MARGIN_S (180) = 1200s total
### [CAPABILITY] sandbox.set_timeout refreshed just before the long comprehension run — prevents mid-turn end-of-life kill
### [CAPABILITY] ANTHROPIC_API_KEY is popped from env inside the sandbox runner — subscription CLI auth only

### [WIRING] Called by map_build.build_understanding_map() when call + token are provided
### [WIRING] Receives the COMPACT navigation map (not the full ranked-signatures dump) as the sandbox navigation index
### [WIRING] call seam (libs.http.call_external) wraps every E2B round-trip
### [WIRING] sandbox_class injected for testing (fake sandbox); real class resolved lazily from libs.http.external.e2b_sandbox_class()
### [WIRING] token is CLAUDE_CODE_OAUTH_TOKEN (subscription, ~$0) — NOT the paid API key

### [EDGE] token is empty or whitespace → early return ComprehensionResult(reasons=["no subscription token..."]) — Part 1 stands
### [EDGE] clone_path does not exist → ComprehensionResult(reasons=["clone path does not exist — comprehension unverifiable"])
### [EDGE] Sandbox provision fails (E2B quota, network error) → BLE001 catch → honest ComprehensionResult with reason
### [EDGE] Model writes DONE but forgets to write the output file → OUT path does not exist → runner writes empty string → build_comprehension gets empty raw → ComprehensionResult(reasons=["comprehension pass produced no understanding"])
### [EDGE] setup command (git clone + pip install) times out within _SETUP_BUDGET_S → command returns non-zero → runner file not executed → empty OUT
### [EDGE] A large repo takes >420s to clone in the sandbox → setup timeout expires → empty understanding → honest degrade
### [EDGE] sandbox.set_timeout not callable on fake sandbox → try/except catches → no crash
### [EDGE] sandbox.kill() raises during finally → BLE001 catch → warning logged, no crash
### [EDGE] Comprehension prose has claims where >50% are ungrounded → wholesale rejection → ok=False
### [EDGE] Comprehension prose has zero file:line claims (all qualitative) → no claim checking → passes as long as text >= 400 chars
### [EDGE] Comprehension prose contains a hard-coded credential (mongodb://user:pass@host) → exclusions.redact scrubs it
### [EDGE] _FILE_LINE_RX matches a dotfile citation (.env:1) → verify_comprehension calls excluded() on ".env" → drops it
### [EDGE] Comprehension text references a file that exists at a unique basename only → _resolve_in_clone finds it via rglob
### [EDGE] Ambiguous basename (multiple files share the same name) → _resolve_in_clone tries tail suffix; if still ambiguous → returns None → claim dropped

### [FAILURE] Network reset mid comprehension run (E2B command times out) → runner writes .err file + empty OUT → build_comprehension returns empty result
### [FAILURE] Model produces output > _ASK_TIMEOUT_S processing time → asyncio.wait_for inside runner raises TimeoutError → OUT written empty → degrade
### [FAILURE] E2B sandbox killed by end-of-life mid-run (lifetime too short) → command exception → raw = "" → honest degrade

### [NUANCE] The comprehension prompt explicitly instructs NO file:line in prose — the design intention is that verify_comprehension finds zero claims to check and passes purely on substance
### [NUANCE] "ok" is True even if claims_checked == 0 AND text length >= 400 — a purely qualitative doc passes
### [NUANCE] The runner uses setting_sources=[] to suppress CLAUDE.md/MCP loading in the sandbox — full native toolset only
### [NUANCE] effort="high" + thinking={"type": "adaptive"} — consistent with the meeting workroom's native-Claude configuration
### [NUANCE] MAX_TURNS = 40 — bounded but generous; a small repo may finish in 5-10 turns; a large one may hit the cap and write a partial understanding (still useful if >= 400 chars and mostly grounded)

---

## 8. understanding.py — composition of Part 1 + Part 2

### [CAPABILITY] build_understanding composes ONE document: _COMPREHENSION_HEADER + comprehension + _NAV_HEADER + navigation
### [CAPABILITY] Empty comprehension → returns navigation alone (no naked divider)
### [CAPABILITY] Empty navigation with non-empty comprehension → returns comprehension alone
### [CAPABILITY] Both empty → returns empty string

### [WIRING] Called by map_build.build_understanding_map() when comp.ok is True

### [EDGE] comprehension has trailing whitespace → .strip() normalises before combining
### [EDGE] navigation has trailing whitespace → .strip() normalises

### [NUANCE] _COMPREHENSION_HEADER explicitly tells the reading agent that this is NOT a code index and that exact file:line must be looked up live — this shapes in-meeting agent behavior
### [NUANCE] _NAV_HEADER is a markdown horizontal rule — creates visual separation between the qualitative block and the navigation aid

---

## 9. map_build.py — map-build orchestrator

### [CAPABILITY] build_map() returns the deterministic Part-1 symbol map (no model call) as MapBuildResult
### [CAPABILITY] build_understanding_map() runs Part 1 always, Part 2 only when call + token provided; combines via build_understanding()
### [CAPABILITY] Honest degrade: if comp.ok is False or text is empty → artifact is symbol map alone (MapBuildResult.degraded=True)
### [CAPABILITY] _is_failed_build detects empty body, known API error strings, and too-short non-map bodies
### [CAPABILITY] _build_map_llm (deprecated) retries up to PROXY_MAP_BUILD_ATTEMPTS times on a failed build before degrading to skeleton map
### [CAPABILITY] _degraded_map produces a complete top-level map from the skeleton + "depth via live search" note
### [CAPABILITY] build_skeleton builds a bounded-depth tree (MAX_SKELETON_DEPTH=3, MAX_SKELETON_LINES=2000)
### [CAPABILITY] collect_high_yield returns existing non-excluded high-yield files (README, manifests, CI, etc.)
### [CAPABILITY] SYMBOL_MAP_BUDGET_TOKENS = 11000 — the token budget for the resident understanding

### [WIRING] Called by pipeline.run_pipeline() and refresh.refresh_on_push()
### [WIRING] build_understanding_map imports comprehension.build_comprehension lazily (only when call/token present) — offline path never imports E2B modules

### [EDGE] call provided but token empty → early return of Part-1 only (no Part 2)
### [EDGE] PROXY_MODEL_MAP env set → _default_map_model uses it; else PROXY_MODEL_ANSWER; else claude-sonnet-4-6
### [EDGE] Provider=None passed to build_map → _ = provider → no crash (signature compatibility)
### [EDGE] build_skeleton on a directory that raises OSError during iterdir → returns empty string (caught in _walk)
### [EDGE] collect_high_yield when a high-yield file is excluded → not included in the batch list
### [EDGE] skeleton truncated at MAX_SKELETON_LINES → "(skeleton truncated — use live search for depth)" appended
### [EDGE] _build_map_llm (deprecated): content-filter block captured as text → _is_failed_build detects "output blocked" → degrades to skeleton map

### [FAILURE] comprehension.build_comprehension raises unexpectedly → build_understanding_map has no outer try/except; an exception bubbles to pipeline → pipeline's map-build stage has no try/except either — this IS a gap (pipeline returns not_ready only for store/verify, not for unhandled map-build exception)

### [NUANCE] build_understanding_map provides the COMPACT navigation map (build_navigation_map) to the comprehension pass, NOT the full symbol map — this is by design (the comprehension's real work is reading the domain code)
### [NUANCE] The deprecated _build_map_llm is preserved but NOTHING on the live path calls it — any code paths that still call it are dead and must be removed

---

## 10. map_store.py — durable Postgres map store

### [CAPABILITY] save_map upserts the map for (tenant_id, repo, sha) — idempotent on PK conflict (re-builds at same SHA overwrite)
### [CAPABILITY] load_map returns ONLY the row for (tenant_id, repo, sha) — always tenant-scoped
### [CAPABILITY] load_latest_map returns (sha, map_text) for the most recently built map for (tenant_id, repo)
### [CAPABILITY] MapStore.save/load/load_latest resolve a fresh connection per call (Database.acquire())
### [CAPABILITY] A cross-tenant read is impossible: the WHERE clause always includes tenant_id = $1

### [WIRING] Called by pipeline.run_pipeline() for durable storage
### [WIRING] Called by refresh.refresh_on_push() for re-storage at new SHA
### [WIRING] load_latest_map consumed by in_meeting.workroom when mounting the resident understanding at meeting start
### [WIRING] Alembic migration 0009_repo_maps creates the table with FK tenant_id → tenants(id)

### [EDGE] PK conflict on (tenant_id, repo, sha) → DO UPDATE SET map = EXCLUDED.map → old map replaced, built_at refreshed
### [EDGE] tenant_id does not exist in tenants table → FK violation → asyncpg raises → exception propagates to pipeline → stored as store fault → not_ready
### [EDGE] Connection pool exhausted → acquire() blocks or raises → propagated to pipeline → honest not_ready
### [EDGE] map_text is extremely large (>1MB) → Postgres text column handles it; asyncpg serialises it → no silent truncation
### [EDGE] load_map miss (no row) → fetchrow returns None → returns None (not an exception)
### [EDGE] load_latest_map when no maps exist for the tenant/repo → returns None
### [EDGE] Concurrent save calls for the same (tenant, repo, sha) → ON CONFLICT DO UPDATE is atomic — no duplicate row

### [FAILURE] store fault (any exception from conn.execute) → pipeline returns not_ready naming "store: <ExcType>" — never a silent success

### [NUANCE] Tenant isolation: load_map(tenant_B, repo, sha) can NEVER return tenant_A's row even if they have the same repo+sha — PK carries tenant_id first
### [NUANCE] built_at uses now() at upsert time — a re-build for the same SHA updates built_at, making load_latest_map return it as fresher

---

## 11. verify.py — deterministic readiness gate

### [CAPABILITY] verify_map runs 5 checks: (1) non-empty + shape markers, (2) no hallucinated file paths, (3) all top-level tracked dirs covered, (4) no secret leak (path or value), (5) ready only on clean pass
### [CAPABILITY] "no parseable source symbols" stub map passes immediately (no claims to verify)
### [CAPABILITY] Accepts EITHER symbol-map shape (REQUIRED_MAP_MARKERS all present) OR nav-map + comprehension shape (REQUIRED_NAV_MARKERS all present)
### [CAPABILITY] extract_named_paths extracts path-shaped tokens; _is_path_claim filters to actual file claims (not URL/domain/symbol/prose)
### [CAPABILITY] _path_exists_in_clone checks exact path OR any file in clone (hallucination by basename check too)
### [CAPABILITY] list_tracked_files used to get the git-authoritative set of top-level dirs
### [CAPABILITY] _COVERAGE_EXEMPT_DIRS excludes tooling dot-dirs and non-navigational dirs from coverage requirement
### [CAPABILITY] _dir_mentioned uses core = name.lstrip(".") + word-boundary regex — dot-dirs (.github) correctly matched
### [CAPABILITY] URL-ish tokens (containing :// or starting //) are never treated as path claims
### [CAPABILITY] Slash-path tokens are path claims only if last segment has a lowercase source-file extension
### [CAPABILITY] Slash-less tokens are path claims only if they are real top-level entries in the clone

### [WIRING] Called by pipeline.run_pipeline() after map_store.save()
### [WIRING] Called by refresh.refresh_on_push() after map re-build
### [WIRING] Imports REQUIRED_MAP_MARKERS, REQUIRED_NAV_MARKERS from symbol_map.py

### [EDGE] Map names a URL like github.com/calcom/cal.com → _looks_like_path: "://" not present, "/" present, last segment "cal.com" → _is_source_file_ext: ext="com" in _DOMAIN_TLDS → NOT a path claim
### [EDGE] Map names Next.js → no "/" → not in top_entries → not a path claim
### [EDGE] Map names packages/lib/x.ts (real file) → path claim; file exists → grounded
### [EDGE] Map names foo/bar.ts (fabricated) → path claim; file does not exist; basename "bar.ts" not in tracked_names → hallucinated → not_ready
### [EDGE] Map names internal/bytesconv.StringToBytes → last segment "StringToBytes" has no extension → _is_source_file_ext returns False → not a path claim (code symbol)
### [EDGE] Map names a real file at a slightly-wrong prefix (lib/x.ts when file is packages/lib/x.ts) → _path_exists_in_clone exact fails; basename "x.ts" in tracked_names → grounded
### [EDGE] Map contains a leading-dot attribute ref (/.group, cli/.group) → empty stem check → not a path claim
### [EDGE] Top-level dir .github not mentioned in map → _COVERAGE_EXEMPT_DIRS includes .github → not required
### [EDGE] Top-level code dir services/ not mentioned → NOT exempt → uncovered → not_ready
### [EDGE] list_tracked_files returns None (no .git) → _uncovered_top_dirs returns empty set (no coverage check possible)
### [EDGE] Map text contains a secret value string → exclusions.secret_values() hit → "secret value leaked into map" reason added
### [EDGE] Map text names a secret path (.env) → leaked_paths non-empty → "secret path leaked into map" reason

### [FAILURE] clone_path does not exist at verify time → "clone path does not exist" reason → not_ready
### [FAILURE] Both REQUIRED_MAP_MARKERS AND REQUIRED_NAV_MARKERS are partially missing → closer shape's missing markers named → not_ready

### [NUANCE] The hallucination check is basename-fallback aware: naming a real file at a wrong dir is NOT a hallucination (agent navigates live)
### [NUANCE] verify does NOT check comprehension prose quality — it only checks file:line claims if present
### [NUANCE] The "no parseable source symbols" special case means repos with zero parseable files (e.g., pure configuration repos) pass verify with an honest stub — they should not block onboarding

---

## 12. pipeline.py — orchestrator

### [CAPABILITY] run_pipeline sequences: mint → clone → map-build → store → verify → ready (5 stages)
### [CAPABILITY] Stage states emitted to readiness_listener: connecting → cloning → indexing → (→ ready on success)
### [CAPABILITY] PipelineResult carries ready, repo, sha, reasons, clone_path, map_text, degraded, states
### [CAPABILITY] listener.emit exceptions are swallowed (BLE001) — a listener blip never fails the pipeline
### [CAPABILITY] store absence (map_store=None) is NOT a readiness failure by itself — map produced and verified without storage (valid for tests)
### [CAPABILITY] minter=None and installation_id=None → clone runs unauthenticated (public repo path / test fixture)
### [CAPABILITY] run_pipeline never raises out — all failures return PipelineResult(ready=False, ...)

### [WIRING] Called by control_plane connect trigger after GitHub App installation
### [WIRING] Receives provider (legacy, retained for signature), map_store, minter, call, oauth_token, sandbox_class

### [EDGE] Listener raises during emit → caught by try/except → states list still updated → pipeline continues
### [EDGE] sha provided → resolved_sha = sha (explicit pin); sha=None → resolved from head_sha(clone_path) or ""
### [EDGE] head_sha returns None (no .git after failed clone) → resolved_sha = ""
### [EDGE] store fault → PipelineResult(ready=False, reasons=["store: ..."]) — verify is NOT run (short-circuit)
### [EDGE] verify_map returns not_ready → "ready" state NOT emitted; reasons propagated
### [EDGE] Both call and oauth_token provided → Part 2 comprehension runs; if comprehension fails gracefully → degraded=True but still verifiable → ready (Part 1 alone)
### [EDGE] map_build.build_understanding_map raises an unhandled exception → NOT caught by pipeline → bubbles up as an unhandled coroutine exception → the caller gets an uncaught exception (RISK: pipeline does not wrap map-build in a try/except)

### [FAILURE] Network down during all 5 stages → auth fails first → not_ready("auth: ...") with states=["connecting"]
### [FAILURE] Clone succeeds but head_sha returns None → resolved_sha="" → store/verify proceed with empty SHA → may conflict with prior SHA-indexed rows

### [NUANCE] No "mapping" state — map-build IS the "indexing" state (deliberate design per SPEC)
### [NUANCE] store fault is an honest not_ready — the map may be excellent but if it can't be stored it can't be used at meeting time; honest rejection is correct

---

## 13. readiness.py — connect-poll surface

### [CAPABILITY] signal_from_result maps PipelineResult → ReadinessSignal with status, states, gaps, sha
### [CAPABILITY] If not_ready with empty reasons → gaps = ["not ready: no reason recorded"] — never an unexplained not_ready
### [CAPABILITY] VALID_STATES frozenset: connecting, cloning, indexing, ready, not_ready

### [WIRING] Consumed by control_plane connect-status poll endpoint to render live progression to the user

### [EDGE] PipelineResult.ready=True → gaps=[] (no gap reasoning when ready)
### [EDGE] PipelineResult.ready=False, reasons=[] → gaps=["not ready: no reason recorded"] — backstop for unexpected cases

### [NUANCE] The poll renders the ORDERED states list — the client can show a progress bar from "connecting" through "indexing" to "ready"
### [NUANCE] sha is included so the UI can confirm which commit was mapped

---

## 14. refresh.py — push-triggered rebuild

### [CAPABILITY] refresh_on_push delta-pulls the existing clone, re-builds Part-1 map (symbol map only — no comprehension), re-stores at new SHA, re-verifies
### [CAPABILITY] Returns not_ready if no existing checkout (push before first connect) — names the gap
### [CAPABILITY] Returns not_ready on auth failure (names "auth: ...")
### [CAPABILITY] Returns not_ready on delta-pull failure (names "delta-pull failed")
### [CAPABILITY] RefreshResult.rebuilt=True when re-build was attempted (even if store/verify fails)
### [CAPABILITY] Never raises out — all failures return RefreshResult(ready=False, ...)

### [WIRING] Called by control_plane GitHub push webhook handler (HMAC-verified upstream)
### [WIRING] Uses build_map (Part 1 only) — NOT build_understanding_map; comprehension is NOT re-run on push refresh (call/token not threaded)
### [WIRING] Re-uses same cloner/exclusions/verify modules as pipeline.py

### [EDGE] push webhook fires before connect completes (checkout not yet present) → returns not_ready("no existing clone to refresh")
### [EDGE] push changes a .env file → scan_after_pull with changed_files includes .env → excluded before map re-build
### [EDGE] New SHA identical to prior SHA (force-push no-op) → stored with same (tenant, repo, sha) → ON CONFLICT DO UPDATE → no duplicate
### [EDGE] map_store=None → store step skipped → verify proceeds → a not_ready from verify without storage is possible
### [EDGE] delta-pull fetch succeeds but fast-forward fails (diverged history) → git update-ref/checkout may fail → head_sha may return old SHA → stored at wrong SHA
### [EDGE] Very rapid successive pushes (two webhooks in flight) → two concurrent refresh_on_push calls for same tenant/repo → race on checkout dir → possible interleaved clone state

### [FAILURE] Store fault on refresh → RefreshResult(ready=False, reasons=["store: ..."], rebuilt=True)
### [FAILURE] Verify fails on refreshed map → RefreshResult(ready=False, reasons=[...], rebuilt=True) — previous meeting with old map unaffected (old SHA row still in Postgres)

### [NUANCE] Refresh uses Part-1 ONLY (no comprehension pass) — the holistic understanding is only updated on a full re-connect (a design trade-off between latency and freshness)
### [NUANCE] The "rebuilt=True" flag lets the caller know re-build was attempted regardless of final readiness verdict

---

## 15. repo_context.py — in-meeting downstream handoff

### [CAPABILITY] RepoContext carries clone_path, map_text (loaded from Postgres), tenant_id
### [CAPABILITY] build_server() returns a McpSdkServerConfig (code_intel MCP server) over the clone, or None if nothing is grounded
### [CAPABILITY] build_repo_context_server() mounts: read, batch_read, grep, glob — all backed by the clone
### [CAPABILITY] Every tool handler returns an error result (is_error: True) on fault — never throws (Hard Rule 6)
### [CAPABILITY] _read_body confines reads to clone.resolve() — a ".." path escape returns "path outside tenant volume" error
### [CAPABILITY] grep uses ripgrep (rg) with -n --no-heading -w flags; excludes secret paths; redacts secret values in context lines
### [CAPABILITY] glob uses rglob over the clone; excludes .git parts and excluded paths; caps at _MAX_GREP_HITS (200)
### [CAPABILITY] batch_read caps at _MAX_BATCH_FILES (20) — prevents unbounded context blowup
### [CAPABILITY] SERVER_NAME = "code_intel" — matches the existing mcp__code_intel__* allowed_tools in wake/workroom

### [WIRING] Called by in_meeting.workroom when mounting the resident understanding at session start
### [WIRING] map_text loaded from map_store.load_latest_map (latest SHA for the tenant/repo)
### [WIRING] clone_path must be tenant-rooted via paths.tenant_repo_dir — prevents cross-tenant reads

### [EDGE] clone does not exist (E2B sandbox — clone is inside the sandbox, not the host) → build_server returns None (has_map alone insufficient without clone)
### [EDGE] clone exists but map_text is None → build_server still tries to build the MCP server (clone exists check only)
### [EDGE] grep called with empty query → early return [] (no subprocess launched for empty query)
### [EDGE] rg binary not on PATH → subprocess.run raises FileNotFoundError → caught by BLE001 → error_result
### [EDGE] Path string passed to read is absolute (starts with /) → resolved to absolute path; confinement check may reject if not under clone.resolve()
### [EDGE] batch_read called with paths as a string (not a list) → _batch converts to [paths] → safe
### [EDGE] batch_read with >20 paths → only first 20 read; truncated=True in result
### [EDGE] Excluded file requested via read → "excluded path" error result
### [EDGE] Non-file path requested (directory) → "not found" error result
### [EDGE] _scanned_exclusions creates a NEW ExclusionManager per call — per-session, not shared

### [FAILURE] build_repo_context_server raises unexpectedly → BLE001 in build_server → returns None (degraded but not crashed)

### [NUANCE] The MCP server name "code_intel" is the SAME name as the live toolbelt — wiring is a substrate swap (clone-backed vs graph-backed), not a name change
### [NUANCE] grep results are limited to _MAX_GREP_HITS (200) — a high-volume symbol may truncate; the agent should grep more specifically if needed
### [NUANCE] The tool is not wired into the meeting/provision path in this module — it IS the generator; the actual mounting happens in in_meeting.workroom

---

## Cross-subsystem integration points

### [INTEGRATION-1] Pipeline → MapStore → Meeting session (the critical data path)
- pipeline.run_pipeline() stores the map_text in repo_maps table at (tenant_id, repo, sha)
- in_meeting.workroom loads via map_store.load_latest_map(tenant_id, repo) — may return a DIFFERENT sha than the meeting was provisioned at if a refresh happened between connect and meeting start
- TEST: after run_pipeline, load_latest_map returns the same text that was stored; after refresh_on_push, load_latest_map returns the refreshed map

### [INTEGRATION-2] verify_map ↔ symbol_map markers (shape contract)
- verify.py imports REQUIRED_MAP_MARKERS and REQUIRED_NAV_MARKERS from symbol_map.py
- If symbol_map.py changes the markers, verify.py must stay in sync — they share the constants
- TEST: a map produced by build_symbol_map() always passes verify_map()'s shape check; a map produced by build_navigation_map() + comprehension always passes the nav-markers check

### [INTEGRATION-3] Comprehension → verify_map (Part 2 text shape meets verify expectations)
- The comprehension-first document has # Navigation map (not # Symbol map) — verify_map must accept REQUIRED_NAV_MARKERS
- TEST: build_understanding_map() with a real comprehension → verify_map() on the result → ready=True

### [INTEGRATION-4] ExclusionManager shared between map-build and verify (secret boundary consistency)
- The SAME ExclusionManager instance (or an equivalent one) must be used across clone → map-build → verify so a secret found post-clone is excluded from the map
- In pipeline.run_pipeline(): em = ExclusionManager(); cloner = Cloner(exclusion_manager=em); ... verify_map(..., exclusions=em) — the same em traverses all stages
- TEST: a .env file in the repo is excluded from the symbol map, from verify's path checks, and does not cause a "secret path leaked into map" failure

### [INTEGRATION-5] Refresh uses Part-1 only; initial pipeline may use Part-1 + Part-2
- A meeting started after a push-refresh gets the Part-1 symbol map (no comprehension), not the richer Part-1+2 doc from the initial connect
- TEST: refresh_on_push produces a map that passes verify; load_latest_map returns the refreshed Part-1 map; the meeting agent has orientation but less qualitative depth

### [INTEGRATION-6] Token redaction across gitio + cloner (PM-CLONE-02 end-to-end)
- Cloner.clone() builds the authenticated URL in memory → passes it to run_git → run_git's interceptor receives REDACTED argv
- TEST: with a fake interceptor, the raw installation token never appears in any recorded argument; the actual git subprocess gets the real URL (not the redacted one)

### [INTEGRATION-7] readiness_listener ↔ connect-status poll (live progression)
- pipeline.run_pipeline() emits connecting → cloning → indexing → (optionally) ready onto the listener
- The connect-status poll client must render these in order; an out-of-order or missing state is a UI failure
- TEST: after run_pipeline, result.states = ["connecting", "cloning", "indexing", "ready"] (or without "ready" on failure)

### [INTEGRATION-8] repo_context SERVER_NAME = "code_intel" ↔ wake/workroom allowed_tools
- If in_meeting changes the allowed_tools prefix or the server name, repo_context.build_repo_context_server must change in sync
- TEST: the server mounted by build_repo_context_server() is named "code_intel"; the tools are named "grep", "read", "batch_read", "glob" — matching mcp__code_intel__{grep,read,batch_read,glob}

### [INTEGRATION-9] Alembic migration 0009_repo_maps ↔ map_store SQL (schema contract)
- map_store.py uses table name repo_maps and columns (tenant_id, repo, sha, map, built_at) — must match the migration exactly
- TEST: after applying migration 0009, save_map and load_map work without SQL errors; downgrade drops the table cleanly

### [INTEGRATION-10] Comprehension sandbox runner ↔ claude-agent-sdk version (pin contract)
- _SDK_PIN = "claude-agent-sdk>=0.2.115" and _MCP_PIN = "mcp==1.28.1" are pinned inside the sandbox
- If the workroom bake target changes these pins, comprehension.py must be updated in sync
- TEST: the runner source installs the pinned SDK; the ClaudeSDKClient import succeeds; the query runs without ImportError

### [INTEGRATION-11] Pipeline's missing try/except around map-build (risk gap)
- build_understanding_map() can raise if an unexpected exception escapes comprehension.build_comprehension() (which itself catches BLE001, but an import error or structural fault could escape)
- If this happens, run_pipeline() raises out → the connect trigger may get an unhandled exception → the user sees no readiness signal and no named reason
- TEST: inject a sandbox_class that raises RuntimeError during sandbox creation — pipeline must return not_ready (currently it does, because build_comprehension catches BLE001 — but confirm this for all exit paths)

### [INTEGRATION-12] Tenant isolation end-to-end (P0 breach prevention)
- tenant_repo_dir(tenant_A, repo) != tenant_repo_dir(tenant_B, repo) for any repo
- load_map(conn, tenant_id=A, repo=repo, sha=sha) cannot return tenant_B's row
- build_repo_context_server(clone_path_A) confines reads to clone_path_A.resolve()
- TEST: two tenants with identically-named repos and identical SHA → their maps are stored and loaded independently; their tool servers cannot cross-read

### [INTEGRATION-13] Verify hallucination check ↔ diverse real-world repos (the repo-diversity stress)
- The cal.com, gin (Go), click (Python) regression suite must all pass verify without false positives (URL domain, code symbol, attribute ref, imprecise internal path)
- TEST: for each known repo type, produce a symbol map via build_symbol_map() and assert verify_map() returns ready=True without any false hallucination reasons

### [INTEGRATION-14] Comprehension's secret scrub ↔ verify's secret check (defence in depth)
- verify_comprehension() calls exclusions.redact() on the prose before returning
- verify_map() checks secret values in the COMBINED understanding document (comprehension + nav map)
- A credential that slips through comprehension's redact() should still be caught by verify_map()
- TEST: plant a hard-coded credential in a source file; run the full pipeline; assert it does not appear in the stored map_text

### [INTEGRATION-15] build_comprehension ↔ sandbox clone path (public vs authenticated clone)
- When repo_url is provided to build_comprehension (via build_understanding_map's repo_url param), the sandbox clones via public URL (no auth token injection)
- A private repo's sandbox clone will fail (no token in the sandbox git clone) — this is an acknowledged gap: comprehension currently only works for public repos unless the sandbox clone uses the same authenticated URL
- TEST: verify that _run_in_sandbox's setup command uses shlex.quote(repo_url) without token injection — and document this as a gap for private-repo comprehension

---
*End of premeeting.md — 15 modules + 15 cross-subsystem integration points.*
