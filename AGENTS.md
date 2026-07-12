# AGENTS.md — Proxy Constitution (read FIRST, every build)
Shared method, invariants, stack, types. Component docs live in `/components/<id>.md`; build process in `docs/BUILD-SYSTEM.md`. A component doc never repeats this file.

## What Proxy is
An AI participant that joins a technical company's meetings already knowing that company's software (learned before the meeting from code + docs + tickets + infra). In-meeting it checks claims against what's true in those systems, does asked tasks and proves them, and speaks up unprompted — cited or silent; any real-world change is a draft a human approves. Built bottom-up: 1.1 Ingest → 1.2 Graph → 1.3 Carve → 1.4 Experts → 1.5 Maintain → 02–06.

## The build method (loop engineering)
Each component is one doc built by one loop with TWO arbiters: the code arbiter (`verify.sh` pytest, per pass) and the real-data eval arbiter (`eval-gate`, per section). Done = the product is provably correct on real data. Acceptance criteria are two-tier and arbiter-tagged: **[pytest]** technical properties, **[eval]** product capabilities scored on real repos vs golden keys at thresholds. Criteria are precise-and-general (binary/measurable AND hold for any estate; per-vertical examples live in fixtures). Never weaken a criterion to pass. Use fresh-context subagents to verify (the author over-reports its own correctness).

## Stack (pin exact versions as the build begins)
Python 3.12 (pipelines) / TypeScript (MCP surfaces) · Postgres 16 + pgvector behind a Store adapter · Claude Agent SDK · E2B (sandbox) · LiteLLM (model gateway) · Langfuse (tracing) · one MCP gateway (tool-search + allow-list + injection scan) · all models zero-retention · pytest + testcontainers · ruff + black + mypy --strict · DeepEval/Braintrust (eval). Layout: `src/<component>/`, `tests/`, `conformance/`, `eval/`.

## Product invariants (true in EVERY component — a violation is a build failure)
1 Cited-or-abstain 2 Lossless-or-honest 3 Zero-copy 4 Permission-at-read 5 Truth-is-live 6 Staged-drafts (world-changes need human approval) 7 Freshness-gated caching (never cache verify/operate) 8 Accelerate-never-gate (uncovered → fall back, never fail) 9 Tenant isolation 10 Self-host credentials 11 Vertical/size-agnostic (zero industry code).

## Shared types (defined once; components reference, never redefine)
Artifact{ id; tenant_id; kind; content; citation{source_uri;locator}; acl_ref; provenance{fetched_at;extractor;confidence;secrets_redacted}; raw_ref|null }
PermissionObject{ id; tenant_id; source_object_uri; allowed_principals[]; acl_version; synced_at }  ·  Principal{ id; tenant_id; type; members? }
Unknown{ tenant_id; description; why_matters; likely_owner; resolver; re_verify_at }
Node{ id; tenant_id; kind; canonical_name; aliases[]; page_ref|null; check_pointer|null; capability_refs|null; freshness{type;state;verified_at}; acl_ref; resolution }
Edge{ id; tenant_id; type; src; dst; confidence:"EXTRACTED"|"INFERRED"; citation; verified_at }
Downstream contract (1.2 serves; 02–06 consume): resolve_entity · relate · explain · verify · operate · coverage

## Definition of Done (every component)
All [pytest] pass · all [eval] clear threshold on every real estate · adversarial clean · ruff/mypy/bandit clean · no invariant-violating path (test-proven) · Store adapter is the only DB access · evidence folder committed · both founders signed off. Done means the product is proven on real data — not that the code compiles.

## Component map & order
1.1 (—) → 1.2 (1.1) → 1.3 (1.2) → 1.4 (1.2,1.3) → 1.5 (1.1–1.4) → 02–06 (1.x). Build in dependency order.
