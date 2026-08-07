# Proxy — Onboarding & Integration

**One of two canonical docs** (the other is `CLOUD-RUNTIME.md`). Self-contained; current as of
2026-08-06. Covers how a customer goes from "interested" → onboarded → connected to all their
systems → running as a permanent tool. Principle: **rent the plumbing, build the moat, keep the
frontend thin and the backend rich; simplest thing that works for startups, scalable to enterprise.**

---

## 1. What Proxy is (for a fresh reader)
Proxy is an AI teammate that **joins a company's meetings already knowing their codebase**, works
live when addressed, and (post-meeting) turns decisions into owned action items + does follow-up
work — reaching people over Slack/email. Multi-tenant SaaS. **One company = one tenant = one durable
"brain"; each meeting spawns an ephemeral sandbox that reads that brain.** (The runtime is in
`CLOUD-RUNTIME.md`.)

## 2. The onboarding model
- **Company-centric, not per-user.** Onboarding provisions a *company's* Proxy, not an individual's.
- **Frictionless + self-serve** (also works from a cold-outreach link): net customer effort ≈
  **sign in + a few scoped OAuth clicks.**
- **Proxy operates through its OWN identity** (own-domain email + its Slack/Chat bot) and reaches
  *into* the customer's systems via **delegated OAuth** — so it never needs to read a private mailbox.

