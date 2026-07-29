# Pre-Meeting System — Design (LOCKED)

The pre-meeting system's ONE job: produce a single artifact — a dense, bounded `index.md`
**repo map** — that gives the in-meeting agent the mental model + navigation to reason with good
judgment at low latency. Everything specific is answered by **live search** (ripgrep/read/
find_references) in-meeting; the map orients, the live read grounds every citation (Law 1).

## Decision: one bounded `index.md` + live search (NOT per-area sub-maps)
A per-area sub-map's job — "find + orient to the relevant area" — is what ripgrep already does in
milliseconds. So sub-maps add friction (a decide-which + a load round-trip) for a benefit live
search mostly already gives. The clean split:
- **`index.md`** gives what ripgrep can't cheaply give: the mental model (what the repo is, major
  areas + where they live, entry points, key models, conventions, how pieces relate).
- **Live search** gives the specifics (exact locations, callers, actual code), fast + always current.

Per-area sub-maps / a true structural index are a DOCUMENTED FUTURE LEVER, built ONLY if an
enterprise monorepo (>100k files, where ripgrep slows to 15s+) needs it. Not in v0.

## The map scales its granularity to a token budget
Small repo → full detail. Large repo → a complete NAVIGATION map (every major area, one line each)
with depth left to live search. Always a complete map of the territory, never bloated — because
"context quality, not window capacity, is the binding constraint."

## `index.md` structure (dense, high-signal, bounded)
```
# Repo Map — <repo> @ <sha>  (built <ts>)
## What this is        — 1-2 sentences: product/service + primary stack
## Where things live    — the navigation core: one line per major area/module (scaled to repo)
## Entry points         — servers, routes, CLI, workers (+ file)
## Key models / domain  — core types/tables/entities + where defined
## Conventions          — build, test, lint/style, notable patterns
## Notes                — monorepo / language mix / anything non-obvious
```

## The five steps (`services/premeeting/`)
1. **Connect + Clone** — the customer installs Proxy's **GitHub App** on their repo (enrollment;
   already built: `connect.py` install URL + `installation_id` stored on the `repos` row + the
   HMAC push webhook). To clone a private repo we **mint a short-lived installation token
   DIRECTLY** — sign a JWT with the App private key → `POST /app/installations/<id>/access_tokens`
   → clone `https://x-access-token:<token>@github.com/...` (token 1 hr, scoped, read-only Contents).
   NO Nango in v0 (production Nango is a 5-service platform — 5 Node svcs + PG + Redis + S3 +
   Elasticsearch — to mint one token; its real benefit is multi-provider, which we don't need).
   The mint lives behind one clean seam so adding GitLab/Bitbucket later can swap in a broker.
   The token rides ONLY in the clone URL and MUST be redacted from every recorded `run_git` argv
   (certify: never logged). Bare + blobless(large) + read-only + per-tenant dir + exclusions scan.
   Any language, any size. Infra: tenant disk + Postgres. NO GCS.
2. **Map-build (agentic core)** — a bounded, NATIVE Claude agent loop. ONE agent (not a spawn):
   the map is NAVIGATION, whose raw material is the directory skeleton + a dozen high-yield files,
   which one bounded agent handles at any realistic repo size. A fan-out only buys deep per-area
   sub-maps — the deferred >100k-file lever — so it solves a problem v0 chose not to have.
   - **Context discipline (this is what keeps one agent safe — grounded):** explore the directory
     tree at BOUNDED DEPTH (a `tree -L 2/3`-style skeleton, ~1k tokens here, tens-of-k even for a
     100k-file monorepo) + read HIGH-YIELD files (README, dependency manifests, CI, CONTRIBUTING)
     + sample a few entry points ON DEMAND. **NEVER ingest the full `git ls-files` list** (~10
     tokens/file → it fills the window around ~15-20k files). The agent's context grows only with
     what IT chooses to open, not with repo size — so building the overview holds regardless of scale.
   - read-only tools (read/grep/glob), secret-redacted (exclusions wrap every read), Sonnet seat.
   - budget: capped max_turns + output tokens. On a monster repo the map DEGRADES GRACEFULLY to
     top-level + a "depth via live search" note — never a crash, never a truncated map. `verify`'s
     dirs-covered check catches an incomplete map → honest `not_ready` (never a false `ready`).
   - the model decides what matters — NO per-language parsers, NO hard-coded structure.
   - reuses the Workroom's isolation-triad agent runner + the ClaudeAgentProvider seam; the
     map-build is a read-only "quick" disposition (read+map only, all mutation blocked).
3. **Store** — durable in Postgres (`repo_maps`: tenant, repo, sha, map, built_at). Tenant-isolated,
   readable from ANY instance, survives host recycle. Clone stays a regenerable disk cache; the map
   is the durable derived artifact. (Production/customer-deployable — not host-local only.)
4. **Verify** — deterministic, model-free: non-empty + has the sections + EVERY path it names exists
   in the clone (no hallucination, Law 1) + top-level tracked dirs covered + no secret leaked.
   `ready` ONLY on pass. This is the reimagined readiness gate.
5. **Refresh** — on the GitHub push webhook: delta-pull → re-build → re-store → re-verify.

## In-meeting handoff (the simple wire — the whole point)
The wake prompt loads `index.md` (as a cached system-prompt prefix) for orientation + mounts the
LIVE toolbelt (`grep`, `read`/`batch_read`, `find_references`, `owner`). Map orients; live read
grounds every citation. The in-meeting side just receives one artifact.

## Meets every goal
- FINAL / supports everything: where/who/what/blast-radius = map + live search; heavy/exhaustive =
  workroom. Nothing the old graph did is lost (lookup → live ripgrep; orientation → the map).
- AGENTIC / nothing hard-coded: map built by a Claude loop; in-meeting is model + tools. No keyword
  routing, no parsers, no decision tables.
- SCALABLE / any repo size: bounded map + live search from 10 files to a large repo unchanged;
  monster-monorepo has a clear future lever.
- DOWNSTREAM: orientation → fewer grep hops → lower latency + cost; mental model → better judgment.

## Module layout (`services/premeeting/src/premeeting/`)
- `github_auth.py`— direct installation-token mint (JWT→access_tokens) behind one call_external seam.
- `cloner.py`     — clone + delta-pull to the tenant disk (lean; reused/ported); token→URL, redacted argv.
- `exclusions.py` — secret-path filtering + value redaction (wraps every map-build read).
- `map_build.py`  — the bounded native Claude agent loop → `index.md`.
- `map_store.py`  — write/read the map durably (Postgres `repo_maps`), per tenant/repo/SHA.
- `verify.py`     — the deterministic completeness + no-hallucination + no-leak check.
- `readiness.py`  — the ready signal (clone + map + verify) on the existing connect-status poll.
- `refresh.py`    — push-triggered rebuild.
- `paths.py`      — per-tenant volume rooting (isolation).
- `pipeline.py`   — thin orchestrator: clone → map_build → store → verify → ready.

## Old code to delete (careful, safe order: build-new → wire → delete-old)
Delete the graph pipeline: `graph`, `graph_builder`, `graph_store`, `graph_gc`, `coverage`,
`langs` (tree-sitter), `orm`, `warm_resolver`, `verifier`, `repo_provider` (dead Nango seam),
`meeting` (per-SHA pins), the per-SHA versioning + 3-arm gate in `code_intel/pipeline.py`.
ONE coupling deferred to the in-meeting phase (do NOT break now): Scribe's referent lookup reads
`graph_nodes` — leave that seam until the in-meeting/reading rework.
