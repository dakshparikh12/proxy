# Proxy — Path to Customer-Deployable (Master Plan)

**Status:** draft for founder review · **Date:** 2026-08-05 · **Branch:** `post-meeting`
**Read alongside:** `SPEC.md` / `PROXY_SYSTEM_SPEC.md` (product), `CLAUDE.md` (build constitution).

This plan assumes the **reactive product works end-to-end** (founder is finalizing that
locally). It covers *everything else* required to turn a working local tool into a
**customer-deployable, secure, serverless, multi-tenant SaaS** — plus **post-meeting v1**
(reactive + full post-meeting is the v1 we sell). Grounded in the current codebase and a
2025–2026 research pass (compute/persistence, onboarding, security/compliance, legal/billing,
enterprise meeting-agent access). Research briefs live in the session scratchpad.

---

## 0. The end goal (one statement)

> A customer says yes → within ~1 hour they are live: **2 clicks + add-a-guest** (install the
> GitHub App, add Proxy to Slack, add `acme@proxy.<domain>` to their invites). Proxy joins their
> meetings named and disclosed, already knows their code, works live, and after the meeting sends
> grounded notes + action items and executes follow-ups behind a human click. It runs
> **serverless / scale-to-zero** on our cloud, isolates every tenant, and we can hand a technical
> buyer a trust page + DPA + subprocessor list and answer their security questionnaire same-day.

---

## 1. Guiding principles (protect these at every phase)

1. **Serverless / scale-to-zero.** Cloud Run scales to zero; E2B sandboxes are per-meeting and
   killed/reaped (idle → ~$0); Cloud SQL + GCS are managed. We pay per meeting, not per idle hour.
2. **The sandbox is a rebuildable cache, never a source of truth.** Durable truth = Postgres + GCS.
   Any sandbox/snapshot can be lost and the work fully reconstructed. This is what makes aggressive
   cost-reaping safe (§2).
3. **The five laws hold** (grounded-or-silent, never-overstate, human-control-absolute,
   dynamic-not-hardcoded, talk-and-glance). Every world-touching action stays a staged draft behind
   a human click; the sandbox holds no push/send credentials.
4. **Buy the undifferentiated, build the core.** Auth/SSO (WorkOS-class), billing (Stripe),
   compliance automation (Vanta/Drata), legal templates (Common Paper) — buy. The agent loop,
   the map/understanding, the orchestrator — build.
5. **Tenancy is first-class and isolation is a P0.** `tenant_id` in every schema, per-meeting
   sandbox, per-tenant GCS prefix, (target) per-tenant CMEK. A cross-tenant read is a P0 breach.

---

## 2. Compute + persistence architecture (the VM question, resolved)

**Decision: sandbox-as-cache + durable record (a hardened "Model B"), with a short optional
warm-snapshot window.** Post-meeting work is *async, not latency-critical*, so we do **not** keep a
live VM around; we rebuild it cheaply on demand.

### The three tiers
- **Postgres (Cloud SQL)** — structured, transactional, small: meeting records (id, tenant, repo,
  start/end, attendees, status), action items (+ approval state: staged/approved/executed),
  readiness verdicts, per-meeting cost/telemetry, post-meeting task/run status, and **snapshot
  bookkeeping** (`snapshot_id`, `tenant_id`, `meeting_id`, `repo_sha`, `expires_at`, `size_bytes`).
  Every row carries `tenant_id`.
- **GCS (object-versioned)** — blobs, append-heavy, large: transcripts, the understanding/`REPO_MAP`
  docs, **staged diffs/patches** (the human-approval artifacts), agent-generated files. Partition
  by tenant: `gs://…/<tenant_id>/<meeting_id>/…`. Object versioning = free audit trail.
- **E2B snapshot** — ephemeral fast-path ONLY. Holds live work state for a short warm-follow-up
  window (24–72h), TTL'd and **reaped**. If lost, the meeting is fully reconstructable from
  Postgres + GCS + the baked template.

