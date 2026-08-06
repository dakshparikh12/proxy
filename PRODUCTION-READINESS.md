# Proxy — Design Plan & Pathway to a Serverless Multi-Tenant Platform

**Status:** the single consolidated plan (supersedes prior drafts) · **Updated:** 2026-08-06
**Branch:** `post-meeting` · **Read with:** `SPEC.md`, `CLAUDE.md`.

This is the **design plan**: what we need to *design*, in clear chunks, in order — not locked
technical decisions. It takes us from the working-locally reactive product to a **serverless,
self-serve, multi-tenant platform any company can hop on and use securely at scale.** Calibrated
to the professional middle (what a real customer-deployable startup has — no less, no more):
enterprise features (SSO/SCIM/SOC 2/per-tenant DBs) are **planned escape hatches, built when a
big customer demands them**, not day-one work.

---

## 1. Where we are today (grounded in the code)

- **Reactive in-meeting product works** locally: webhook → provision per-meeting E2B sandbox →
  warm → wake-on-address → work → respond over Recall/Cartesia. ~20k lines, real.
- **Multi-tenant already threaded** (`tenant_id` pervasive), coherent Terraform stack exists,
  premeeting clone→understanding→store (repo_maps keyed by tenant/repo/sha) is real.
- **Missing:** any persistent per-company memory, post-meeting, the customer web app, self-serve
  onboarding UI, the operational safety layer.
- **The honest shape:** today Proxy is **body-only** — each meeting is a stateless sandbox. The
  whole gap to the vision is the **persistent brain** + the platform wrapper.

## 2. Where we're going (the north star)

With **zero manual work from us**: a company signs up self-serve → gets a securely isolated
**Proxy of their own** → connects repos + chat + meetings in a few clicks → Proxy auto-provisions
a **persistent, code-grounded company brain** → joins their meetings (one or many at once), works
live, and folds everything back into that brain → gets smarter across meetings → acts (PRs,
messages) behind a human click → secure, hosted by us, scales from one repo to many and one
company to hundreds. Adding post-meeting/proactive is then just shipping code on this foundation.

## 3. The core model (Proxy-specific — everything hangs off this)

**A company's Proxy = a durable BRAIN + an ephemeral BODY + a LEARNING LOOP.**

- **Brain (durable, one per company, lives forever):** identity + access grants + the accumulating
  knowledge (code understanding tied to real `file:line`, meeting decisions/action-items/outcomes,
  ownership, "state of the work"). Small, cheap, encrypted, isolated. In Postgres + GCS. **This is
  the product the customer buys.**
- **Body (ephemeral, one per meeting/task, thrown away):** a sandbox rehydrated from the brain +
  the repo, killed and reaped after. **This is the "instance" we generate per meeting** — not a
  literal Cloud Run job (a job is wrong for a long interactive session); it's a warm sandbox
  instance (E2B today, behind a swappable seam; GCE-VM à la Gallop is the proven alternative).
- **Learning loop (events → brain, incremental):** a **PR/push refreshes the code understanding**;
  a **meeting ends → its decisions/outcomes fold into the brain**; a **task completes → record the
  result**. Never a full rebuild — incremental updates. This *is* "gets smarter over time."

**"Keep the environment forever" = keep the BRAIN forever; the body stays disposable.** That
single split resolves persistent-vs-ephemeral and answers "instances vs. selling an agent" — the
brain is the agent you sell; the body is it temporarily embodied.

**Open granularity decision (to resolve in design, not now):** is a "Proxy" **per-company** (one
brain) or **per-team** (scoped brains within a company)? *Recommended default:* one company brain
with **team-scoped views/permissions**, and full per-team brains only if a customer needs hard
separation — keeps it simple, supports "multiple instances within a company" without N brains.

---

## 4. The design chunks (what we need to design)

Each chunk lists the **specific things to design** (the real open questions), current state, and
the professional calibration (IN now vs LATER escape hatch).

### Chunk A — Foundational contracts *(design FIRST; everything reads/writes these)*
- **The company-Proxy / tenant model:** what a tenant is, its lifecycle (provision at signup →
  live → offboard/delete), and the company-vs-team granularity above.
