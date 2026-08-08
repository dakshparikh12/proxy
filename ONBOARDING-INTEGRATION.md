# Proxy — Track B: Onboarding & Integration (the front door)

> **This is the kickoff brief for the Track-B design deep-dive.** It is self-contained: the mission,
> where we are, where we need to be, the frozen seam we share with Track A, what Track A is doing, and
> the buildable path. Sibling doc: `CLOUD-RUNTIME.md` (Track A). Master index: `ROADMAP.md`. Product
> source of truth: `SPEC.md`. Current as of 2026-08-07.

## Mission (applies to both tracks)
Proxy is a **serverless, multi-tenant SaaS on GCP** that **works for any customer size**, **runs fully
on the cloud** (nothing on a laptop, nothing manual per meeting), and is **secure by construction**.
**Gallop (`/Users/daksh/platform`) is the source of reference — copy its proven mechanics; diverge only
where our ICP or our laws demand it, and say so.** The bar is **the simplest, easiest path that still
scales to the best end-state.** **V1 targets startups** (don't overcomplicate — DIY the two simple auth
parts, closed-by-default, boring proven patterns); the design must extend cleanly to enterprise
(SSO/SCIM, per-customer isolation) later without a rewrite.

## What Track B owns (and what it does NOT)
**Owns:** the front door — how a customer goes from "interested" → signed in → identity/org →
connected to all their systems → a consent policy chosen → running as a permanent tool, plus the
console. It **writes** the tenant/member/connection/repo/upcoming-meeting rows and sets the consent
policy (see **The Seam**), and **reads** the brain for the console. **Does NOT own:** the meeting
runtime, spawn, persistence, or hosting — that's Track A. Track B is fully buildable and testable
against a **stub runtime** + seeded brain rows — it never needs live meetings to exist.

---

## 1. What Proxy is (for a fresh reader)
An AI teammate that **joins a company's meetings already knowing their codebase**, works live when
addressed, and (post-meeting) turns decisions into owned action items + does follow-up work — reaching
people over Slack/email. Multi-tenant SaaS. **One company = one tenant = one durable "brain"; each
meeting spawns an ephemeral body that reads that brain** (the runtime is Track A). Proxy operates
through its **OWN identity** (own-domain email + its Slack/Chat bot) and reaches *into* the customer's
systems via **delegated OAuth** — so it never reads a private mailbox.

## 2. Where we are (Track B "you are here")
- **Real today:** the GitHub connect → index trigger; session/tenant infra; the in-meeting agent (Track
  A). The **consent gate core is built** (`transport/consent.py` + `join.py`).
- **Current code diverges — fix first:** `libs/db/.../identity.py` creates a **new tenant per email
  address** (named after the full email), so two colleagues at one company get **separate, disjoint
  tenants**. The **domain→org rewrite** (find/create the company tenant → add a `members` row, first =
  admin, closed-by-default) is the **first Track-B build task.** *(Corroborated in code.)*
