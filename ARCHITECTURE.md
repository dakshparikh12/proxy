# Proxy — Complete Cloud Architecture (onboarding → running at scale)

**Status:** the one-page complete picture (2026-08-06). Unifies `ONBOARDING-INTEGRATION.md` +
`CLOUD-RUNTIME.md`. Grounded in Gallop's proven patterns. **Compute: E2B (locked); GCE-copy-Gallop
is the swappable future option.** Principle: simplest thing that runs everything on the cloud at
scale — no per-customer manual work.

---

## 1. The whole system on one screen

```
   CUSTOMER (admin + team, browser & Slack)
        │  onboard · use console · get meetings
        ▼
 ┌──────────────────────────────────────────────────────────────┐
 │  CONTROL PLANE  — Cloud Run (ONE service, stateless, →0)       │
 │  connect page · console API · webhooks (Recall, GitHub push)   │
 │  meeting orchestrator (claim·heartbeat·admission·reaper)       │
 │  Recall/Cartesia relay · the `to_meeting` surface              │
 │  WorkOS · Composio · Stripe wiring                             │
 │  ── HOLDS ALL CREDENTIALS ──                                   │
 └───┬───────────────────────────────────────┬──────────────────┘
     │ reads/writes (durable truth)           │ spawns 1 per meeting (MANY in parallel)
     ▼                                        ▼
 ┌───────────────────────────┐        ┌───────────────────────────────┐
 │ BRAIN  (per tenant)        │        │ BODY  — E2B sandbox (ephemeral)│
 │ Cloud SQL (structured) +   │◀──────▶│ repo + Claude, booted from the │
 │ GCS (blobs), tenant_id     │ seed / │ per-repo BAKED TEMPLATE.       │
 │ one per company, forever   │  fold  │ Claude runs HERE (local tools, │
 └───────────────────────────┘        │ cached prime). NO credentials. │
                                       └───────────────────────────────┘
 externals: GitHub App · Composio(Slack/Docs/…) · Recall · Cartesia · Anthropic
```

**Read it as:** one Cloud Run control plane (the brain-stem, holds creds, orchestrates) · one
durable **brain** per company (Cloud SQL + GCS) · **many ephemeral bodies** (E2B sandboxes), one
per meeting, spawned in parallel, each running Claude with the repo local, folding results back
into the brain, then reaped.

## 2. How Gallop hosts tenants / provisions / uses the cloud — and our simple version
| Gallop (proven) | Proxy (simple, E2B) |
|---|---|
| **Tenant hosting:** shared GCP project + `tenant_id` for standard customers; a **dedicated per-customer project** (`customers/<name>/platform` via the `customer-platform` Terraform module) only for enterprise | Same: shared multi-tenant now; per-customer GCP project as the enterprise escape hatch |
| **Compute per unit of work:** a **GCE VM** provisioned at runtime (`instances.insert`) from a **Packer golden image**, configured by a startup-script, idle-reaped | A **per-meeting E2B sandbox** booted from a **baked per-repo template** (repo+agent pre-loaded), reaped. Same shape, managed vendor |
| **Cloud usage:** Cloud Run = the app service; **Cloud Build jobs** = CI + migrations; a release-registry promotes images across projects | Cloud Run = control plane; Cloud Build (or a script) = CI + Alembic migrations; `terraform apply` stands up the estate |
| **Orchestration:** DB-state-machine + atomic claim + lease/heartbeat + reaper (no queue) | Same primitives — **already in our code** (atomic claim ✓); add the heartbeat/reaper (§4) |
| **Persistence:** Postgres (sessions/meta) + GCS (blobs) + git-mirror-to-GCS to survive VM restart | Same: Postgres + GCS brain; git-mirror workroom output to GCS |

**Note:** neither Gallop nor Proxy runs a meeting as a "Cloud Run job." Cloud Run hosts the
long-lived control plane; the meeting runs in a sandbox/VM. Cloud Run/Build *jobs* are for CI,
migrations, and template bakes only.

## 3. The complete process, onboarding → running at scale (one flow)
1. **Onboard** (`ONBOARDING-INTEGRATION.md`): admin SSO sign-in → WorkOS Org + **tenant + brain
   provisioned** → connect GitHub App + Slack/etc. (Composio, per-tenant) → Proxy indexes + proves
   comprehension → team gets role-based access.