## 3. Identity & auth — **DIY for v1, no WorkOS** (the simplest proven pattern)
WorkOS's real value is enterprise SSO/SCIM/audit, which **v1 (startups) does not need**. So for v1
we DIY the two simple parts and defer the rest:
- **Login = "Sign in with Google/Microsoft"** (OAuth/OIDC via **Authlib** in Python — the exact
  equivalent of Gallop's `passport-google-oauth20`/`passport-microsoft`).
- **Sessions = signed cookie + a Postgres `sessions` table** (Proxy already has session infra).
- **Tenant/org model = our own Postgres tables** (`tenants` (exists) + `members(user_id, tenant_id,
  role)`). On first sign-in, derive the company from the **verified email domain** → find/create the
  tenant → create the member. **First user for a domain = admin; others = member** (two roles, a
  column — no RBAC engine).
- **Membership default:** auto-join by verified domain (frictionless); admin-approve is the tighten-
  later option.
- **Deferred to enterprise:** SSO/SAML, SCIM, audit-log streaming, an admin portal — add **WorkOS**
  (free ≤1M users, drop-in) only when a specific enterprise demands them.

This is *the* default B2B pattern ("Sign in with Google + DB sessions + tenants/members") — boring,
proven, and exactly what Gallop runs while serving enterprise customers.

## 4. Tool connections — Composio + our own apps (per-tenant, isolated)
- **We register ~4 of our OWN OAuth apps once, globally:** the **GitHub App** (repos), **Slack**,
  **Google**, **Microsoft**. Composio's "custom auth" *operates* them — so the customer's consent
  screen shows **"Proxy"** and we control least-privilege scopes. (A per-service grant, not one
  blanket grant. No product mints your branded apps for you — but you register each once, ~30 min.)
- **Composio** handles the per-customer connect flow, encrypted token vault, refresh, webhooks, and
  **per-tenant isolation via `user_id` = tenant**: each customer's token is workspace-scoped and
  stored under their `user_id`; 10 companies = 10 isolated tokens; outbound scoped by `user_id`,
  inbound events arrive on one signed webhook mapped back to the tenant. **The sandbox holds no
  tokens** — the host invokes Composio-scoped tools (the credential boundary).
- **GitHub is special:** we use our own **GitHub App** for the clone + host-side PRs (the
  differentiated path); Composio covers the breadth (Slack/Chat/Google/Microsoft/Jira/Linear/Notion).
- **Google/Microsoft apps** need a one-time *light* verification (we **send**, never read mailboxes →
  non-restricted scopes, no CASA audit). A directly-installed Slack app needs no marketplace review.

## 5. Meeting join — Recall Calendar (default) + invite-email (fallback)
- **Default = Recall Calendar OAuth.** Customer connects their Google/Microsoft calendar in
  onboarding → **Recall auto-detects meetings and auto-joins the bot ~2 min before start** (`join_at`;
  Recall reserves the machine) → reschedule/cancel handled via Recall webhooks. **We run no
  scheduler.**
- **Fallback = the per-tenant invite email** (any provider / no calendar): they add
  `acme@useproxy.co` to the invite → we parse the `.ics` → set `join_at` on Recall.
- **The meeting URL is the provider-agnostic key** — Recall joins Zoom/Meet/Teams/Webex by URL, and
  we get that URL from the calendar (Recall) or the parsed invite. Upcoming meetings live in an
  `upcoming_meetings` Postgres row `{tenant, source, start_at, meeting_url, recall_bot_id, status}`.
- **All platforms supported in v1** (free — Recall joins any by URL).

## 6. Email — per-tenant address on one catch-all domain
- We own `useproxy.co` with a **catch-all inbox → one webhook**. **Each tenant gets a unique address**
  (`acme@useproxy.co`) = the routing key (local-part → tenant). Bot display name is "Proxy".
- **Inbound** via Cloudflare Email Routing / Postmark → webhook. **Outbound** (notes/outreach) via
  **Resend** from the per-tenant address; replies return to the same address → same tenant.

## 7. The onboarding flow (thin frontend, rich backend)
1. **Sign in** (Google/Microsoft) → company tenant + Proxy provisioned (info collected: work identity
   + company name only).
2. **Connect code** — install the GitHub App, pick specific repos → clone + understanding begins.
3. **Connect workspace + calendar** — Slack/Chat + calendar via Composio/Recall (one click each).
4. **Index + PROVE it worked** — Proxy answers a repo question / posts a finding *before* the first
   meeting (time-to-value).
5. **Team access** — members auto-join by domain; admin/member roles.
6. **Go live** — Recall joins the next meeting; Proxy works; folds results back.
7. **Persist** — a standing bot + GitHub App + brain + console; expand repos/seats over time.

## 8. The console (one company workspace, role-scoped)
One shared company console with role-scoped views (admin sees all; member sees own). Sections:
company profile · **connections + health** (per-integration status + reconnect) · members & roles ·
activity/monitoring · **controls** (the human-approval gate for world-touching actions) · billing
(later). **The onboarding steps fill the console.** Enterprise-selling trio: capabilities ·
transparency (activity) · frictionless control.

## 9. Security & consent (the parts that gate real customers)
- **Recording-consent floor (legal):** the bot **joins named + announces transcription**; never
  silent. Required for all-party-consent US states + EU. *(Exact mechanism = an OPEN decision:
  named bot + a join announcement + a host consent setting + a jurisdiction default.)*
- **Credential boundary:** OAuth tokens encrypted host-side; the sandbox holds none; every
  world-touching action (PR, message) is a **staged draft executed host-side after a human click**.
- **Delete/offboard:** revoke tokens + purge the tenant's data + kill sandboxes (to build).
- **DPA + subprocessor list + ToS/Privacy** (Common Paper templates) before a paying customer.

## 10. What Recall already does for us (don't build)
- **Meeting scheduling / join-at-time** (Calendar V2 + `join_at`).
- **Action-item owner → email resolution** (Recall recovers participant emails via calendar
  fuzzy-matching — solves "who do I message" for free).
- **Bot/meeting state** (consume `bot.*` webhooks: joining, waiting-room, permission-denied, ended).
- **Raw transcript/recording storage** (or `retention:null` for zero-retention).

## 11. Current state vs. to-build (honest)
- **Real today:** the GitHub connect → index trigger; session/tenant infra; the in-meeting agent.
- **To build (v1):** Google/MS sign-in wiring + `members` table; the ~4 OAuth apps + Composio
  custom-auth configs; the per-tenant email routing + inbound webhook; the connect page + console;
  the recording-consent mechanism; the delete/offboard path.
- **Deferred to enterprise:** WorkOS (SSO/SCIM/audit), per-tenant KMS, marketplace listings.

## 12. Locked decisions (onboarding)
Auth: **DIY Google/MS sign-in, no WorkOS for v1** · Connectors: **Composio custom-auth**, our own
GitHub App · Calendar: **Recall OAuth default**, invite-email fallback · Email: **per-tenant address**
· Membership: **auto-join by verified domain** · Wake: **voice + chat** (verify chat-wake is wired).

## Cost (onboarding-related)
Composio free 20k tool-calls → $29/mo · Recall $0.50/recording-hr (+ free calendar API) · Resend free
→ $20/mo · WorkOS $0 (deferred). The heavy costs are LLM tokens + compute — see `CLOUD-RUNTIME.md`.