### The lifecycle
1. **Pre-meeting** (once per repo, rebuilt on signed push): clone → build understanding/map → store
   in Postgres+GCS → **bake a per-repo E2B template keyed by commit SHA** (repo + map pre-loaded).
2. **Meeting**: warm a sandbox *from the template* (never re-clone). Stream transcript. Work live.
3. **Meeting-end**: persist the meeting record (transcript + Proxy's staged work + intents) to
   Postgres+GCS. Optionally pause to a snapshot for the warm-follow-up window (record the
   `snapshot_id` + `expires_at`). **Then it's safe to be killed/reaped.**
4. **Post-meeting task** (async): if within the warm window → resume the snapshot (~1s); else →
   spawn a fresh sandbox from the baked template + load the durable record. Do the work, checkpoint
   each step to GCS+Postgres, stage the result behind a human click, then re-pause or kill.

### Hard requirements the research surfaced
- **Own a snapshot reaper** — E2B snapshots have **no TTL / no auto-expiry** and accrue storage
  cost with **no published per-GB rate**. A Cloud Run cron kills expired snapshots by the Postgres
  bookkeeping table. Prefer `keepMemory:false` unless the live process tree is needed on resume.
  *(Get the paused-storage rate from E2B sales in writing before modeling idle as free.)*
- **Never re-clone per meeting** — versioned baked template rebuilt on the same signed-push trigger
  as the map.
- **Any-repo-size escape ladder** (E2B Pro = 8 vCPU / 8 GiB / 20 GB disk; ~4.3 GB Dockerfile-COPY
  limit; no GPU): (a) baked template for <~4 GB working sets; (b) **sparse + shallow clone**
  (`--filter=blob:none --sparse --depth`) — the highest-leverage move, turns a 20 GB repo into a
  few-hundred-MB working set; (c) E2B Volumes + FUSE mount; (d) Enterprise custom disk;
  (e) off-E2B **Cloud Run job** for heavy clone/build → push pruned working set + map to GCS.
- **Two sandbox sizes**: 2 vCPU / 4 GiB default (chat/light), 8 vCPU / 8 GiB for code-heavy repos;
  route >8 GiB builds to the off-E2B Cloud Run job. Cap build subprocess memory so an OOM kills the
  *build*, not the agent (report honestly — Law 2).
- **Abstract compute behind a `libs` seam** (like `libs/http`'s external-call discipline) so
  E2B → Fly Machines / Modal / Northflank-BYOC is a config swap, not a rewrite.

### Cost + when to migrate
- Per meeting ≈ **$0.17/hr** light, **$0.53/hr** heavy — trivial vs Claude token spend. The real
  cost levers are **concurrency** and **un-reaped snapshots**, not runtime.
- E2B Pro: 100 concurrent (→1,100 add-on), 5 sandboxes/s creation (warm-pool to hide bursts).
- Stay on E2B **through product-market fit.** Re-evaluate at **sustained ~100+ concurrent** on
  economics (E2B list ≈ 2–3× a Fly/Modal build, ≈ 8× BYOC at 200-concurrent) and native idle
  billing (Fly Sprites) or GCP-native isolation (Northflank BYOC).
- **The orchestrator is the hard part at scale** (Devin's team: ~¾ of engineering) — provisioning,
  warm pools, reaping, crash recovery, per-action audit. Budget for it; it is not "just the sandbox."

---

## 3. Integrations & permissions model

Separate three things people conflate: **joining meetings**, **accessing code**, **reaching people**.

### Code — GitHub App (read now; PR-write host-side, human-gated)
- **Clone/read** = GitHub App, fine-grained `contents:read` + `metadata:read`, **org-admin-approved,
  per-repo**. Short-lived installation tokens minted per meeting (never stored, never in the sandbox).
- **"Do the task and bring it back" = a PR** — the *control-plane* (host-side) uses the App's
  installation token with `pull_requests:write` to open the PR, **only after a human approves the
  staged draft**. The sandbox never holds the token. Industry standard (Devin/CodeRabbit/Graphite),
  and it keeps Law 3 intact.

### Meetings — Recall join, two admission tiers
- **Recall joins Meet / Zoom / Teams by URL.** Teams meetings are no harder than Meet here.
- **Tier 1 (pilot): guest / waiting-room** — bot lands in the lobby; a host admits it. Handle the
  **5-min silence auto-leave** (join near start) and the not-admitted case honestly.
- **Tier 2 (real customer): calendar-OAuth signed-in bot** — the bot's Google account email is on
  the calendar event, so it **skips the waiting room**, avoids anonymous-guest warnings, and enters
  sign-in-required meetings. Recall **login groups** (a pool of bot accounts) handle concurrency.
  This is also the **fallback for orgs that block external guest bots**.
- **Ultimate fallback (locked-down):** a customer-provisioned bot account inside their own Workspace.

### Reaching people — email is the floor, Slack first, Chat/Teams on demand
Every human has email; calendar attendees, Slack, Chat, and Teams users all key off email. Build
**one internal outreach seam** with pluggable adapters:

| Channel | Access | Effort | When |
|---|---|---|---|
| **Email** (send-only, own domain) | none from customer | lowest | v1 baseline — always works |
| **Slack** | "Add to Slack" → bot token, `chat:write`, domain-verified | low | v1 |
| **Google Chat** | per-org internal app | medium | on demand |
| **Teams chat** | Teams app + MS Graph + admin consent | high | on demand |

So "sometimes Slack, sometimes Teams" never blocks a sale — email always carries the action items;
a chat adapter is an upgrade, not a dependency.

- **Identity resolution:** action-item owner (from transcript) → match to a calendar-attendee email
  → look up their handle on whatever chat platform the tenant connected. Unresolved = flagged
  `UNASSIGNED`, never guessed (Law 1).
- **Outreach approval model (proposed):** the **meeting organizer approves the outreach batch once**
  ("send these notes + reach out to these owners?"); **any code/PR or world-mutation is gated
  individually**; anything a recipient approves is identity-matched. (Founder decision — §9.)

### Recording consent & disclosure (legal floor — see §5)
The bot **joins named ("Proxy — AI teammate") and announces transcription**. Never silent/anonymous.

---

## 4. Onboarding flow (frictionless, org-safe)

Self-serve, admin-led, bound to a verified corporate domain (one tenant = one org/workspace):

1. **Sign up + pay** (Stripe) → tenant created.
2. **Install GitHub App** — org admin approves, selects repos. Proxy indexes async
   (connecting → cloning → indexing → ready, or an honest `not_ready` naming the gap).
3. **Add to Slack** — one click by a workspace admin; **verify the connecting user's email domain
   matches the corporate domain** (blocks personal-account installs).
4. **Invite Proxy to meetings** — add `acme@proxy.<domain>` as a guest (Tier 1), or connect calendar
   OAuth for signed-in auto-join (Tier 2). No Gmail/mailbox read — send-only.

Email "just works" (own domain configured once, globally). Net customer effort: **2 clicks +
add-a-guest.** Every grant is least-privilege, revocable, and bound to `tenant_id`.

---

## 5. Security, trust & compliance (staged; consent is Stage A)

### Stage A — before the first paid meeting
- **Recording consent floor (legal):** bot joins **named + announces transcription**; documented
  consent/disclosure posture; pre-meeting notice; **no silent/anonymous join, ever.** Required for
  any all-party-consent US state (~12–14, incl. CA) or EU meeting. *Get counsel review of the
  Ambriz "interceptor/capability" exposure — Proxy acts on transcript content, so its risk is above
  a passive notetaker's.*
- **Isolation + credential boundary + injection guardrail** wired and documented (these are your
  differentiators in security reviews).
- **Per-action audit log** of every agent world-touching action (PR, message, email) — doubles as
  Law-3 evidence.
- **Egress allowlist** on the sandbox (top exfiltration control buyers probe), **per-meeting cost
  cap**, **sandbox reaper** live (§2).

### Stage B — first real (security-aware) customer
- **Trust page** documenting the controls above; **DPA + full subprocessor list** (Anthropic,
  AssemblyAI, Cartesia, Recall, E2B, GCP — each with its own DPA + residency confirmed).
- **Request Anthropic Zero-Data-Retention**; confirm no-training + retention terms of each
  subprocessor and state them.
- **SOC 2 Type I** kicked off via **Vanta/Drata** (~$10–40k/yr platform + $12–40k CPA audit;
  Type II is the 3–12-month observation window) → market "**Type II in progress**." Acceptable to
  SMB buyers; enterprise hard-gates on the delivered report.
- **Pre-answered SIG-Lite / CAIQ / AI-CAIQ v1.0.2** with standing answers to the 7 grill points:
  consent, where transcripts/code live, subprocessors, autonomous-agent-on-code risk, prompt
  injection via transcript, can-the-agent-push-code (+ human gate), model provenance.
- **SSO (SAML+OIDC)** via a WorkOS-class provider at first serious inbound (gates ~$50k+ ACV).
- **CMEK per-tenant** → "delete the key = crypto-shred the tenant" (clean deletion story).

### Stage C — security-conscious / larger / regulated / EU
- **SCIM** deprovisioning; **RBAC**; immutable **SIEM-exportable audit logs**.
- **EU data residency** (EU GCP region + DPA commitment; GDPR legitimate-interest basis + DPIA).
- **Single-tenant** option + **BYOK**; **BYOC/VPC** deployment only when a specific regulated
  "code-can't-leave" deal justifies it (SOC 2 must cover the deployment model actually used).

---

## 6. Legal (sign-able minimum)

- **Doc set:** MSA/ToS, Privacy Policy, **DPA + subprocessor page** (mandatory — we're a processor),
  Acceptable Use; Order Form + mutual NDA when sales-led; SLA only if a buyer pushes.
- **Source:** **Common Paper** (or Bonterms) free standard MSA/DPA/mNDA; generate the Privacy Policy;
  publish the subprocessor page. Then **one ~$1–2k boutique flat-fee review** of the three risk
  clauses before the first signed paid deal.
- **Clauses that matter for Proxy:** customer owns their code + **no-training**; **AI-output IP
  carve-out** (US Copyright Office Jan-2025: pure AI output isn't copyrightable → assign rights to
  customer, disclaim warranties, carve AI output out of indemnity); **liability cap at 12mo fees**
  with AI-output losses excluded from "direct damages"; subprocessor flow-down; automated-agent
  disclosure + human-approval statement. *(Not legal advice — counsel reviews these three.)*

---

## 7. Billing (Stripe, margin-protected)

- **Stripe Billing** (Stripe acquired Metronome Jan 2026 → usage metering going native; staying in
  Stripe defers ever needing Orb/Lago).
- **Model:** per-seat base fee + **included per-meeting credits + metered overage**, with **usage
  caps + alerts** so a runaway sandbox loop can't outspend the customer. Per-meeting is the value
  metric (tracks bot-minutes + tokens + compute); keep raw tokens internal. Price overage with
  headroom over marginal COGS.

---

## 8. The demo / what a startup buyer needs to see

- **A live "wow" moment:** Proxy joins a real meeting, is asked a code question, answers grounded in
  *their* repo with real `file:line`, and — the differentiator Otter/Fireflies structurally can't do
  — **cites the codebase**. Then does a small live task and stages a PR.
- **The post-meeting artifact:** grounded notes (decisions with the settling line, owners table with
  `UNASSIGNED`, verified `file:line`) + typed action items delivered to Slack/email.
- **A one-page trust story:** isolation + credential boundary + human-gated actions + subprocessor
  list. Technical buyers ask for this on the first call.
- **Frictionless onboarding shown:** the 2-clicks-+-add-a-guest flow, live.

---

## 9. The phased roadmap (with gates + current-state)

Legend: ✅ real/live · 🟡 exists-but-unwired · 🔨 to build · 🔑 founder-gated.

- **Phase 0 — Foundation lock** ✅ (done): one clean trunk (`proxy-build`), dead code gone.
  Remaining: confirm push to real GitHub.
- **Phase 1 — Prove reactive on real infra** 🔑 (founder): real vendor keys, swap subscription token
  → `ANTHROPIC_API_KEY` (ToS), `terraform apply`, bake E2B template, live audio meeting.
  *Gate:* a real meeting, grounded, barge-in works.
- **Phase 2 — Ops-at-scale hardening** 🔨: sandbox **reaper** (🟡 `mark_ended`/`mark_meeting_ended`
  are orphans today), per-meeting **cost cap** (🟡 `record_cost` uncalled), **egress allowlist**
  verified live, **pause/resume + baked-template** lifecycle (§2), **injection guardrail** wired on
  the seed path, per-action **audit log**. *Gate:* 10 concurrent meetings / 3 tenants — zero
  cross-tenant leak, zero orphan VMs, cost capped.
- **Phase 3 — Onboarding spine** 🔨: GitHub App (host-side PR-write), invite-email → Recall
  join-by-URL + Tier-2 calendar-OAuth, Slack (domain-verified), own-domain email
  (SPF/DKIM/DMARC), Stripe. *Gate:* new tenant → joined, indexed, working meeting in 2 clicks +
  add-a-guest.
- **Phase 4 — Post-meeting v1** 🔨: persist meeting record at end (today teardown *deletes* it) →
  one-prompt notes + typed/owned action items → identity resolution → outreach (Slack + email
  floor) → resume/rebuild sandbox → execute one item → stage PR behind a click. *Gate:* a real
  meeting yields grounded notes + items; one item executed end-to-end behind approval.
- **Phase 5 — Trust/legal/compliance** 🔨 (parallel, mostly non-code): consent posture (Stage A!),
  trust page, DPA + subprocessors, Anthropic ZDR, SOC 2 Type I kicked off on first pilot convert.
  *Gate:* hand a buyer a security page + DPA + subprocessor list, answer their questionnaire
  same-day.
- **Phase 6 — Later** 🔨: Google Chat + Teams chat adapters; SSO/SCIM (WorkOS) on enterprise
  inbound; single-tenant/EU/BYOC on regulated demand; the **proactive** model.

---

## 10. Open decisions & founder-gated items

**Decisions for the founder:**
1. **Outreach approval granularity** (§3): organizer-approves-batch + individual-gate-for-mutations
   (recommended), vs tighter (per-message) / looser (auto-send notes to a configured channel).
2. **Warm-snapshot window length** (§2): 0h (pure rebuild-on-demand, simplest) vs 24–72h (faster
   near-term follow-ups, needs the reaper). Recommend starting at a short window once the reaper
   exists; 0h is acceptable v1.
3. **Which chat adapters ship in v1** beyond email + Slack (recommend: none until a customer needs
   Chat/Teams).

**Founder-gated (only you can do):**
- Prove reactive live (Phase 1); real vendor creds + GCP billing; swap to `ANTHROPIC_API_KEY`.
- Get E2B paused-storage per-GB rate in writing.
- Legal: engage a boutique SaaS lawyer for the 3-clause review.
- Counsel review of the Ambriz consent/interceptor exposure before all-party-state / EU pilots.
- Register the GitHub App + Slack app + sending domain; provision the bot Google account(s) for
  Tier-2 admission.

---

## Appendix — current-state ground truth (from a fresh codebase audit)

- **REAL & live:** reactive path (webhook → provision → warm → wake → respond), premeeting map
  build, multi-tenancy (`tenant_id` pervasive), Terraform/deploy stack.
- **Exists but unwired:** `db.meetings.mark_ended`, `sandbox_provider.mark_meeting_ended`,
  `record_cost` (cost metering), the reaper — orphans with zero callers.
- **Absent (to build):** all post-meeting features (persist-at-end, action items, outreach, notes
  broadcast), the proactive model.
- **Uncommitted on disk (pre-existing):** a `premeeting/{comprehension,symbol_map,understanding}.py`
  layer + `queries/` — confirm intent (commit or discard) as part of Phase 0.