2. **Pre-meeting** (once/repo, on connect + every push): clone → understanding/`REPO_MAP` → store
   in the brain → **bake the per-repo E2B template**.
3. **Meeting spawn** (per meeting, many in parallel): Recall webhook / calendar → tenant resolved →
   **atomic claim** (one harness per meeting) → boot a sandbox from the template + seed cached
   prime + brain context → **per-meeting heartbeat** ticks the fence.
4. **Meeting run:** Recall bot joins (control-plane relay) → transcript streams into the sandbox's
   cached Claude context → on wake, Claude runs in the sandbox (local tools) → reply streams out →
   Cartesia voice / `to_meeting` chat. World-touching = staged draft (human-gate), executed
   host-side.
5. **Meeting-end:** persist record + git-mirror output to GCS + **fold into the brain** → reap
   sandbox (idle → $0).
6. **Post-meeting** (async): on event/approval → re-boot a sandbox from template+brain → Claude
   does the work → Composio executes + hears replies (triggers→webhook) → brain updated.
7. **Scale:** many sandboxes across tenants + per tenant, bounded by admission control; the
   watchdog reaps orphans; the brain is the per-tenant continuity. Nothing runs when idle.

## 4. Local → cloud: the small details that would block it (don't skip any)
Everything above runs today *locally*; these are the concrete gaps to run it **fully on the cloud
at scale**. Split by who does them.

**A. Founder-gated (only you — creds/billing/deploy):**
- Real vendor keys in **Secret Manager**: Recall, Cartesia, AssemblyAI, E2B, and **swap the Claude
  subscription token → `ANTHROPIC_API_KEY`** (ToS for prod).
- GCP project + **billing linked**; `terraform apply` the estate (Cloud Run · Cloud SQL · GCS ·
  Secret Manager · Artifact Registry).
- Build + push the control-plane image; second apply wiring `PUBLIC_BASE_URL`.
- Register the **Recall status webhook** (per region) + paste the **AssemblyAI BYOK key** into
  Recall.
- **Bake the E2B `proxy-workroom` template** (native `claude` + `mcp` + git) and set it as the
  workroom default; use the **bigger machine** (the OOM fix).

**B. Concurrency hardening (from `CLOUD-RUNTIME.md` §4a — additive, no redesign):**
- **Heartbeat loop** (tick the meeting fence every ~10s) — *do first; unblocks multi-instance.*
- **Watchdog + orphan-sandbox reaper** (wire the reconcile loop; tag sandboxes with
  `{meeting_id, tenant_id}`).
- **Admission control + per-tenant cost cap** before spawn.
- **Provisioning circuit breaker** (3 strikes).
- *(Later, at >1 instance)* de-serialize the webhook drain.

**C. Live-path wiring gaps (found in prior audits — needed for the full product):**
- **Persist the meeting record at end** (today teardown discards it) — the keystone for
  post-meeting + cross-meeting memory.
- Wire the **map-build model provider** (D-032) so connect→index actually builds the brain.
- Bind the **`offer`/staged-draft sink** (host-side approval) for world-touching actions.
- Verify **egress-deny** applied on the live E2B create; wire the **injection guardrail** on the
  seed path.

**D. Onboarding wiring (from `ONBOARDING-INTEGRATION.md` §4):**
- Register the ~4 OAuth apps (GitHub App, Slack, Google, Microsoft) + Composio custom-auth configs.
- WorkOS org/SSO/SCIM/RBAC/FGA/audit wired; Resend sending domain; the connect page + console.

## 5. Why this is simple + scales
- **One control plane** (Cloud Run, scale-to-zero) + **one brain per company** (managed Postgres +
  GCS) + **many disposable bodies** (E2B). No Kubernetes, no queue, no per-tenant servers.
- **Serverless economics:** nothing runs between meetings; a body lives only for its meeting; idle
  ≈ $0.
- **Scales by adding bodies**, bounded by admission control; the brain is cheap and durable; the
  orchestrator (already mostly built) coordinates without a broker.
- **Copied from Gallop** where it's proven; **adapted** only for Proxy's live path (Claude in the
  sandbox + cached prime). Enterprise escape hatch (per-customer project) available when needed.
