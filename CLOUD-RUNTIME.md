# Proxy — Track A: Cloud Runtime & Instance

> **This is the kickoff brief for the Track-A design deep-dive.** It is self-contained: the mission,
> where we are, where we need to be, the frozen seam we share with Track B, what Track B is doing, and
> the buildable path. Sibling doc: `ONBOARDING-INTEGRATION.md` (Track B). Master index: `ROADMAP.md`.
> Product source of truth: `SPEC.md`. Current as of 2026-08-07.

## Mission (applies to both tracks)
Proxy is a **serverless, multi-tenant SaaS on GCP** that **works for any customer size**, **runs fully
on the cloud** (nothing on a laptop, nothing manual per meeting), and is **secure by construction**.
**Gallop (`/Users/daksh/platform`) is the source of reference — copy its proven mechanics; diverge only
where our ICP or our laws demand it, and say so.** The bar is **the simplest, easiest path that still
scales to the best end-state.** **V1 targets startups** (don't overcomplicate — shared infra, boring
proven patterns); the architecture must extend cleanly to enterprise-grade isolation later without a
rewrite.

## What Track A owns (and what it does NOT)
**Owns:** everything that makes a meeting run on the cloud — the control plane, the durable per-tenant
brain, the ephemeral bodies, the in-meeting interaction loop (say/chat/offer — already built), spawn,
persistence, ops-safety, hosting, and the deploy pipeline. **Does NOT own:** how a customer signs up,
connects tools, or is provisioned — Track B writes the tenant/connection/repo rows this track reads
(see **The Seam**). Track A is fully buildable and testable on **seeded rows** — it never needs the
onboarding UI to exist.

---

## 1. Where we are (Track A "you are here")
**Real and proven today — the hard part is built:**
- The **live in-meeting reactive loop** (the core product) + the **interaction surface** (`to_meeting`:
  say/chat/dm/screen/offer/mute), grounded + verified on real data.
- **Concurrency primitives:** atomic claim + per-meeting `MeetingRuntimeRegistry` — confirmed to BE
  Gallop's proven Postgres-state-machine spine, not an invention.
