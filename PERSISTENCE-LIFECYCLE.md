# Proxy — Persistence, Data Model & Customer Lifecycle

**Status:** the durable-substrate + lifecycle spec (2026-08-06). Companion to `ARCHITECTURE.md`.
Defines *what is stored where*, *how a meeting becomes a durable data point*, the *customer
lifecycle* (tenant → repos → meetings → memory), and the *exact cloud requirements* that fall out.
Grounded in Gallop's Postgres+GCS+git-mirror pattern. Simple + scalable; E2B for bodies.

---

## 1. Email routing (per-tenant address on one domain)
- One domain `useproxy.co`, **catch-all inbound → one webhook**.
- **Each tenant = a unique address** (`acme@useproxy.co`) = the routing key (local-part → tenant).
  Bot display name stays "Proxy". Customer adds *their* address to invites; Proxy sends *from* it.
- Inbound via Cloudflare Email Routing / Postmark inbound / SES inbound → webhook → parse recipient
  → tenant → parse `.ics` → Recall join. Outbound via Resend/Postmark from the per-tenant address.
- Enterprise tier: calendar OAuth instead (no shared inbox).

## 2. The durable data model — what's stored where

**Rule (from Gallop + our constitution):** the sandbox is a rebuildable cache; **durable truth =
Postgres + GCS**, everything keyed by `tenant_id`. Marked H (exists in code) / B (to build).