- **The brain / durable store:** the schema for the persistent knowledge — what's stored in
  Postgres (structured: meeting records, action items, ownership, cost, snapshot bookkeeping) vs
  GCS (blobs: understanding docs, transcripts, staged diffs). *Minimize durable raw code — keep
  only derived understanding; raw clones stay ephemeral.*
- **Cross-meeting memory:** the shape of what persists and how a meeting **retrieves the relevant
  slice** without blowing context. *Recommended:* simple structured recall first; heavier
  search/embeddings only if needed.
- **Identity & auth:** how a person logs into Proxy (**"Sign in with Google/Microsoft" → tenant by
  domain**, + domain verification) and the credential model for stored grants (encrypted).
- Calibration — **LATER:** SSO/SAML, SCIM, fine-grained RBAC (start: admin vs member).

### Chunk B — Onboarding & Access
- **The self-serve flow/UI:** the step sequence a company follows — sign in → connect code host →
  connect chat → add Proxy to meetings — with a **readiness view** (cloning → understanding →
  ready) and honest **failure states** (repo too big, clone failed, monorepo, non-GitHub host).
- **The connector seam (design once, adapters per family):** code host (GitHub first; Bitbucket/
  GitLab adapters later), chat (Slack first; Google Chat/Teams later), **email as the universal
  outreach floor**, meeting-join. Least-privilege scopes; encrypted token storage.
- **Meeting-join spine:** how Proxy gets invited — per-tenant invite email / calendar → Recall
  join-by-URL; and the reliable admission tier (signed-in bot on the invite) for locked-down orgs.
- Calibration — **LATER:** marketplace listings, additional adapters on demand.

