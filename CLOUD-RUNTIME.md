# Proxy — Cloud & Runtime (the technical backbone)

**Status:** designed (2026-08-06), grounded in a code-study of Gallop's proven Claude runtime +
infra. Read with `ONBOARDING-INTEGRATION.md`. Principle: **copy Gallop where it fits, invert two
things for the live/latency path, don't overcomplicate.**

---

## 1. The three planes
| Plane | Where | What it is | Holds |
|---|---|---|---|
| **Control plane** | **Cloud Run** (stateless, scale-to-zero) | webhooks (Recall, GitHub push), meeting orchestration, the Recall/Cartesia relay, the `to_meeting` surface, admission/cost/reaper, WorkOS/Composio/Stripe, connect page + console API | **all credentials**; DB is truth, no authoritative state in memory |
| **Durable substrate** | **Cloud SQL + GCS** (per-tenant) | the **brain's home** — Postgres (structured) + GCS (blobs), keyed by `tenant_id` | the durable source of truth |
| **Per-meeting sandbox** | **E2B microVM** (ephemeral; behind a provider seam) | the **body** — booted from a per-repo baked template, runs Claude *inside* it | the repo + Claude; **no credentials** |

## 2. The Proxy instance = brain (durable) + body (ephemeral)
- **The company's "Proxy instance" IS its durable brain** (per-tenant Postgres + GCS) — one per
  company, persistent forever.
- **A meeting spawns a body** (E2B sandbox) rehydrated from the brain + baked template → killed/
  reaped after. Compute is disposable; the brain is the product.
- **One brain per company; MANY concurrent bodies.** Dozens of meetings — same company and across
  companies — run in parallel, each a fresh sandbox reading the one shared brain; writes are
  appended as events and folded back (never two bodies mutating shared state).
- **Compute decision (LOCKED 2026-08-06): E2B now.** It's already wired and is the fastest path to
  ship; we use it exactly as currently integrated. **GCE-VMs-copying-Gallop is the documented
  future option behind the provider seam** — chosen later if we want to drop the vendor, escape
  E2B's concurrency caps, or need bigger machines. Not now.

## 3. How Claude runs — the agent loop + the two inversions
Gallop runs Claude **host-side on Cloud Run** and reaches tools **remotely on the VM** (a network
hop per tool). Proxy **inverts two things** for the live path:

1. **Claude runs INSIDE the E2B sandbox**, repo local → code tools (Read/Grep/Bash/Edit) are
   **local built-ins, free and instant — no per-tool network round-trip.** Big latency win.
2. **Prompt caching on the stable prime** (`CLAUDE.md` prime + `REPO_MAP` + `MEETING_INFO` as
   cached blocks). Gallop has none; for Proxy each wake pays **only the fresh transcript delta** —
   the decisive latency lever.

**Copied from Gallop:** the SDK `query()` agentic loop behind a single **AgentService choke-point**
(owns the loop, event→delta translation, the guardrail central-append, tool config, and
**abort = barge-in**), `strictMcpConfig`/`settingSources:[]` isolation, "tool handlers return
errors, never throw."

**Tools:** code tools = in-sandbox built-ins. The **only MCP surface is `to_meeting`**
(say/chat/dm/show/offer/mute), carried by the host relay. World-touching actions = **staged drafts
behind a human click**, executed host-side (GitHub App PR, Composio message) — never from the
sandbox (credential boundary).

## 4. Instances, spawning, concurrency, lifecycle
- **Spawn:** meeting-start (calendar/invite → tenant resolved) → control plane boots a sandbox from
  that tenant's **per-repo baked template** (no re-clone), seeds cached prime + brain context,
  starts the Claude session. Warmed just ahead of the meeting for latency.
- **Many at once:** many sandboxes across tenants *and* for one tenant — **all read the one shared
  brain; writes appended as events + folded back** (never two bodies mutating shared state).
  Orchestrated by a DB-state-machine + atomic claim (copy Gallop).
- **Lifecycle/cost:** copy Gallop's **5-min keepalive + 3-strike circuit breaker + idle-reaper +
  hard-delete backstop**. Sandbox dies at meeting-end → idle ≈ $0. **We own the reaper** (E2B
  snapshots have no TTL). Concurrency bounded by E2B (100 → 1,100 add-on); swappable via the
  provider seam (E2B → Fly/Modal/GCE-Gallop-style).

## 4a. Concurrency — already built vs. the 4 fixes to run dozens (verified in code)
**Good news: Proxy is already concurrent-by-design, not single-meeting.** Already correct — do
NOT rebuild:
- per-meeting **`MeetingRuntimeRegistry`** (dict keyed by `meeting_id`); each meeting = its own
  runtime + background asyncio task + E2B sandbox + keep-warm + teardown, isolated, no
  cross-meeting global-state collisions;
- a correct **Gallop-style atomic claim** (`INSERT … ON CONFLICT … RETURNING` on a partial-unique
  index) = exactly one harness per meeting; idempotent redelivery; pause/resume fast-join.

