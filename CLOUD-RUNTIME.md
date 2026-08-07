# Proxy — Cloud, Runtime & Data

**One of two canonical docs** (the other is `ONBOARDING-INTEGRATION.md`). Self-contained; current as
of 2026-08-06. Covers how Proxy runs on the cloud at scale: the architecture, how the agent runs, how
meetings spawn, how everything is stored, and the honest build list. Grounded in a code-study of
**Gallop** (a sister company running this shape for enterprise customers; reference repo `~/platform`).
Principle: **copy Gallop where it fits, invert two things for the live path, don't overcomplicate.**

---

## 1. The architecture — three planes
```
   CUSTOMER (admin + team; browser & Slack)
        ▼
 ┌──────────────────────────────────────────────────────────────┐
 │  CONTROL PLANE  — Cloud Run (ONE service, stateless, →0)       │
 │  webhooks (Recall, GitHub push) · meeting orchestration        │
 │  Recall/Cartesia relay · the `to_meeting` surface              │
 │  heartbeat · admission · reaper · billing · connect+console API│
 │  ── HOLDS ALL CREDENTIALS ──                                   │
 └───┬───────────────────────────────────────┬──────────────────┘
     │ durable truth                          │ spawns 1 per meeting (MANY in parallel)
     ▼                                        ▼
 ┌───────────────────────────┐        ┌───────────────────────────────┐
 │ BRAIN  (per tenant)        │        │ BODY  — E2B sandbox (ephemeral)│
 │ Cloud SQL (structured) +   │◀──────▶│ repo (from GCS) + Claude,      │
 │ GCS (blobs), tenant_id     │ seed / │ booted from a toolchain        │
 │ one per company, forever   │  fold  │ template. Claude runs HERE.    │
 └───────────────────────────┘        │ NO credentials.                │
                                       └───────────────────────────────┘
 externals: GitHub App · Composio · Recall · Cartesia · Anthropic
```
This shape — one control plane + shared DB with `tenant_id` logical isolation + per-session managed
microVMs — is the **converged 2025–26 pattern** for multi-tenant AI-agent SaaS (cf. AWS Bedrock
AgentCore). We're on-pattern; the risk is execution completeness, not the architecture.

## 2. The instance model — brain (durable) + body (ephemeral)
- **The company's "Proxy" IS its durable brain** (per-tenant Cloud SQL + GCS) — one per company,
  forever. This is what the customer buys.
- **A meeting spawns a body** (E2B sandbox) that reads the brain, does the work, folds results back,
  then is reaped. Compute is disposable; the brain is the product.
- **One brain, MANY concurrent bodies.** Dozens of meetings (same company + across companies) run in
  parallel; each reads the shared brain; writes are appended as events + folded back (never two
  bodies mutating shared state).
- **Compute: E2B — LOCKED.** GCE-VMs-copying-Gallop is the documented future swap behind a provider
  seam (chosen later only to drop the vendor / escape concurrency caps).

## 3. How the agent runs (the two inversions vs Gallop)
Gallop runs Claude host-side and reaches tools remotely (a network hop per tool). Proxy **inverts two
things for the latency-sensitive live meeting:**
1. **Claude runs INSIDE the E2B sandbox**, repo local → code tools (Read/Grep/Bash/Edit) are **local
   built-ins, instant — no per-tool round-trip.**
2. **Prompt-cache the stable prime** (behavioral prime + `REPO_MAP` + meeting info) → each wake pays
   only the fresh transcript delta.
- **Model: Claude Sonnet 5** (accuracy without Opus cost); **we hold the Anthropic key and meter per
  tenant.**
- **Agent loop** = the SDK `query()` agentic loop behind a single "AgentService choke-point" (owns the
  loop, event→delta translation, the injection-guardrail append, tool config, and **abort = barge-in**).
- **Tools:** code = in-sandbox built-ins; the **only MCP surface is `to_meeting`** (say/chat/screen/
  offer). World-touching = staged draft, executed host-side (credential boundary).
- **Wake:** voice + chat (Recall chat events). **Voice out = Recall Output Audio** (base64 MP3 clips
  via Cartesia — the simplest path; Output Media/continuous+screenshare is a later upgrade).