- **Not yet built (this track's work):** the domain→org identity rewrite + `members(role,status)`; the
  `connections` integration-record table + Composio adapter behind the seam; the HMAC signed-origin
  webhook verifier; Google/MS sign-in wiring; the ~4 OAuth apps at least-privilege scopes; per-tenant
  email routing + inbound webhook; the connect page + console; the consent-policy selector (default
  closed); the per-platform readiness UI; the `audit_log` writer wiring; the delete/offboard path.

## 3. Where we need to be (Track B end goal)
Any company — a 5-person startup or a 100-engineer org — can be brought from sign-in → connected repo +
calendar + Slack → a chosen consent policy → a first grounded answer, entirely through the hosted UI,
with **admin-controlled membership**, least-privilege scopes, and a trust story a security-conscious
VP-Eng signs off on. Self-serve-*capable*, delivered founder-assisted first (§7).

---

## 4. The onboarding model
- **Company-centric, not per-user.** Onboarding provisions a *company's* Proxy, not an individual's.
- **Onboarding motion is phased, not zero-touch.** **Phase 1 = founder-assisted** through the real UI
  (early customers need consent-policy selection, GitHub-org-owner approval, a bot test-join, admission
  troubleshooting, repo-scope confirmation, and the first grounded-answer proof — not "a few clicks").
  **Phase 2 = assisted self-serve** once repeatable. **Phase 3 = self-serve.** Thin frontend from day
  one; we don't *claim* zero-touch before it's real. (Gallop's own onboarding is a reviewed, assisted
  ops sequence — this matches.)
- **Proxy operates through its OWN identity** — own-domain email + its Slack/Chat bot; delegated OAuth
  into customer systems; never reads a private mailbox.

## 5. Identity & auth — DIY for v1, no WorkOS (copy Gallop's pattern)
WorkOS's real value is enterprise SSO/SCIM/audit, which **v1 (startups) does not need.** Gallop itself
DIYs this, so we copy it:
- **Login = "Sign in with Google/Microsoft"** via **Authlib** (the Python equivalent of Gallop's
  `passport-google-oauth20`/`passport-microsoft`). Identity key = **`(email, provider)`**; a **TOFU
  `provider_id` anchor** locked on first login (Gallop `passport.ts`).
- **Sessions = signed cookie + a Postgres `sessions` table** (`connect-pg-simple` equivalent);
  `httpOnly` + `sameSite=lax`; fail-fast `SESSION_SECRET`; a CSRF header guard. No JWT for user sessions.
- **Membership = closed-by-default (never auto-join).** **The organization is authoritative; the
  verified email domain is only evidence.** On first sign-in the founder/first user **creates the org
  and is its admin**; later same-domain users are **suggested/pending members an admin approves** (or an
  admin invites) — **never auto-joined.** This is Gallop's literal practice (OAuth *resolves* an existing
  user, never *creates* one; `domain` is only an allow-list constraint on who an admin may add). Two
  roles (admin/member), a `status` column — no RBAC engine. Auto-join by domain is a later
  **admin-enabled opt-in**, not the default — because contractors, vendors, subsidiaries, ex-employees,
  and compromised accounts share domains, and our ICP treats silent auto-join as a trust failure.
- **Deferred to enterprise:** SSO/SAML, SCIM, audit-log streaming, an admin portal — add **WorkOS**
  (free ≤1M users, drop-in) only when a specific enterprise demands them.

## 6. Tool connections — Composio adapter + our own apps (per-tenant, isolated)
- **We register ~4 of our OWN OAuth apps once, globally:** the **GitHub App** (repos), **Slack**,
  **Google**, **Microsoft**. Composio's custom auth *operates* them → the consent screen shows **"Proxy"**
  and we control least-privilege scopes.
- **Composio is a credential/connectivity adapter behind a seam — NOT our tenancy model.** **Proxy owns
  the authoritative integration record** (the `connections` row, see §12) and Composio connection IDs are
  merely **references** stored against it. Composio handles the connect flow, encrypted token vault,
  refresh, and outbound scoped calls; but **every inbound event is verified by signed origin and mapped
  to a tenant through OUR `connections` record — a Composio `user_id`/entity ID is never trusted as
  tenant identity** (routing a tenant off a payload field is a P0 breach). Per-tenant creds are stored
  AES-256-GCM with **domain-separated keys** (Gallop `aesGcm.ts`). **The sandbox holds no tokens** — the
  host invokes Composio-scoped tools; the sandbox gets only short-lived, scoped task credentials.
- **Least-privilege scopes, stated on the trust page.** **Slack** = `chat:write`, `users:read`,
  `im:write`, + `app_mentions:read` only if we support Slack invocation (`im:history` *only* if we must
  process DM replies) — **never** channel/private history, workspace-wide read, search, or files.
  **Google/Microsoft** = send-only mail + calendar-read (no mailbox read). The delivery/review product
  does not need Slack history.
- **GitHub is special:** our own **GitHub App** for the clone + host-side PRs (the differentiated path);
  Composio covers breadth (Slack/Chat/Google/MS/Jira/Linear/Notion). The install is stored so Track A can
  fetch a clone token host-side (Seam §12.3).
- **App verification:** Google/MS need a one-time *light* verification (we **send**, never read mailboxes
  → non-restricted scopes, no CASA audit). A directly-installed Slack app needs no marketplace review.

## 7. Meeting join — Recall Calendar (default) + invite-email (fallback)
- **Default = Recall Calendar OAuth.** Customer connects their Google/MS calendar → **Recall discovers
  meetings and schedules the bot join ~2 min before start** (`join_at`) → reschedule/cancel via Recall
  webhooks. **Recall owns the calendar→bot-join scheduling layer; we run no calendar poller** — but
  Track A's orchestrator still owns meeting admission/consent/spend-cap/claim on every join. Onboarding's
  job is only to establish the calendar connection and write `upcoming_meetings` rows.