- **Cross-tenant read barrier:** `services/control-plane/.../authz.py` — returns no rows on tenant
  mismatch (Gallop's "404-not-403" structural authz; **no RLS**).
- **Durable code understanding** (`repo_maps`); the **consent gate** (`transport/consent.py` +
  `join.py`); the **staged-draft** credential-boundary path.
- A **coherent Terraform stack** (Cloud Run + Cloud SQL + GCS + Secret Manager + Artifact Registry) and
  a dev deployment (`proxy-meeting-dev`) — **re-verify it runs current code before relying on it.**

**Not yet built (this track's work):**
| Gap | Why it blocks |
|---|---|
| Durable brain persistence (`meetings`/`meeting_events`/`memory` + a **meeting-end writer**) — teardown discards everything today | keystone; post-meeting depends on it |
| Toolchain-template bake + repo-in-GCS pull-at-spawn (today: cold clone + installs, 60–90s) | latency + cost |
| Private-repo clone wired into the sandbox fetch (host-side GitHub-App token) | can't index private repos |
| Per-tenant `ANTHROPIC_API_KEY` (shared personal token today) | ToS + isolation |
| 4 ops-safety fixes: heartbeat · orphan reaper · admission + spend-cap · circuit breaker | multi-instance safety + cost tail |
| Deploy pipeline (build → immutable release image → deploy per tenant) | "fully on the cloud, nothing manual" |

## 2. Where we need to be (Track A end goal)
A meeting for **any** tenant runs entirely on hosted GCP infra with zero manual steps: the control
plane admits it, spawns a body from a baked image in seconds, the body reads the brain + repo, Claude
works, world-touching actions stage as drafts executed host-side, the record folds into the brain, and
the body is destroyed — at 70–85% gross margin, with orphan bodies ≈ 0, and isolation/consent/credential
guarantees a security-conscious VP-Eng signs off on.

---

## 3. Architecture — three planes
```
 CONTROL PLANE — Cloud Run (ONE service, min_instances=1, HOLDS ALL CREDENTIALS)
   webhooks (Recall/GitHub/Composio, signed-origin verified) · admission · orchestration
   Recall/Cartesia relay · the to_meeting surface · heartbeat · reaper · spend-cap · billing
        │ durable truth                                   │ spawns 1 body per meeting (MANY ‖)
        ▼                                                 ▼
   BRAIN (per tenant): Cloud SQL (structured) + GCS   ◀──▶  BODY — sandbox (ephemeral, NO creds)
   (blobs), tenant_id, one per company, forever       seed/  repo (from GCS) + Claude runs HERE,
                                                       fold   booted from a golden toolchain image
```
This is the converged 2025–26 multi-tenant AI-agent shape; the risk is execution completeness, not the
architecture. **Two inversions vs Gallop (for live latency):** (1) **Claude runs INSIDE the sandbox**,
repo local → code tools are instant built-ins, no per-tool round-trip; (2) **prompt-cache the stable
prime** (behavioral prime + `REPO_MAP` + meeting info) → each wake pays only the fresh transcript delta.
Model = **Claude Sonnet 5**; we hold the Anthropic key and meter per tenant.

## 4. The instance model — brain (durable) + body (ephemeral)
- **The company's "Proxy" IS its durable brain** (per-tenant Cloud SQL + GCS), one per company, forever
  — this is what the customer buys.
- **A meeting spawns a body** that reads the brain, works, folds results back, then is reaped. Compute
  is disposable; the brain is the product. **One brain, MANY concurrent bodies.**
- **Compute = E2B for v1 (LOCKED); GCE-Spot is the documented swap behind a provider seam** (chosen
  later to drop the vendor / escape concurrency caps). Gallop's GCE lifecycle is the reference the seam
  targets.

## 5. How the agent runs
- **Agent loop** = the SDK `query()` agentic loop behind a single **AgentService choke-point** (owns the
  loop, event→delta translation, the injection-guardrail append, tool config, and **abort = barge-in**).
- **Tools:** code = in-sandbox built-ins; the **only MCP surface is `to_meeting`** (say/chat/screen/
  offer). World-touching = staged draft, executed host-side (credential boundary).
- **Wake:** voice + chat (Recall chat events). **Voice out = Recall Output Audio** (base64 MP3 clips via
  Cartesia — simplest path; continuous Output Media is a later upgrade).

## 6. Template + spawn (copy Gallop: disposable body, durable state elsewhere)
- **Bake the TOOLCHAIN into ONE golden image** (Packer, `workspace.pkr.hcl` analog: claude + mcp + git +
  deps + agent code), content-hash-labeled → boot ships only a tiny config-only startup script; no
  install cost; the runtime boots "the newest labeled image" → no image/server skew.
- **Keep the repo as a shallow (`--depth=1`) copy in GCS, refreshed by the PR-push webhook** — a
  *mutable* store, not the immutable image. Always current; no template rebuilds.
- **`REPO_MAP` computed once, persisted in the brain, re-indexed on push** — never per meeting.
- **At spawn** (pre-warmed on Recall's `bot.joining_call`, ~2 min early): boot image → pull repo from
  GCS (no GitHub hit) → load cached prime → ready. Per-repo full-image bake = huge-monorepo escape hatch.

## 7. Persistence / data model
**Rule:** the sandbox is a rebuildable cache; **durable truth = Postgres + GCS**, keyed by `tenant_id`.
Structured tables and the brain live in **The Seam** (§9) — Track A **reads** the tenant/connection/repo
rows and **writes** the brain (`meetings`/`meeting_events`/`memory`) via the **meeting-end writer**.
GCS (object-versioned, per-tenant prefix `gs://…/<tenant_id>/…`): the `REPO_MAP`, the shallow repo copy,
full transcripts, staged diffs, and **git-mirrored body output**. "Every meeting = a stored data point":
each meeting writes its row + transcript + events and folds decisions/ownership into `memory` — that's
how the brain learns across meetings.

## 8. Concurrency & lifecycle (copy Gallop's `ResourceOrchestrator` exactly)
**Already correct in code:** the atomic claim + per-meeting registry. **To run dozens across instances —
4 additive fixes (no redesign):**
1. **Tick the meeting heartbeat** (fence exists, isn't ticked; else a 2nd instance reaps live meetings)
   — *or* pin Cloud Run to 1 instance for now. *Do first.*
2. **Watchdog + orphan-sandbox reaper** — a **Cloud-Scheduler reconcile sweep** (idle-reap + stuck-row
   recovery), tag every body `{meeting_id, tenant_id}`, plus a **`maxRunDuration`+DELETE backstop** so a
   dead reaper can't leak a body.
3. **Admission control + per-tenant spend cap** before spawn (cost/DoS backstop).
4. **Provisioning circuit breaker** (3 strikes).

**Claim/lifecycle mechanics (Gallop):** partial unique index (one active row per meeting) + `INSERT …
ON CONFLICT DO NOTHING` + status-guarded `UPDATE`; 5-min heartbeat bumps `last_active_at`; **git-mirror-
to-GCS makes the body deletable, not just stoppable**, guarded by the **refuse-to-wipe triad** (`.git`
present + HEAD resolves + tracked files non-empty) + a Storage-API preflight so a boot error can't
masquerade as an empty bucket. Aggressive sliding-TTL destroy (minutes after a call). **Open upside:**
GCE Spot + a tiny warm pool (the externalized-state design already permits both).

## 9. THE SEAM — the shared contract with Track B (FROZEN; build against this)
> **Track A and Track B meet ONLY here. This block is identical in both docs. Freeze it first — then
> Track A develops against SEEDED rows and Track B against a STUB runtime, and they never touch again.**

**Shared Postgres schema** (Proxy owns tenancy; `tenant_id` on every row; **no vendor ID is ever the
tenant boundary**; **no RLS** — enforce with the structural access-control factory / `authz.py`):
- `users(id, email, provider, provider_id)` — identity = `(email, provider)`; `provider_id` is a TOFU
  anchor locked on first login.
- `tenants(id, name, domain, consent_policy, status)` — the **org is authoritative**; `domain` is
  evidence only.
- `members(user_id, tenant_id, role[admin|member], status[pending|active])` — **closed-by-default.**
- `sessions(...)` — server-side login sessions (signed cookie).
- `connections(id, tenant_id, provider, integration_id, provider_connection_id, scopes, consent,
  credential_health, revocation_state, audit)` — Proxy's authoritative integration record; Composio IDs
  are **references** stored here.
- `repos(id, tenant_id, github_install_id, repo_full_name, current_sha, template_ref, index_status)`.
- `upcoming_meetings(id, tenant_id, source, start_at, meeting_url, recall_bot_id, status)`.
- **brain:** `meetings(id, tenant_id, repo_id, pinned_sha, status)`, `meeting_events(...)`, `memory(...)`.

**Three interface contracts (the only calls that cross the seam):**
1. `resolve_consent(tenant_id, meeting) → policy` — admission reads `tenants.consent_policy`; **default
   CLOSED** (join + announce only; no recording/transcription/analysis/voice/proactive). Track B sets
   it; **Track A enforces it before the body does anything observable.**
2. `get_scoped_token(tenant_id, provider, scope, resource_id) → short-lived token` — the **credential
   boundary.** Long-lived creds live host-side (in `connections`, AES-256-GCM, domain-separated keys);
   the runtime requests a short-lived, resource-scoped token (DB-persisted expiry + atomic claim; HS256
   JWT bound to the resource id; only token *hashes* in DB). **The sandbox never holds a long-lived or
   push/send credential.**
3. `get_repo_clone_token(repo_id) → host-side GitHub-App install token` — Track B stores the install;
   **Track A clones host-side and hands the body a read-only checkout (no push cred).**

**Inbound events (Recall/GitHub/Composio):** every webhook is verified by **signed origin (HMAC +
`timingSafeEqual`)** and mapped to a tenant through OUR `connections`/`repos`/`upcoming_meetings` record
— **NEVER a payload field.** (Gallop is pull-based, no verifier to copy — we build this.)

**Who writes / who reads:** **Track B WRITES** `users/tenants/members/connections/repos/upcoming_meetings`
and sets `consent_policy`; **Track A READS** them to spawn bodies + join meetings and **WRITES the brain**,
which Track B's console READS.

## 10. Hosting & deploy (copy Gallop; "fully on the cloud")
- **Everything is Cloud Run.** Control plane at **`min_instances=1`** warm floor (Gallop deliberately
  does NOT scale-to-zero the interactive app — cold-start avoidance), `max_instances` = cost cap,
  `--no-cpu-throttling`, `--timeout=3600`, private-ranges-only egress + Cloud NAT.
- **True scale-to-zero = the ephemeral bodies;** batch/post-meeting work runs as **Cloud Run Jobs**
  (zero idle).
- **Deploy path:** Cloud Build → Kaniko SHA-tagged image → a **`promote-to-release` job `crane copy`s**
  it into an immutable `releases` Artifact Registry repo → `gcloud run deploy --impersonate-service-
  account` per tenant/consumer row. Terraform owns the service shell (`ignore_changes=[template]`,
  placeholder image at create); the **pipeline owns the image.**
- **Cloud SQL** private-IP-only via the proxy socket, REGIONAL + PITR. **Secret Manager only**, per-key
  `secretAccessor`, `prevent_destroy` + `ignore_changes=[secret_data]`, **boot fail-fast gates that
  `exit(1)` naming the missing key.**
- **Terraform estate (Gallop `modules/{bootstrap,platform,customer-platform}`):** state in a versioned+
  locked GCS bucket; `platform` = the recurring stack; `customer-platform` = the per-customer override
  (the enterprise escape hatch, §12).

## 11. Security within the runtime
- **Credential boundary** (§9.2): control plane holds all long-lived creds; the sandbox gets short-lived
  resource-bound scoped tokens only. **We copy Gallop's pattern but NOT its one exception** — Gallop puts
  the SCM token on the VM via the clone URL; our Law 3 keeps push creds host-side (host-side clone/PR).
- **Isolation:** `tenant_id` everywhere + per-meeting sandbox + per-tenant GCS prefix + a JWT
  host↔sandbox control channel + the credential boundary. **No RLS — structural authz + 404** (already
  in `authz.py`). Cross-tenant read = P0.
- **Egress:** a **generous allowlist** (package registries, GitHub, Anthropic, the host) — not arbitrary
  hosts; the injection guardrail is the backstop. Tighten to strict before security-conscious customers.
- **Audit:** an append-only `audit_log` + a **shared server-side writer seam wired into every mutation**
  (Gallop's table exists but has no writer — **we build the writer**). Cloud Audit Log alert with an
  anti-evasion IAM-change filter.

## 12. Multi-tenant scale, isolation, cost
- **N tenants on shared infra (v1):** ONE Cloud Run, ONE Cloud SQL (`tenant_id`), ONE GCS bucket
  (per-tenant prefixes). Compute scales by adding bodies (bounded by admission); the reaper keeps orphans
  ≈ 0; idle ≈ $0 (bodies) with the warm control-plane floor.
- **Isolation decision (LOCKED):** **shared-`tenant_id` for v1 startups**; **per-customer GCP project for
  enterprise** (adopt Gallop's `customer-platform` + `provision.sh` verbatim). Deliberate divergence from
  Gallop's per-project default — a dedicated Cloud SQL (~$250–350/mo) under every startup would break the
  70–85% margin model.
- **Cost:** per meeting ≈ compute + Sonnet-5 tokens (dominant, cache-reduced). The real risk is
  un-reaped/un-capped bodies → fix #2/#3. Retention: transcript ~90d, `meeting_events` ~12mo, decisions ∞
  (capped); raw media on Recall (or `retention:null`); offboard = purge rows + GCS prefix + revoke +
  kill bodies.

## 13. Track A build phases (buildable, ordered)
**Phase 0 — Hosting & deploy foundation:** re-verify `proxy-meeting-dev`; stand up the deploy pipeline
(Cloud Build→Kaniko→promote-to-release→deploy, control plane `min_instances=1`); bake the golden image;
Cloud SQL private-IP + PITR; GCS versioned per-tenant prefixes; Secret Manager fail-fast gates.
**Phase 1 — Foundation core:** (1) meeting-end writer → persist meetings/events + fold `memory`; (2)
repo-in-GCS + pull-at-spawn + private-repo clone wired; (3) git-mirror-to-GCS + refuse-to-wipe +
preflight; (4) per-tenant `ANTHROPIC_API_KEY`; (5) the 4 ops-safety fixes.
*Done when: a **seeded** private-repo tenant's meeting runs end-to-end on hosted infra, persists into the
brain, and no orphan body survives a reaper sweep.*
**Later:** post-meeting execution runs here as Cloud Run Jobs (Batch API 50% off for autonomous parts) —
its product shape is framed in Track B, but this runtime executes it.

## 14. Founder-gated (not code)
Real vendor keys in Secret Manager · GCP billing + `terraform apply` · build/push the image + set
`PUBLIC_BASE_URL` · register the Recall webhook · bake + publish the golden image.

## 15. What Track B is doing (so this chat knows the boundary)
Track B builds the **front door**: sign-in (Authlib Google/MS), identity (domain→org, closed-by-default
`members`), the connect flow, the 4 OAuth apps at least-privilege scopes, the Composio adapter, calendar/
Recall connect, per-tenant email routing, the consent-policy selector, and the console. **It hands Track A
the rows in §9 and sets `consent_policy`; it reads the brain Track A writes, for the console.** You do not
wait on it — seed the rows and build.
