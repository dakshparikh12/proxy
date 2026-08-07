# Proxy — The Plan: from working in-meeting core → customer-deployable cloud SaaS

**THE singular plan (2026-08-06).** Supersedes the status/decision framing of the topic docs
(`ARCHITECTURE.md`, `ONBOARDING-INTEGRATION.md`, `CLOUD-RUNTIME.md`, `PERSISTENCE-LIFECYCLE.md`,
`PRODUCTION-READINESS.md` — kept as detail). Red-teamed from fresh context against the code.
**Honest framing (the red-team's #1 lesson): this is NOT "hardening a working system" — it is
finishing the platform on top of a working in-meeting core.** Post-meeting is deliberately deferred.

---

## 0. Honest status — what's REAL vs the PLAN (verified in code)
**Real & working today:**
- The live in-meeting loop (webhook → provision → warm sandbox → wake → respond over Recall/Cartesia).
- The **concurrency primitives**: the per-meeting `MeetingRuntimeRegistry` + a correct **atomic claim**
  (one harness per meeting) — genuinely built and clean.
- The durable **code understanding** (`repo_maps`, per tenant/repo/sha) — this part of the brain is real.
- Staged-draft (world-touching) path exists; host never pushes.
- A coherent Terraform stack + the GitHub connect→index trigger.

**Plan, NOT yet built (do not describe as current):**
- The **durable "brain" that gets smarter across meetings** — `meetings`/`meeting_events`/`memory`
  tables and any **meeting-end persistence writer** do **not** exist (`end_meeting` only kills the
  sandbox). This is the single biggest build item.
- **Template-baked fast spawn** — `DEFAULT_TEMPLATE=None`; every meeting **cold-clones ~60–90s**.
- **Private-repo clone into the sandbox** — unwired (clone uses a bare URL, no token).
- The **onboarding/identity/connector stack** — WorkOS / Composio / Resend / Stripe = **zero code**
  (only the GitHub connect trigger exists).
- **Operational safety** — meeting heartbeat not ticked; no admission control / spend cap; the orphan
  reaper is unwired and sandboxes aren't tagged.

## 1. Target architecture (coherent, industry-validated)
One **control plane** (Cloud Run) + one durable **brain per tenant** (Cloud SQL + GCS) + **many
ephemeral bodies** (E2B sandboxes), one per meeting, spawned in parallel, folding results back into
the brain. This shape — control plane + shared DB with `tenant_id` logical isolation + per-session
managed microVMs — is the **converged 2025-26 pattern** (cf. AWS Bedrock AgentCore's per-session
microVMs; the control-plane/application-plane split). We're on-pattern; the risk is *execution
completeness*, not the architecture. (Full picture: `ARCHITECTURE.md`.)

## 2. The per-customer lifecycle (onboarding + cloud, unified)
`TENANT → REPOS(1..N) → MEETINGS(0..N) → BODY(1 per meeting, many ‖) → MEMORY(accumulates)`
1. **Onboard** (same for every customer, frictionless): SSO sign-in → WorkOS Org = tenant → brain
   shell (Postgres rows + GCS prefix) + **per-tenant email address** + connections. *(design-only today)*
2. **Connect a repo** (our own GitHub App): clone → build understanding → store (`repo_maps`+GCS) →
   **bake the per-repo template**. PR/push re-indexes → stays fresh. *(understanding real; bake TO-BUILD)*
3. **Meeting**: resolve tenant (per-tenant email / calendar / webhook) → **atomic claim** → spawn a
   body **from the template + cached prime + brain** → Recall joins (host-side) → Claude runs *in the
   sandbox* → voice/chat out. World-touching = staged draft, executed host-side.
4. **Meeting-end**: persist the record + git-mirror output + **fold into the brain** → reap. *(TO-BUILD)*
5. **Across meetings**: the brain is the continuity — one per company, persistent, learning.
6. **Multi-customer**: N tenants on shared infra (one Cloud Run, one Cloud SQL + RLS, one GCS bucket
   with per-tenant prefixes); enterprise → dedicated GCP project.

## 3. Exact cloud mechanisms
Cloud Run (control plane) · Cloud SQL Postgres (**shared, `tenant_id` + RLS** — RLS is a target; today
app-level only) · GCS (object-versioned, per-tenant prefixes) · Secret Manager · Artifact Registry
(control-plane image) · **E2B** (bodies + baked templates) · Cloud Scheduler (reaper/stale-sweep) ·
inbound email (Cloudflare/Postmark → webhook). **Spawn:** per meeting, boot a sandbox from the baked
template (target) — today a cold clone; **pre-warm from the calendar** (target; today invite-triggered
only). **Concurrency:** the atomic claim + per-meeting registry already isolate; scaling to dozens
across instances needs the heartbeat + reaper + admission control (§5).

## 4. Locked decisions + reconciliations
- **Compute: E2B — LOCKED.** (Resolves the contradiction: `PRODUCTION-READINESS.md`'s "open decision"
  line is superseded.) GCE-copy-Gallop is the documented future swap behind the provider seam.
- **DB: shared Cloud SQL + `tenant_id`/RLS by default; dedicated project/DB = enterprise escape hatch.**
- **Credentials:** host-side; the sandbox holds **no world-touching push/send creds**, and every
  world-touching action is a staged draft executed host-side. **Correction (honesty):** today the
  sandbox *does* receive the shared Claude subscription token (+ optional OpenAI/FAL/etc.) — these
  must be de-shared (per-tenant `ANTHROPIC_API_KEY`; drop/scope the rest). Restate the invariant as
  "no push/send creds," and fix the LLM-key sharing (§5).
- **Email:** per-tenant address on one catch-all domain = the routing key (`acme@useproxy.co`), not
  one shared address.
- **Isolation:** `tenant_id` everywhere + per-meeting sandbox + per-tenant GCS prefix; RLS to add.

## 5. THE FIRST-CUSTOMER BLOCKERS (ordered — this is the build list)
Everything below **blocks a first paying customer**; each is bounded (not a redesign):
1. **Private-repo clone into the sandbox.** Thread `premeeting.github_auth`'s GitHub-App installation
   token into the workroom clone (`workroom.py:1010`). *Without it, real private repos boot brainless.*
2. **Swap the Claude subscription token → per-tenant `ANTHROPIC_API_KEY`** (+ stop forwarding shared
   OpenAI/FAL/Replicate/Context7 keys, or scope them). *ToS + cross-tenant credential isolation.*
3. **Tick the meeting heartbeat** (start `_heartbeat_loop` on the won claim; self-terminate on
   `is_owner=False`) **— or pin Cloud Run to one instance for now.** *Else a 2nd instance reaps live meetings.*
4. **Operational safety layer:** per-tenant **spend cap + admission control** before spawn; a **meeting
   time cap**; a **sandbox-tagging reaper** (tag E2B `create` with `{meeting_id, tenant_id}` + wire the
   reconcile loop). *Cost-bomb prevention — the most likely surprise bill.*
5. **Meeting-record persistence at end** (write `meetings` + transcript + `meeting_events`; fold into
   memory). *The keystone for "gets smarter across meetings" + all of post-meeting. Hard blocker if the
   pitch is cross-meeting value; near-term otherwise.*
6. **Recording consent/announce + DPA + subprocessor list.** *Legal gate to record real people.*
7. **A working delete/offboard path** (purge tenant Postgres rows + GCS prefix + revoke tokens + kill
   sandboxes). *Security-review + trust requirement.*
8. **Minimal onboarding to connect a repo + join a meeting** (GitHub connect exists; wire the per-tenant
   email routing + the connect UI; WorkOS/Composio can start minimal).

**Founder-gated deploy (only you):** real vendor keys in Secret Manager · GCP billing + `terraform
apply` · build/push the image + `PUBLIC_BASE_URL` · register the Recall webhook · **bake the E2B
template** (fixes the 60–90s cold spawn) + bigger machine (OOM fix).

**Latency near-term (strongly recommended, not strictly blocking):** the template bake (#founder) +
**calendar pre-warm** so an un-pre-warmed meeting doesn't pay the full cold cost while the room waits.

## 6. Deferred (correctly)
Post-meeting features · cross-meeting `memory` retrieval sophistication (start structured, simple) ·
WorkOS SSO/SCIM · SOC 2 · per-tenant GCP projects · per-tenant KMS/BYOK · the GCE compute swap ·
vector memory.

## 7. What we copy from Gallop (proven at enterprise scale)
Postgres(meta)+GCS(blobs) split · git-mirror-to-GCS (HEAD-verify + refuse-to-wipe) · atomic-claim /
heartbeat / reaper concurrency spine · `tenant_id`-everywhere + shared-default + per-customer-project
escape hatch · Packer/template golden-image pattern (as an E2B baked template) · the `AgentService`
choke-point · encrypted per-tenant tokens · the credential boundary. Reference repo: `~/platform`.

## 8. Bottom line
The architecture is coherent, on-pattern, and the hard concurrency primitives are genuinely built —
**not a fantasy plan.** But we are *finishing a platform on a working in-meeting core*, not hardening
a finished system. The path to a first customer is the **eight ordered blockers in §5** plus the
founder-gated deploy — each bounded. Do those, and Proxy is customer-deployable on the cloud, at
scale, for the frictionless first customers.