- **Calendar scope is sensitive data — disclose precisely.** The grant reads upcoming event metadata
  (titles, attendees, times, descriptions, links, recurrence). The UI states exactly: *"Proxy reads
  upcoming calendar events only to detect and join selected meetings. Proxy does not read email,
  documents, or Slack message history,"* plus retention (what's stored — title/time/URL for scheduled
  meetings only — for how long, and how to disconnect).
- **Fallback = a per-tenant invite address** (any provider / no calendar): add the tenant's address to
  the invite → parse the `.ics` → set `join_at`. That address is a **routing key** (local-part → tenant),
  proven and safe. It is **NOT** assumed to be the bot's signed-in Meet identity — whether a Workspace
  alias/catch-all satisfies Meet lobby-skip is an **unproven admission question to test before promising
  it**; until green, the bot joins with Recall's managed identity, with a neutral pooled address (e.g.
  `proxy-atlas@useproxy.co`) as fallback.
- **Platform support is honest, not "all-by-URL."** *Joining* by URL ≠ *supporting* a platform
  (admission, signed-in requirements, chat/DM, consent UX, transcript, output audio, host controls all
  differ; Meet has no private DMs, Zoom allows more, Teams heavier). **V1 = Google Meet first; best-effort
  Zoom/Teams; per-platform capabilities shown honestly in the readiness UI.**

## 8. Email — per-tenant address on one catch-all domain
- We own `useproxy.co` with a **catch-all inbox → one signed webhook**. **Each tenant gets a unique
  address** (`acme@useproxy.co`) = the routing key (local-part → tenant). Bot display name "Proxy".
- **Inbound** via Cloudflare Email Routing / Postmark → webhook (mapped to tenant through our record,
  not a header). **Outbound** (notes/outreach) via **Resend** from the per-tenant address; replies return
  to the same address → same tenant.

## 9. The onboarding flow (thin frontend, rich backend; Phase-1 = founder-assisted)
1. **Sign in** (Google/MS) → the first user **creates the company org + is admin**; Proxy provisioned.
2. **Connect code** — install the GitHub App, pick specific repos → clone + understanding begins (Track A
   indexes).
3. **Connect workspace + calendar** — Slack/Chat + calendar via Composio/Recall (one scoped grant each).
4. **Choose a consent policy** (required; default closed) + **index & PROVE it worked** — Proxy answers a
   repo question / posts a finding *before* the first meeting (time-to-value; this step is the first
   place Track A and Track B converge).
5. **Team access** — admin invites/approves members (domain is evidence, not auto-join); admin/member.
6. **Go live** — Recall joins the next meeting; Proxy works; folds results back (Track A).
7. **Persist** — a standing bot + GitHub App + brain + console; expand repos/seats over time.

## 10. The console (one company workspace, role-scoped)
One shared company console, role-scoped (admin sees all; member sees own): company profile ·
**connections + health** (per-integration status + reconnect) · members & roles · activity/monitoring
(reads the brain) · **controls** (the human-approval gate for world-touching actions) · billing (later).
The onboarding steps fill the console. Enterprise-selling trio: capabilities · transparency · frictionless
control.

## 11. Security & consent (the parts that gate real customers)
- **Recording-consent floor, CLOSED default:** the bot **joins named + announces**; never silent
  (all-party-consent US states + EU). Core is **built** (`consent.py` + `join.py`). **The safe default is
  settled, not open: with no policy selected, Proxy may join + announce only — no recording,
  transcription, analysis, voice, or proactive behavior.** The admin **must select a consent policy
  during onboarding** (required step); we ship a jurisdiction-aware default they can tighten. Track A
  enforces the policy at admission (Seam §12.1).
- **Credential boundary:** OAuth tokens encrypted host-side (AES-GCM, domain-separated keys); the sandbox
  holds none; every world-touching action (PR, message) is a **staged draft executed host-side after a
  human click**.