### Postgres (Cloud SQL) — structured, transactional, small
| Table | Holds | H/B |
|---|---|---|
| `tenants` | company id, name, verified domain, **per-tenant email address**, plan | H |
| `members` / `roles` | who's in the org + role (or via WorkOS) | B/rent |
| `connections` | per-tenant integration handles (provider, `connected_account_id`, health); tokens encrypted / in Composio's vault | B |
| `repos` | per-tenant repo, provider, install id, `current_sha`, **`template_id`**, index status | H (partial) |
| `repo_maps` | pointer to the understanding blob in GCS, `sha`, built_at (the brain's code layer) | H |
| `meetings` | id, tenant_id, repo_id(s), start/end, attendees, status, **completion (complete/partial/failed)** | B (orphan `mark_ended` exists) |
| `meeting_events` | ordered transcript/decision events (or a pointer to the GCS transcript) | B |
| `action_items` | meeting_id, owner (or UNRESOLVED), type, status, approval state | B |
| `memory` | cross-meeting: confirmed decisions, ownership, "state of the work" (structured recall) | B |
| `operation_runs` | the claim/heartbeat/session state (the concurrency spine) | H |
| `sandboxes` | per-meeting sandbox id + `expires_at` (reaper bookkeeping) | B |
| `usage` / `cost` | per-meeting/tenant metering (signal already flows via `call_external`) | B |
| `audit` | every Proxy action + admin change (or via WorkOS Audit Logs) | rent |

### GCS (object-versioned bucket) — blobs, per-tenant prefix `gs://…/<tenant_id>/…`
- the understanding / `REPO_MAP` docs (the brain's code knowledge)
- full transcripts
- staged diffs / patches / artifacts (the human-approval outputs)
- git-mirror of workroom output (survives sandbox teardown — Gallop's rsync+HEAD-verify+no-wipe)

### The template registry (E2B)
- the **baked per-repo template** (repo + agent pre-loaded), keyed by `repo + sha`; rebuilt on
  signed push. This is the "golden image" — E2B's equivalent of Gallop's Packer image.

**"Every meeting = a stored data point":** each meeting writes a `meetings` row + its transcript
(GCS) + `meeting_events`/`action_items`, and **folds decisions/ownership/work-state into `memory`**.
Nothing important lives only in the sandbox; the meeting is durably captured as structured rows +
blobs, and it *enriches the brain* — that's how Proxy learns across meetings.

## 3. The customer lifecycle (generalized — nested scopes)

```
TENANT (company)                         ← created at onboarding; owns brain + email + connections
  ├─ REPOS (1..N)                         ← each: clone → understanding → bake template → re-index on push
  ├─ MEETINGS (0..N, ongoing)             ← each: resolve tenant(+repo) → claim → spawn body → run → fold
  │     └─ BODY (1 per meeting, many ‖)   ← ephemeral E2B sandbox from template + brain; reaped after
  └─ MEMORY (accumulates)                 ← decisions/ownership/work-state grow over the tenant's life
```

**Lifecycle events (the same for every customer):**
1. **Onboard** → provision tenant: brain shell (Postgres rows + GCS prefix) + per-tenant email
   address + connections. *(Gallop: `provisionTenant`/`ensure_tenant`.)*
2. **Connect a repo** → clone → build understanding → store (`repo_maps` + GCS) → **bake template**.
3. **Push / PR** → re-index changed files → update understanding → rebake template. *(continuous learning)*
4. **Meeting starts** → resolve tenant (email address / calendar / webhook) → **atomic claim** →
   spawn body from template+brain → **heartbeat** the fence.
5. **Meeting runs** → transcript in → Claude in sandbox → reply out → world-touching = staged draft.
6. **Meeting ends** → persist record (`meetings`+`meeting_events`+transcript) → git-mirror output →
   **fold into `memory` + `action_items`** → reap body.
7. **Post-meeting** (async) → re-spawn body from template+brain → act (Composio) → hear replies →
   update memory.
8. **Member joins/leaves** → SCIM updates access (WorkOS).
9. **Offboard** → purge: delete tenant's Postgres rows + GCS prefix + revoke tokens + kill
   sandboxes + confirm.

## 4. Consolidating to MANY customers (how it scales)
- **N tenants on shared infra:** ONE Cloud Run control plane, ONE Cloud SQL (rows partitioned by
  `tenant_id` + RLS), ONE GCS bucket (per-tenant prefixes), tokens per tenant. Isolation =
  `tenant_id` everywhere + per-meeting sandbox + per-tenant GCS prefix.
- **Compute scales by adding bodies** — N concurrent meetings = N sandboxes (E2B cap 100→1,100),
  bounded by **admission control**; the **reaper** keeps orphans ≈ 0; idle ≈ $0.
- **Storage scales cheaply** — Postgres grows a little per meeting (structured); GCS grows with
  transcripts/artifacts (cheap blobs); retention policies (below) cap it.
- **Enterprise escape hatch** — a dedicated GCP project per customer (Gallop's `customer-platform`)
  when a big/regulated customer demands isolation; everyone else shared.

## 5. Retention / TTL / deletion (per-tenant, capped)
| Data | Default | Where |
|---|---|---|
| Raw audio | not stored | — |
| Transcript | 90 days (configurable) | GCS (lifecycle rule) |
| `meeting_events` | 12 months | Postgres |
| Understanding / `REPO_MAP` | until SHA superseded | GCS |
| Decisions / memory | indefinite | Postgres |
| Staged diffs / artifacts | 90 days after merge/discard | GCS |
| Audit | 24 months | WorkOS / Postgres |
| Sandboxes | reaped at meeting-end / short TTL | E2B (+ `sandboxes` table) |

Deletion (offboard) = delete tenant's Postgres rows + GCS prefix + revoke tokens + kill sandboxes
+ report. (CMEK per-tenant later for "crypto-shred on delete".)

## 6. The exact cloud requirements (derived from the lifecycle)
Everything the lifecycle needs, and nothing it doesn't:
- **Cloud Run** — the control plane (webhooks, orchestrator, relay, console API, the reaper loop).
- **Cloud SQL Postgres** (one, small, PITR on) — the structured brain + operational state.
- **GCS bucket** (one, object-versioned, per-tenant prefixes, lifecycle rules) — blobs.
- **Secret Manager** — all credentials.
- **Artifact Registry** — the control-plane image.
- **E2B** — per-meeting bodies + the baked per-repo templates (E2B template registry).
- **Cloud Scheduler** (or an in-process loop) — the reaper/reconcile + stale-sweep cron.
- **Inbound email** (Cloudflare Email Routing / Postmark inbound) → webhook.
- **Vendors:** Recall · Cartesia · AssemblyAI · Anthropic. **SaaS:** WorkOS · Composio · Resend ·
  Stripe.
- **(Enterprise)** a per-customer GCP project via the Terraform `customer-platform` module.

That's the whole cloud footprint — one control plane, one DB, one bucket, managed sandboxes, a
cron, an inbox. Simple, and it carries any number of tenants/meetings.

## 7. What we copy from Gallop
Postgres(meta)+GCS(blobs) split · git-mirror-to-GCS with HEAD-verify + refuse-to-wipe guards ·
`tenant_id`-everywhere + per-tenant GCS prefix · shared-default + per-customer-project escape hatch
· the atomic-claim/heartbeat/reaper concurrency spine · `provisionTenant`-style onboarding. We add:
the per-tenant email routing, the `memory` (cross-meeting) layer, and the baked-template registry
keyed by repo+sha.
