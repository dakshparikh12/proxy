# Proxy — Build Roadmap (where we are → where we're going → buildable steps)

**The third canonical doc.** `ONBOARDING-INTEGRATION.md` and `CLOUD-RUNTIME.md` describe the *target
design*; this doc is the **path**: exactly where we are, the fully-hosted end goal, the locked
decisions (nothing left to debate), how GitHub + the cloud fit together, and the ordered buildable
phases. Grounded in a deep `file:line` code-study of **Gallop** (`/Users/daksh/platform`) — the
detailed extractions live in `scratchpad/gallop-{onboarding-access,security,compute-vm,
gcp-tenant-serverless}.md`. Current as of 2026-08-07.

Principle: **copy Gallop's proven mechanics; diverge only where our ICP (startups → enterprise) or
our latency/credential laws demand it — and say so explicitly when we do.**

---

## 1. The end goal (stated concretely — this is what "done" means)
A fully-hosted, multi-tenant SaaS on GCP where **nothing is manual**:
- **Every tenant = one durable "brain"** (Postgres rows + GCS blobs, keyed by `tenant_id`), hosted on
  the cloud, living forever. This is what the customer buys.
- **One control plane** (Cloud Run) holds all credentials, runs meeting-admission + orchestration,
  and is the only thing that touches the world.
- **Every meeting spawns an ephemeral body** (compute sandbox) that reads the brain, does the work,
  folds results back, and is destroyed — the body is a rebuildable cache, never durable state.
- **GitHub is wired end to end:** our GitHub App clones the repo (host-side), a push webhook refreshes
  a shallow repo mirror in GCS, and host-side PRs are the differentiated write path. The sandbox holds
  no GitHub push credential.
- **Deploys are pipeline-driven** (build → immutable release image → `gcloud run deploy` per tenant/
  consumer row), and **adding a customer is a reviewed config change**, not hand-work.
- Reactive **and** post-meeting both live on this foundation; the brain is the cross-meeting continuity.

**Done = a real customer's repo, meetings, and follow-up work all running on this hosted stack with
zero manual steps per meeting, at 70–85% gross margin, with the isolation/consent/credential
guarantees a security-conscious VP-Eng will sign off on.**

---

## 2. Where we are (the "you are here" map)

**Real today — the hard part is built and proven:**
- The **live in-meeting reactive loop** (the core product), grounded + verified on real data.
- **Concurrency primitives:** atomic claim + per-meeting `MeetingRuntimeRegistry` (built) — and the
  research confirms these ARE Gallop's proven spine (Postgres-as-state-machine), not an invention.
- **Cross-tenant read barrier:** `services/control-plane/.../authz.py` (built) — returns no rows on a
  tenant mismatch, exactly Gallop's "404-not-403" structural-authz posture.
- **Durable code understanding** (`repo_maps`), the **consent gate** (`transport/consent.py` +
  `join.py`), and the **staged-draft** credential-boundary path.
- A **coherent Terraform stack** (Cloud Run + Cloud SQL + GCS + Secret Manager + Artifact Registry).
- A **dev GCP deployment exists** (`proxy-meeting-dev`, per prior notes) — **re-verify before relying
  on it; do not assume the live revision runs current code.**

