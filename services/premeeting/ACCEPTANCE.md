# Pre-Meeting System — Acceptance Criteria (specific + testable)

Each criterion: **behavior · oracle · threshold · evidence**. Tier: [unit] fake-at-model-seam OK ·
[real-infra] real Postgres/git · [eval] real-model (BLOCKED-on-credits, D-032 — state, never fake).
A criterion is GREEN only after its test RAN and output was shown.

## AUTH — installation-token mint (`github_auth.py`)
- **PM-AUTH-01** [unit] Mint signs a JWT with the App private key and calls
  `POST /app/installations/<id>/access_tokens` **through `libs/http.call_external`** (no raw client).
  Oracle: a stub `call_external` records exactly one POST to that path with a Bearer JWT. Threshold: exact.
- **PM-AUTH-02** [unit] The minted token is NEVER cached on the instance and NEVER logged.
  Oracle: two mints → two distinct upstream calls (no reuse); a log-capturing handler shows zero
  occurrences of the token string. Threshold: 0 leaks.
- **PM-AUTH-03** [unit] A mint failure (non-2xx / network) raises a typed error the pipeline turns into
  an honest `not_ready` reason — never a silent success. Oracle: forced 401 → `not_ready("auth: ...")`.

## CLONE — safe clone (`cloner.py`)
- **PM-CLONE-01** [real-infra] Private-repo clone uses `https://x-access-token:<token>@github.com/...`.
  Oracle: the git remote URL passed to `run_git` carries the token. Threshold: present.
- **PM-CLONE-02** [unit] The token is REDACTED from every recorded `run_git` argv / any log line.
  Oracle: grep the interceptor's recorded argv + logs for the token → 0 hits. Threshold: 0.
- **PM-CLONE-03** [real-infra] Clone is read-only, bare, per-tenant-dir, and blobless above the file
  threshold. Oracle: no push/ref-write; `--bare` present; `--filter=blob:none` iff > threshold; path
  under `tenant_repo_dir`. Threshold: all true.
- **PM-CLONE-04** [real-infra] `exclusions.scan_after_clone` runs so secret files are stripped before
  any read. Oracle: a planted `.env`/`id_rsa` in a fixture repo is absent/redacted post-clone. Threshold: 0 secrets readable.
- **PM-CLONE-05** [real-infra] Delta-pull on refresh updates an existing clone (no full re-clone).
  Oracle: second sync issues `fetch`, not `clone`. Threshold: exact.

## MAP-BUILD — the bounded one-agent loop (`map_build.py`)
- **PM-MAP-01** [unit] Produces an `index.md` containing ALL required sections: What this is / Where
  things live / Entry points / Key models / Conventions / Notes. Oracle: section headers present. Threshold: 6/6.
- **PM-MAP-02** [unit] The agent NEVER ingests the full file list: its tool transcript contains a
  bounded-depth directory listing + high-yield reads, and the raw `git ls-files` full dump is never
  fed into context. Oracle: assert no single tool result > N lines of pure file paths; skeleton depth ≤ 3.
  Threshold: full-list ingestion = 0.
- **PM-MAP-03** [unit] High-yield files are read in a BATCH (one `batch_read`), not one-by-one.
  Oracle: the transcript shows a single batched read of README/manifest/CI. Threshold: ≥ present.
- **PM-MAP-04** [unit] Read-only "quick" disposition — every mutation tool is blocked (isolation triad).
  Oracle: a write attempt in the fake transcript is refused. Threshold: 0 mutations reach disk.
- **PM-MAP-05** [unit] Budget backstop: on a synthetic huge repo the loop STOPS at `max_turns`/token cap
  and emits a top-level map + a "depth via live search" note — never hangs, never a truncated fragment.
  Oracle: bounded run count; note present. Threshold: ≤ cap.
- **PM-MAP-06** [eval] **BLOCKED-on-credits** — real Claude builds a map on ≥5 diverse real repos; a
  deepeval battery scores navigation accuracy (named paths resolve; major areas covered) ≥ threshold.
  State as blocked; do NOT mark green without a funded key.