## 4. Template + spawn (kept fresh, pre-warmed)
- **Bake the TOOLCHAIN into ONE shared E2B template** (claude + mcp + git + deps) — this kills the
  real cold-spawn cost (the installs), and rarely changes.
- **Keep the repo as a shallow (`--depth=1`) copy in GCS, refreshed by the PR-push webhook** — a
  *mutable* store, not the immutable template image. Always current by construction; no template
  rebuilds. (Full git history not stored; PR/diff awareness comes from the webhook + GitHub API.)
- **The understanding (`REPO_MAP`) is computed once, persisted in the brain, and re-indexed on push**
  — never recomputed per meeting.
- **At spawn** (pre-warmed on Recall's `bot.joining_call`, ~2 min early): boot from the toolchain
  template → pull the repo from GCS (no GitHub hit) → load the cached understanding/prime → ready.
- Idle ≈ $0 (bodies die at meeting-end; nothing kept alive). Per-repo full-image bake = huge-monorepo
  escape hatch only.

## 5. Concurrency (already built + the 4 fixes)
**Already correct in code (do not rebuild):** a per-meeting `MeetingRuntimeRegistry` + a correct
**atomic claim** (one harness per meeting) + per-meeting isolated task/sandbox/keepwarm/teardown.
**To run dozens across instances — 4 additive fixes (no redesign):**
1. **Tick the meeting heartbeat** (the fence exists but isn't ticked; else a 2nd instance reaps live
   meetings) — *or* pin Cloud Run to 1 instance for now. *Do first.*
2. **Watchdog + orphan-sandbox reaper** (wire the reconcile loop; tag E2B `create` with
   `{meeting_id, tenant_id}`).
3. **Admission control + per-tenant spend cap** before spawn (cost/DoS backstop).
4. **Provisioning circuit breaker** (3 strikes).

## 6. Persistence / data model — what's stored where
**Rule:** the sandbox is a rebuildable cache; **durable truth = Postgres + GCS**, keyed by
`tenant_id`. We store the *minimum* raw code (derived map persists; raw clone is ephemeral/in-GCS).

**Postgres (Cloud SQL) — structured:** `tenants`, `members`, `connections` (per-tenant integration
handles; tokens encrypted/in Composio vault), `repos` (+ `current_sha`, template ref, index status),
`repo_maps` (pointer to the understanding blob), `meetings` (+ completion status), `meeting_events`,
`action_items`, **`memory`** (cross-meeting: confirmed decisions, ownership, work-state), `sessions`,
`operation_runs` (the claim/heartbeat spine), `sandboxes` (reaper bookkeeping), `usage`/`cost`,
`upcoming_meetings`. `tenant_id` on every row (Postgres **RLS to add** — today isolation is
application-level `tenant_id` filtering only, NOT database RLS).

**GCS (object-versioned, per-tenant prefix `gs://…/<tenant_id>/…`):** the understanding/`REPO_MAP`,
the shallow repo copy, full transcripts, staged diffs/artifacts, git-mirrored workroom output.

**E2B template registry:** the shared toolchain template (+ optional per-repo later).

**"Every meeting = a stored data point":** each meeting writes a `meetings` row + transcript +
`meeting_events`/`action_items`, and **folds decisions/ownership into `memory`** — that's how the
brain learns across meetings. (Retrieval starts as simple structured recall; vectors only if needed.)

## 7. The customer lifecycle (nested scopes)
`TENANT → REPOS(1..N) → MEETINGS(0..N) → BODY(1 per meeting, many ‖) → MEMORY(accumulates)`
1. **Onboard** → provision tenant (brain shell + per-tenant email + connections).
2. **Connect repo** → clone → understanding → store → (toolchain template shared; repo copy to GCS).
3. **Push/PR** → re-index changed files → refresh the GCS repo copy + the understanding.
4. **Meeting** → resolve tenant → atomic claim → pre-warm+spawn body → Recall joins → Claude works →
   voice/chat out (world-touching = staged draft).
5. **Meeting-end** → persist record + git-mirror output + **fold into the brain** → reap body.
6. **Post-meeting** (async) → re-spawn body from template+brain → do the work → Composio executes +
   hears replies → brain updated.
7. **Across meetings** → the brain is the continuity, per tenant, forever, learning.

## 8. Multi-tenant scale, isolation, cost
- **N tenants on shared infra:** ONE Cloud Run, ONE Cloud SQL (`tenant_id`; RLS to add), ONE GCS bucket
  (per-tenant prefixes). Compute scales by adding bodies (bounded by admission control); the reaper
  keeps orphans ≈ 0; idle ≈ $0.
- **Isolation:** `tenant_id` everywhere + per-meeting sandbox + per-tenant GCS prefix + a JWT
  host↔sandbox control channel + the credential boundary. **Enterprise escape hatch:** a dedicated
  GCP project per customer (copy Gallop's `customer-platform` module) — only when demanded.
- **Egress:** the sandbox may reach what it needs for real work (package registries, GitHub,
  Anthropic, the host) — a **generous allowlist**, not arbitrary hosts (the sandbox holds private
  code; arbitrary exfil is the one risk; the injection guardrail is the backstop). Tighten to a strict
  allowlist before security-conscious customers.
- **Cost:** per meeting ≈ $0.17–0.53 compute + Claude(Sonnet-5) tokens (dominant, cache-reduced); E2B
  Pro 100 concurrent (→1,100 add-on). The real cost risk is un-reaped/un-capped sandboxes → fix #2/#3.
- **Retention/deletion:** transcript ~90d, `meeting_events` ~12mo, decisions ∞ (per-tenant, capped);
  raw media on Recall (or `retention:null`); offboard = purge Postgres rows + GCS prefix + revoke
  tokens + kill sandboxes.

## 9. The exact cloud footprint
Cloud Run (control plane) · Cloud SQL Postgres (shared, `tenant_id`+RLS, PITR on) · GCS (versioned,
per-tenant prefixes) · Secret Manager · Artifact Registry (control-plane image) · **E2B** (bodies +
toolchain template) · Cloud Scheduler (reaper/stale-sweep) · inbound-email webhook · vendors (Recall,
Cartesia, AssemblyAI, Anthropic). Provisioned by **copy-Gallop Terraform/Packer.** Single region to
start (us-central1); EU/per-customer-project later.

## 10. What we copy from Gallop
The atomic-claim/heartbeat/reaper concurrency spine · Packer/template golden-image pattern (as the
E2B toolchain template) · Postgres(meta)+GCS(blobs) split · **git-mirror-to-GCS** (HEAD-verify +
refuse-to-wipe) · `tenant_id`-everywhere + shared-default + per-customer-project escape hatch · the
`AgentService` choke-point · encrypted per-tenant tokens · the credential boundary · the whole
Terraform estate shape.

## 11. Current state vs. to-build (honest)
**Real today:** the live in-meeting loop; the atomic claim + per-meeting registry (genuinely built);
the durable **code understanding** (`repo_maps`); a coherent Terraform stack; staged-draft path.
**Not yet built (the v1 work):**
- The **durable brain persistence** — `meetings`/`meeting_events`/`memory` + a **meeting-end writer**
  (today teardown discards everything). *The keystone; in v1 because post-meeting depends on it.*
- **Toolchain template bake** + **repo-in-GCS + pull-at-spawn** (today: cold clone + installs, 60–90s).
- **Private-repo clone wired** (thread the GitHub-App token into the sandbox fetch).
- Swap the shared personal Claude token → **per-tenant `ANTHROPIC_API_KEY`** (ToS + isolation).
- The **4 concurrency fixes** (heartbeat, reaper, admission/cap, breaker).
**Founder-gated deploy:** real vendor keys in Secret Manager · GCP billing + `terraform apply` ·
build/push the image + `PUBLIC_BASE_URL` · register the Recall webhook · bake the E2B template.

**Framing (from the red-team):** the architecture is coherent + on-pattern and the concurrency
primitives are genuinely built — this is **finishing a platform on a working in-meeting core, not
hardening a finished system.** The list above is bounded; none of it is a redesign.