**Not yet built — the gap to a hosted product (this is the roadmap):**
| Gap | Why it blocks | Phase |
|---|---|---|
| Durable brain persistence (`meetings`/`meeting_events`/`memory` + meeting-end writer) — teardown discards everything today | keystone; post-meeting depends on it | 1 |
| Toolchain-template bake + repo-in-GCS pull-at-spawn (today: cold clone + installs, 60–90s) | latency + cost | 1 |
| Private-repo clone wired into the sandbox fetch | can't index private customer repos | 1 |
| Per-tenant `ANTHROPIC_API_KEY` (shared personal token today) | ToS + isolation | 1 |
| 4 ops-safety fixes (heartbeat, orphan reaper, admission/spend-cap, circuit breaker) | multi-instance safety + cost tail | 1 |
| Identity: domain→org rewrite (`identity.py` bug) + `members(role,status)` + closed-by-default | correctness + trust | 2 |
| `connections` integration-record table + Composio adapter behind a seam | Proxy-owned tenancy (P0 isolation) | 2 |
| HMAC signed-origin webhook verifier (Gallop has none to copy — we build it) | inbound tenant routing must not trust payload | 2 |
| `audit_log` server-side writer seam wired into mutations | enterprise trust (table exists, writer doesn't) | 2 |
| Onboarding stack: sign-in wiring, 4 OAuth apps (least-privilege), email routing, connect page + console, consent-policy selector, per-platform readiness UI, delete/offboard | the front door | 2 |
| Post-meeting execution (plan → do the action items) | the second product | 3 |

**Founder-gated (not code — access/decisions):** real vendor keys in Secret Manager · GCP billing +
`terraform apply` · build/push the control-plane image + set `PUBLIC_BASE_URL` · register the Recall
webhook · bake + publish the compute template.

---

## 3. Locked decisions (nothing here is up for debate)

1. **Compute = E2B for v1 (LOCKED), GCE-Spot as the documented swap behind a provider seam.** Gallop's
   GCE lifecycle (`ResourceOrchestrator.ts`, `gce.ts:505` `instances.insert`, Packer `workspace.pkr.hcl`)
   is the reference the seam targets when we drop E2B for cost/scale.
2. **Isolation = shared-`tenant_id` for v1 startups; per-customer GCP project for enterprise.** This is
   our one deliberate divergence from Gallop, and it's a cost decision: Gallop runs a **dedicated GCP
   project per customer** (own Cloud SQL, VPC, secrets) because its customers are enterprise from day
   one. A dedicated Cloud SQL under every startup (~$250–350/mo each) would break the 70–85% margin
   model. So: **shared control plane + shared Cloud SQL + `tenant_id` everywhere + the access-control
   factory (404 on mismatch) for v1**, and **adopt Gallop's `terraform/modules/customer-platform` +
   `provision.sh` verbatim as the enterprise tier** when a customer demands hard isolation and pays
   for it.
3. **No Postgres RLS — copy Gallop's structural authz instead.** Gallop uses no RLS anywhere; a typed
   route wrapper where handlers can't read tenant IDs from the body, authz uses only credentials +
   looked-up params, an AST test bans bypass, and cross-tenant returns **404**. Our `authz.py` already
   does this — so **drop "RLS to add" from `CLOUD-RUNTIME.md`; the target is the access-control factory,
   not RLS.**
4. **Membership = closed-by-default (admin pre-provisions / invites; never auto-join).** Confirmed as
   Gallop's literal practice (`passport.ts:70-96`: OAuth resolves an existing user, never creates one;
   `domain` is only an allow-list constraint on who an admin may add). Already fixed in
   `ONBOARDING-INTEGRATION.md`.
5. **Auth = Passport-equivalent (Authlib) Google/MS OAuth + Postgres sessions.** Identity key
   `(email, provider)`; a TOFU `provider_id` anchor locked on first login; `httpOnly`+`sameSite=lax`
   cookie; fail-fast `SESSION_SECRET`; a CSRF header guard; platform-admin via an env allow-list
   reconciled on boot with a zero-admin rollback guard. No JWT for user sessions.
6. **Secrets = Secret Manager only**, per-key `secretAccessor` (never project-wide), Terraform
   `prevent_destroy` + `ignore_changes=[secret_data]`, **boot fail-fast gates that `exit(1)` naming the
   missing key** (`server.ts:181-188`).
7. **Per-tenant credentials = AES-256-GCM**, fresh IV per encrypt, `{ciphertext,iv,tag}` columns, key
   from Secret Manager, **domain-separated keys** (separate GitHub vs. integrations key = bounded blast
   radius); prod throws if key absent (`aesGcm.ts`).
8. **Credential boundary = control-plane holds all long-lived creds; the sandbox gets short-lived,
   resource-bound scoped tokens only** (Gallop `tokenPush.ts`: DB-persisted expiry + atomic claim +
   HS256 JWT bound to the resource id; only token *hashes* in DB). **We copy the pattern but NOT
   Gallop's one exception** — Gallop puts the SCM token on the VM via the clone URL; our Law 3 keeps
   push/send creds host-side, so host-side clone/PR only.