## MAP-STORE — durable, tenant-isolated (`map_store.py`)
- **PM-STORE-01** [real-infra] `save_map` then `load_map(tenant, repo, sha)` round-trips the exact bytes
  from Postgres `repo_maps`. Oracle: byte-equal. Threshold: exact.
- **PM-STORE-02** [real-infra] Tenant isolation: `load_map` for tenant B never returns tenant A's map,
  even for the same repo/sha. Oracle: cross-tenant read → None/deny. Threshold: 0 cross-tenant reads.
- **PM-STORE-03** [real-infra] Survives host recycle / is readable from any instance (durable, not
  in-process). Oracle: a fresh store object reads a prior-written map. Threshold: present.
- **PM-STORE-04** [real-infra] The migration authoring `repo_maps` exists in `./migrations/versions`,
  upgrades and downgrades cleanly on a scratch DB. Oracle: `alembic upgrade head` + `downgrade -1` clean.
  (Applying to PROD is human-gated — NOT part of this gate.)

## VERIFY — deterministic completeness + no-hallucination + no-leak (`verify.py`)
- **PM-VERIFY-01** [unit] Every file/dir path NAMED in `index.md` EXISTS in the clone. Oracle: a map with
  a fabricated path fails; a faithful map passes. Threshold: 0 hallucinated paths in a passing map.
- **PM-VERIFY-02** [unit] Every top-level tracked directory is covered by the map. Oracle: an omitted
  top-level dir → fail with the dir named. Threshold: 100% top-level coverage.
- **PM-VERIFY-03** [unit] No secret value/secret-path leaks into the map. Oracle: a map echoing an
  excluded secret fails. Threshold: 0 leaks.
- **PM-VERIFY-04** [unit] `ready` is emitted ONLY on full pass; any failure yields `not_ready` naming the
  gap (Law 1/2). Oracle: each failure mode → a specific named reason. Threshold: no silent pass.

## READINESS + REFRESH (`readiness.py`, `refresh.py`, `pipeline.py`)
- **PM-READY-01** [real-infra] The connect-status poll reports connecting → cloning → indexing → ready
  driven by the real pipeline; terminal `ready` carries the real verify result. Oracle: staged states
  observed; no faked number. Threshold: real values.
- **PM-REFRESH-01** [real-infra] A push webhook (HMAC-verified) drives delta-pull → rebuild → re-store →
  re-verify for THAT repo. Oracle: a forged/unsigned delivery is rejected; a signed one rebuilds. Threshold: exact.

## DOWNSTREAM — helps everything (`repo_context.py` + wiring)
- **PM-DOWN-01** [unit] `load_map` + `RepoContext` are consumed by the wake turn: the map is mounted as
  a prompt prefix and the live toolbelt (grep/read/glob/batch_read) is advertised. Oracle: the mounted
  context contains the map text + the tools. Threshold: both present.
- **PM-DOWN-02** [unit] The WORKROOM code-task agent receives the SAME map as orientation + the same
  toolbelt on the clone. Oracle: `_resolve_code_intel_server` returns a context carrying the map.
  Threshold: present (this is the "help the workroom" requirement).
- **PM-DOWN-03** [unit] An unindexed repo (no map yet) degrades gracefully: consumers mount NO context
  and Proxy stays functional (fail-closed to None). Oracle: None context → no crash. Threshold: no crash.

## ISOLATION + BUILD HEALTH (cross-cutting)
- **PM-ISO-01** [unit] No cross-tenant read anywhere in the pipeline (paths + store enforce tenant_id).
  Oracle: a tenant-A pipeline can't read tenant-B's clone/map. Threshold: 0.
- **PM-BUILD-01** [real-infra] `bash build/gates/signoff.sh` is GREEN: ruff + mypy --strict + bandit +
  naming lint (no internal names in user-visible strings) + contracts registry closed. Threshold: exit 0.
- **PM-BUILD-02** [real-infra] After deletion of the graph-build modules the workspace still imports and
  `signoff.sh` stays green; the Scribe `graph_nodes` seam is untouched. Threshold: exit 0 + seam intact.