**Not yet safe to run dozens *across instances* — 4 additive fixes (no redesign):**
1. **Hook the meeting heartbeat (FIRST — unblocks multi-instance).** The fencing token
   (`OperationHandle`) exists but **nothing ticks it**, so every live meeting's row looks stale
   after 40s → a second instance's stale-sweep can reap live meetings and double-provision a
   sandbox. Fix: per-meeting heartbeat loop (reuse the existing `_heartbeat_loop`) ticking every
   ~10s, self-terminating on `is_owner=False`. **This is the precondition for >1 instance.**
2. **Periodic reclaim + orphan-sandbox reaper (watchdog).** Schedule the reconcile loop (~300s);
   tag E2B `create` with `metadata={meeting_id, tenant_id}` and reap orphans via
   `AsyncSandbox.list()`. (A reaper exists in code but is unwired + off the live path.)
3. **Admission control before spawn.** Per-tenant + global concurrent-meeting cap (+ the cost
   signal already flowing through `call_external`) → honest-degrade over the cap.
4. **Provisioning circuit breaker (3 strikes)** per tenant/repo → cooldown instead of hammering E2B.
5. *(Throughput, later, only at >1 instance)* de-serialize the webhook drain (cache
   bot_id→meeting; fan out).

**"One brain, many concurrent bodies" is achievable with the current architecture + these four
additive fixes — hardening, not a redesign.**

## 5. Storage + cross-meeting memory + continuous learning
- **At meeting-end:** persist the meeting record (transcript + Proxy's work + intents) to
  Postgres+GCS; **git-mirror code output to GCS** (copy Gallop's rsync + HEAD-verify + refuse-to-
  wipe guards); fold decisions/action-items into the brain; kill the sandbox.
- **Data split:** Postgres = structured (tenants, meetings, action items, sessions, brain metadata,
  snapshot/instance bookkeeping, cost); GCS = blobs (transcripts, `REPO_MAP`/understanding, staged
  diffs, artifacts), per-tenant prefix. **Sandbox = rebuildable cache, never source of truth.**
- **Cross-meeting memory:** the brain + a **Postgres transcript mirror** (copy Gallop's
  `PostgresSessionStore`) → Proxy remembers across meetings.
- **Continuous learning:** PR/push → webhook re-indexes changed files → updates the brain +
  rebakes the template.

## 6. End-to-end trace (one meeting)
1. **Pre-meeting** (once/repo): clone → understanding/`REPO_MAP` → store in brain → **bake template**.
2. **Start:** tenant resolved → sandbox spawned/warmed from template + cached prime + brain context.
3. **During:** Recall bot joins (host-side); transcript streams into the sandbox's Claude context
   (cached — only the delta is fresh).
4. **Wake:** Claude `query()` runs *in the sandbox* → local built-in code tools → streams reply
   deltas out → host relay → **Cartesia TTS (voice, first-clause TTFT)** / `to_meeting` chat.
5. **Action:** staged draft → human approves → executed host-side (GitHub App / Composio).
6. **End:** persist record + git-mirror output + fold into brain → reap sandbox.
7. **Post-meeting:** on event/approval → rebuild sandbox from brain+template → Claude follow-up →
   Composio executes + hears replies (triggers→webhook) → brain updated.

## 7. Copy-from-Gallop vs adapt
- **Copy verbatim:** AgentService choke-point · guardrail central-append · `strictMcpConfig`/
  `settingSources:[]` · credential boundary · delta streaming · Postgres transcript mirror ·
  git-to-GCS mirror (+guards) · keepalive/circuit-breaker/abort · the whole Terraform/Packer/
  provisioning/reaper infra pattern · per-tenant-project escape hatch.
- **Invert/adapt:** Claude **in** the sandbox (local tools) · **prompt caching** on the prime ·
  **voice-first** streaming at clause boundaries · MCP only for the meeting surface · the per-meeting
  sandbox is the session boundary (no Cloud-Run scale-out disk-locality problem *during* a meeting).

## 8. Hosting, scale, cost, the seam
- **Hosting (one line):** Cloud Run (control plane) + Cloud SQL + GCS (brain) + E2B (bodies) +
  Recall/Cartesia (transport, host-side), provisioned by **copy-Gallop Terraform/Packer**. Everything
  on the cloud; nothing per-customer manual.
- **Per-tenant isolation:** `tenant_id` everywhere + one sandbox per meeting + per-tenant GCS prefix;
  per-customer GCP project as the enterprise escape hatch (copy Gallop's `customer-platform` module).
- **Provider seam:** compute abstracted (like `libs/http`) so E2B → Fly/Modal/GCE swaps by config;
  re-evaluate at sustained ~100+ concurrent.
- **Cost:** per meeting ≈ $0.17–0.53 sandbox + Claude tokens (dominant, cache-reduced); idle ≈ $0.
  The orchestrator (spawn/warm/reap/audit) is the real engineering, per Gallop (~¾ of their effort).

## 9. What we build (the moat)
The **brain** (per-company knowledge), the **agent loop** (Claude in-sandbox), the **live-meeting
core** (the host relay holding Recall/Cartesia + the `to_meeting` surface), the **GitHub clone +
repo-map**, the **human-gate**, and the **orchestrator** (spawn/warm/reap/cost). Everything else —
rent (E2B, Recall, Cartesia, WorkOS, Composio) or copy (Gallop's infra patterns).