### Chunk C — Cloud Platform & Runtime *(partial re-architecture: body → brain+body)*
- **Hosting & per-tenant isolation:** shared multi-tenant infra + strong `tenant_id` isolation
  (Stage-1, right for 0–500 customers). **Escape hatch (LATER):** per-customer GCP project
  (Gallop's `customer-platform` module) for enterprise/regulated buyers who demand it.
- **The instance model:** how a meeting **rehydrates a sandbox** from the brain + baked per-repo
  template (no cold re-clone), and **folds results back**; kill + **reaper** after.
- **Multi-repo & big-repo:** a company has N repos; a meeting **scopes to the relevant repo(s)**;
  big monorepos via **sparse/shallow clone**; understanding rebuilt on signed push.
- **Concurrency (Proxy joins many meetings at once):** many sandboxes across tenants **and many
  concurrent sessions of the SAME company's brain** — brain is the source of truth, sandboxes are
  read-through caches, **writes appended as events + reconciled** (never two VMs mutating shared
  state). Orchestrator with per-resource locks (copy Gallop's DB-state-machine + reconcile).
- **Operational essentials (IN — cheap, non-negotiable):** a real deploy (Cloud Run + Terraform,
  have); basic monitoring + error tracking + the cost telemetry you have; a **per-tenant spend
  cap**; a **sandbox reaper**; **backups on** (PITR + GCS versioning); **egress controls** on
  sandboxes; a **cross-tenant isolation test in CI**.
- Calibration — **LATER:** full observability/on-call/status-page/DR-drills, per-tenant KMS +
  rotation, schema-per-tenant / dedicated DBs (Stage 2–3).

### Chunk D — Product
- **Post-meeting:** notes + typed/owned action items → outreach (email floor + Slack) → **execute
  a task → PR behind a human click**.
- **The learning loop wiring** (§3): PR/push → refresh understanding; meeting-end → fold into
  brain; task done → record outcome. **Monitored** so we can see it working and scale it.
- **Customer dashboard:** meetings, notes, action items, **approve staged drafts**, connection
  health, billing; **notifications** (ping when a draft awaits approval — the human gate is useless
  if no one is told).
- Calibration — **LATER:** the proactive/unprompted model; product analytics; vector memory.

### Chunk E — Trust, Legal & Business *(parallel; two items gate real customers)*
- **Gate to onboarding real clients (do early):** recording-**consent** (named bot + announce);
  **DPA + subprocessor list** (E2B, Recall, AssemblyAI, Cartesia, Anthropic).
- **IN:** ToS + Privacy Policy (Common Paper + one lawyer review); a **security page** (isolation +
  credential boundary + injection guardrail); data retention + working delete/offboard (have a
  route); **Stripe billing** (per-company agent + per-meeting usage); landing page + trial.
- Calibration — **LATER:** SOC 2 (start when a deal needs it), pen-test, DSAR tooling, trust center.

---

## 5. The pathway & timeline (design order → then parallel build)

**Design in dependency order; front-load contracts so the build parallelizes.**

- **Phase 0 — Foundational contracts (Chunk A).** *Gate:* tenant/brain/memory/auth schemas agreed;
  every other chunk can build against them. *Blocks everything — do first.*
- **Phase 1 — Onboarding & Platform (Chunks B + C) in parallel.** They meet at ONE clean handoff:
  *"a provisioned tenant + stored credentials."* Nail that contract and two people build them at
  once. *Gate:* a brand-new company self-serves to a joined, indexed, working meeting; runs safely
  (spend cap, reaper, isolation test green).
- **Phase 2 — Product (Chunk D).** Post-meeting + dashboard + the learning loop on the foundation.
  *Gate:* a real meeting → grounded notes + action items; one item executed end-to-end behind a
  click; the brain visibly gets richer across meetings.
- **Parallel throughout — Trust/Legal/Business (Chunk E)**, with consent + DPA pulled early.
- **Later — proactive model + enterprise escape hatches** (per-customer project, SSO/SCIM, SOC 2)
  as specific customers demand them.

*(Calendar dates depend on your velocity — the ordering + gates are the plan; each phase is a
design pass then a build pass.)*

## 6. The specific enterprise questions — answered/captured

- **Multiple / big repos hosting:** brain holds a **derived understanding per repo** (not a
  permanent raw copy); a meeting rehydrates only the relevant repo(s) into a sandbox from a baked
  template; big repos via sparse clone; rebuilt on push. (Chunk C.)
- **"Cloud Run job per meeting?":** no — the control-plane (Cloud Run) *orchestrates*; the meeting
  runs in an **ephemeral sandbox instance** (E2B/GCE-VM), the "body." Cloud Run jobs suit batch,
  not a live interactive session. (Chunk C.)
- **Customer-to-customer separation:** `tenant_id` everywhere + per-meeting sandbox now;
  per-customer GCP project escape hatch for enterprise. (Chunk C.)
- **Proxy joining many meetings at once:** many concurrent sandboxes; the same-company concurrent
  sessions read the shared brain, writes appended/reconciled; orchestrator with locks. (Chunk C.)
- **Multiple instances of Proxy (per company / per team):** one company brain + team-scoped views
  by default; full per-team brains only on demand. (Open decision, §3.)

## 7. Open design decisions (resolve during design — not now)

1. **Proxy granularity:** company-level brain + team views (recommended) vs per-team brains.
2. **Live compute engine:** keep E2B for live (recommended) vs adopt Gallop's GCE-VM pattern —
   behind a provider seam either way, so it's swappable, not a one-way door.
3. **Memory retrieval:** simple structured recall first (recommended) vs search/embeddings now.
4. **Warm-follow-up window:** rebuild-on-demand only (simplest) vs a short snapshot window.
5. **Outreach approval granularity:** organizer approves the batch once + per-mutation gating
   (recommended) vs per-message.

---

## Appendix — reference research (in session scratchpad)
Deep briefs backing this plan: compute/persistence at scale (`research-vm-scale.md`), enterprise
meeting-agent access + consent law (`research-enterprise-access.md`), completeness audit
(`verify-completeness.md`), plus the Gallop `~/platform` infra study (proven reference architecture
we copy below-the-line: Terraform modules + per-customer-project + Packer image + runtime provision
+ idle-reaper + DB-state-machine orchestration + encrypted SCM tokens + Secret Manager).