- **Audit:** wire a shared server-side `audit_log` writer into every mutation (the table exists; the
  writer doesn't — we build it).
- **Delete/offboard:** revoke tokens + purge the tenant's rows + GCS prefix + kill bodies.
- **DPA + subprocessor list + ToS/Privacy** (Common Paper templates) before a paying customer.

## 12. THE SEAM — the shared contract with Track A (FROZEN; build against this)
> **Track A and Track B meet ONLY here. This block is identical in both docs. Freeze it first — then
> Track B develops against a STUB runtime and Track A against SEEDED rows, and they never touch again.**

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
   CLOSED** (join + announce only). **Track B sets it (the required onboarding step); Track A enforces
   it before the body does anything observable.**
2. `get_scoped_token(tenant_id, provider, scope, resource_id) → short-lived token` — the **credential
   boundary.** Track B stores the long-lived cred in `connections` (AES-256-GCM, domain-separated keys);
   Track A requests a short-lived, resource-scoped token (DB-persisted expiry + atomic claim; only token
   *hashes* in DB). **The sandbox never holds a long-lived or push/send credential.**
3. `get_repo_clone_token(repo_id) → host-side GitHub-App install token` — **Track B stores the install**;
   Track A clones host-side and hands the body a read-only checkout (no push cred).

**Inbound events (Recall/GitHub/Composio):** every webhook is verified by **signed origin (HMAC +
`timingSafeEqual`)** and mapped to a tenant through OUR `connections`/`repos`/`upcoming_meetings` record
— **NEVER a payload field.** (Gallop is pull-based, no verifier to copy — we build this. Track B owns
building the verifier since it owns the `connections` record it maps through.)

**Who writes / who reads:** **Track B WRITES** `users/tenants/members/connections/repos/upcoming_meetings`
and sets `consent_policy`; **Track A READS** them to spawn bodies + join meetings and **WRITES the brain**,
which Track B's console READS.

## 13. Track B build phases (buildable, ordered — roadmap Phase 2)
1. **Domain→org identity rewrite** (fix `identity.py`) + `members(role,status)` + closed-by-default
   admin-approve/invite.
2. **`connections` table (Proxy-owned tenancy)** + Composio adapter behind the seam + the **HMAC
   signed-origin webhook verifier** (map to tenant via our record).
3. **Google/MS sign-in** (Authlib) + Postgres sessions + TOFU anchor + CSRF guard.
4. The **4 OAuth apps** at least-privilege scopes (Slack/Google/MS + the GitHub App install storage).
5. **Per-tenant email routing** (catch-all → signed webhook → tenant) via Cloudflare/Postmark + Resend.
6. **Connect page + console** (connections+health, members/roles, controls, activity); **consent-policy
   selector** (default closed); **per-platform readiness UI** (Meet-first).
7. **`audit_log` writer** wired into mutations; **delete/offboard** path.
*Done when: a founder-assisted onboarding takes a new company from sign-in → connected repo + calendar +
Slack → chosen consent policy → first grounded answer, entirely through the hosted UI — writing every
Seam row Track A reads.*

## 14. Locked decisions (Track B)
Auth: **DIY Authlib Google/MS sign-in + Postgres sessions, no WorkOS for v1** (WorkOS = enterprise) ·
Membership: **admin-approve / invite, never auto-join (domain = evidence; org authoritative; auto-join is
a later admin opt-in)** · Connectors: **Composio custom-auth behind a seam; Proxy owns the `connections`
tenancy record** · GitHub: **our own App** (clone + host-side PRs) · Calendar: **Recall OAuth default**,
invite-email fallback (routing key, NOT a signed-in identity until tested) · Platforms: **Meet-first**,
best-effort Zoom/Teams, honest readiness UI · Email: **per-tenant address** on one catch-all domain ·
Consent: **default CLOSED, admin selects a policy in onboarding** · Scopes: **least-privilege, on the
trust page** · Wake: **voice + chat** (verify chat-wake is wired).

## 15. Cost (onboarding-related)
Composio free 20k tool-calls → $29/mo · Recall $0.50/recording-hr (+ free calendar API) · Resend free →
$20/mo · WorkOS $0 (deferred). Heavy costs (LLM tokens + compute) are Track A — see `CLOUD-RUNTIME.md`.

## 16. What Track A is doing (so this chat knows the boundary)
Track A builds the **cloud runtime**: control plane, per-tenant brain, ephemeral bodies, the in-meeting
loop, spawn from a golden image, git-mirror-to-GCS, the 4 ops-safety fixes, and the deploy pipeline. **It
READS the rows you write (§12) to spawn bodies + join meetings, and WRITES the brain your console reads.**
You do not wait on it — build against a stub runtime + seeded brain rows; the two converge only at
onboarding step 4 ("prove it worked") and step 6 ("go live").