9. **Compute lifecycle = disposable body, durable state elsewhere.** Bake a golden image (config-only
   startup script), externalize state via **git-mirror-to-GCS** with the **refuse-to-wipe triad**
   (`.git` present + HEAD resolves + tracked files non-empty; `gcs.ts:197-221`) so the body can be
   *deleted*, not just stopped; DB-state-machine claim + 5-min heartbeat + Cloud-Scheduler reconcile
   sweep (idle-reap + stuck-row recovery + 3-strike breaker) + a `maxRunDuration`+DELETE backstop.
10. **Hosting = Cloud Run, `min_instances=1` warm floor for the interactive control plane** (Gallop
    deliberately does NOT scale-to-zero the interactive app — cold-start avoidance); **true
    scale-to-zero is the ephemeral bodies**; batch/post-meeting work runs as **Cloud Run Jobs** (zero
    idle). Cloud SQL private-IP-only via the proxy socket, REGIONAL + PITR.
11. **Model = Claude Sonnet 5**, prompt-cached prime; we hold the Anthropic key and meter per tenant.

---

## 4. The full hosting + GitHub story (how it all plays together)
```
 Developer push ─▶ GitHub App ─▶ push webhook ─▶ CONTROL PLANE (Cloud Run, min_instances=1)
                                    │                 │  holds ALL creds · admission · orchestration
                                    │                 ├─▶ refresh shallow repo mirror in GCS (per-tenant prefix)
                                    │                 ├─▶ rebuild REPO_MAP → brain (Postgres + GCS)
 Recall (calendar OAuth) ──────────┘                 │
   discovers meeting, schedules join ─▶ bot.* webhook ─▶ orchestrator: claim + admission + consent +
                                                          spend-cap → spawn BODY
                                                              │
   BODY (E2B now / GCE-Spot later, from golden image)  ◀──────┘  pulls repo from GCS + brain + prime
     Claude runs INSIDE · code tools local · NO push/send creds
     world-touching = staged draft ─▶ control plane executes host-side after a human click
     meeting-end ─▶ git-mirror-to-GCS + fold into brain ─▶ body DELETED (reaper backstop)
 Composio (adapter behind a seam) ─▶ Slack/Google/MS/Jira… scoped by OUR connections record
```
**Everything is hosted:** the brain (Cloud SQL + GCS), the control plane (Cloud Run), the bodies
(E2B/GCE), the image (Artifact Registry), secrets (Secret Manager), the reaper (Cloud Scheduler).
**Nothing runs on a laptop.** **Deploy path (copy Gallop):** Cloud Build → Kaniko SHA-tagged image →
`promote-to-release` job `crane copy`s it to an immutable `releases` repo → `gcloud run deploy
--impersonate-service-account` per consumer row; Terraform owns the service shell
(`ignore_changes=[template]`), the pipeline owns the image. **Onboarding a new customer = a reviewed
PR adding one row** (our analog of Gallop's `consumers.auto.tfvars`) — for v1 that row provisions a
tenant in the shared plane; for enterprise it instantiates the `customer-platform` module.

---

## 5. Gallop copy-map (condensed — full `file:line` in the four scratchpad briefs)
- **Onboarding/access** → closed-by-default membership; Passport→Authlib OAuth + `connect-pg-simple`
  sessions; `(email,provider)` + TOFU anchor; access-control factory (404). `gallop-onboarding-access.md`
- **Security** → Secret Manager + fail-fast gates; AES-GCM domain-separated per-tenant creds;
  short-lived resource-bound scoped tokens; private-IP SQL + private-ranges egress + NAT; append-only
  `audit_log` + IAM-change anti-evasion alert. **Two gaps we build ourselves:** the `audit_log` writer
  seam, and an HMAC signed-origin webhook verifier (Gallop is pull-based, nothing to copy).
  `gallop-security.md`
- **Compute/VM** → Packer golden image + config-only startup; `ResourceOrchestrator` DB-state-machine;
  heartbeat + reconcile reaper + 3-strike breaker; git-mirror-to-GCS + refuse-to-wipe; `e2-medium`
  default + `maxRunDuration` backstop; spot + tiny warm pool = our open upside. `gallop-compute-vm.md`
- **GCP/tenant/serverless** → `modules/{bootstrap,platform,customer-platform}`; state in a versioned+
  locked GCS bucket; Cloud Run warm-floor + Cloud Run Jobs for batch; SA-to-SA `provision.sh`;
  release-registry promote pipeline. `gallop-gcp-tenant-serverless.md`

---

## 6. The buildable phases (ordered; each step concrete + non-debatable)

**Phase 0 — Hosting & deploy foundation (make the cloud real).**
0.1 Re-verify the `proxy-meeting-dev` deployment; confirm the live revision, secrets, SQL, image.
0.2 Stand up the **deploy pipeline** (Cloud Build → Kaniko → promote-to-release → `gcloud run deploy`),
    control plane at `min_instances=1`; Terraform owns the shell, the pipeline owns the image.
0.3 Bake the **compute template/golden image** (toolchain + agent), content-hash-labeled.
0.4 Cloud SQL private-IP + PITR; GCS versioned, per-tenant prefixes; Secret Manager fail-fast gates.

**Phase 1 — Foundation core (brain + spawn + ops-safety).** *The keystone phase.*
1.1 **Meeting-end writer** → persist `meetings`/`meeting_events` + fold into `memory` (stop discarding).
1.2 **Repo-in-GCS + pull-at-spawn** + **private-repo clone wired** (GitHub-App token, host-side).
1.3 **git-mirror-to-GCS** with the refuse-to-wipe triad + Storage-API preflight.
1.4 **Per-tenant `ANTHROPIC_API_KEY`** (swap the shared personal token).
1.5 **The 4 ops-safety fixes:** tick the heartbeat; wire the Cloud-Scheduler reconcile reaper (tag
    every body `{meeting_id, tenant_id}`); admission control + per-tenant spend cap; 3-strike breaker.
*Done when: a private customer repo indexes, a meeting runs on hosted infra, the record persists into
the brain, and no orphan body survives a reaper sweep.*

**Phase 2 — Identity & onboarding stack (the front door).**
2.1 **Domain→org identity rewrite** (fix `identity.py`) + `members(role,status)` + closed-by-default
    admin-approve/invite.
2.2 **`connections` table (Proxy-owned tenancy)** + Composio adapter behind the seam + **HMAC
    signed-origin webhook verifier** (map to tenant via our record, never a payload field).
2.3 Google/MS **sign-in wiring** (Authlib) + Postgres sessions + TOFU anchor + CSRF guard.
2.4 The **4 OAuth apps** at least-privilege scopes (Slack/Google/MS + the GitHub App).
2.5 **Per-tenant email routing** (catch-all → signed webhook → tenant) via Cloudflare/Postmark + Resend.
2.6 **Connect page + console** (connections+health, members/roles, controls, activity); **consent-policy
    selector** (default closed); **per-platform readiness UI** (Meet-first).
2.7 **`audit_log` writer seam** wired into every mutation; **delete/offboard** path.
*Done when: a founder-assisted onboarding (Phase-1 motion) takes a new company from sign-in → connected
repo + calendar + Slack → first grounded answer, entirely through the hosted UI.*

**Phase 3 — Post-meeting execution.**
3.1 Extraction/planning lane (decisions → owned action items + plan) — cheap, bounded.
3.2 Execution (draft/send docs+messages via Composio; host-side PRs) with an **ACU-style work meter +
    fair-use bound** so the coding tail can't break margin (see the pricing analysis).
3.3 Batch API (50% off) for the autonomous, latency-tolerant portions; run as Cloud Run Jobs.
*Done when: a meeting's action items are completed post-call on hosted infra, metered by work depth.*

**Phase 4 — Enterprise tier (only when demanded + paid).**
4.1 Instantiate Gallop's `customer-platform` per-customer GCP project + `provision.sh`.
4.2 WorkOS (SSO/SCIM/audit-streaming), per-tenant KMS, region/residency controls.

---

## 7. Still genuinely open (honest — resolve, don't hide)
- **Google Meet lobby-skip via a Workspace alias/catch-all** — unproven; test before promising a
  per-tenant meeting identity. Until green: Recall's managed identity + pooled-neutral fallback.
- **Spot VMs + a tiny warm pool for bodies** — real cost/latency upside Gallop doesn't use; decide when
  we take the E2B→GCE swap.
- **Isolation default per tier** — locked as shared-v1 / per-project-enterprise (§3.2); revisit only if
  a startup segment demands hard isolation at startup pricing (it likely won't).
