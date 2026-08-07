# Proxy — Onboarding & Integration (definitive process)

**Status:** designed + tool-validated (2026-08-06). Scope: everything from "a company is
interested" → fully onboarded, integrated, and running as a permanent tool. The cloud/runtime
("how Claude runs per-tenant + multi-meeting spawning") is a separate pass.

Calibrated for a **startup-first** motion that scales to enterprise on the same rails. Principle:
**rent the plumbing, build the moat, keep the frontend thin and the backend rich.**

---

## 1. Core model
- **One company = one WorkOS Organization = one tenant (`tenant_id`) = one Proxy "brain."**
  Company-centric, never per-individual.
- **Brain** (durable, per company, forever) + **body** (ephemeral sandbox per meeting) + a
  **learning loop** (PR/push + meeting-end events keep the brain current).

## 2. The stack — rent vs build
| Layer | Tool | Role |
|---|---|---|
| Identity / org / SSO / SCIM / RBAC / FGA / audit / admin portal | **WorkOS** | the identity + authorization + audit control plane |
| Third-party tool connections + inbound events (per-tenant) | **Composio** | Slack/Chat/Teams/Google/Microsoft/Jira/Linear/Notion/Docs/email |
| Code clone + repo work + PRs | **GitHub App (ours)** | in-house (credential boundary) |
| Meeting join + calendar detection | **Recall** | |
| Proxy's own-domain email | **Resend** | send + receive replies |
| Ephemeral code sandbox | **E2B** | the "body" (cloud pass) |
| Reasoning | **Claude** | the brain's cognition |
| **The moat (BUILD)** | — | the per-company brain, agent loop, live-meeting core, GitHub clone/repo-map, human-gate, webhook router |

## 3. The OAuth apps WE own (register once, Composio operates)
No product mints your branded apps — you register each in the platform's dev console (≈30 min
each, one-time, global), then Composio (custom auth) operates them per-customer.

| App | We register in | Used for | Operated by |
|---|---|---|---|
| **GitHub App** | GitHub dev settings | clone (read) + PRs (write, human-gated) | **us** (install token host-side) |
| **Slack app** | api.slack.com | bot: post/DM/read + events | Composio |
| **Google OAuth client** | Google Cloud console | Chat, Calendar, Gmail-send, Docs | Composio |
| **Microsoft Entra app** | Entra/Azure portal | Teams, Outlook, calendar | Composio |
| *(optional, on demand)* Notion / Jira / Linear | each platform | tickets/pages | Composio |

- **Per-service, least-privilege:** each is its own grant with minimal scopes (what security teams
  check). No blanket "everything" grant.
- **Verification:** Google + Microsoft apps need a one-time app verification — the *light* kind,
  because Proxy **sends** and never **reads private mailboxes** (non-restricted scopes; avoids the
  annual CASA audit). A directly-installed Slack app needs no marketplace review.

## 4. One-time setup WE must do to be integration-ready (the actionable list)
1. Register the **GitHub App** (`contents:read`, `pull_requests:write`; per-repo install).
2. Register the **Slack app** (bot scopes + event subscriptions).
3. Register the **Google OAuth client** (Chat/Calendar/Gmail-send/Docs scopes) + consent screen +
   submit light verification.
4. Register the **Microsoft Entra app** (Teams/Mail.Send/Calendars) + admin-consent config.
5. **Composio:** create a **custom-auth config per service** (paste our client IDs/secrets),
   set the redirect URI, build on the `link()` connect flow (not deprecated `initiate()`).
6. **WorkOS:** wire AuthKit (login), the Organization model (= tenant), and turn on SSO / SCIM /
   RBAC / FGA / Audit Logs / Admin Portal.
7. **Resend:** own sending domain + SPF/DKIM/DMARC.
8. **Recall:** bot + calendar API.
9. Build **one signed webhook endpoint** to receive Composio triggers (map event → tenant).
10. Build the **connect page** (Next.js) rendering the WorkOS + Composio hosted flows.

## 5. The per-customer onboarding flow (8 steps · thin frontend, rich backend)
| # | Step | Frontend (mostly rented) | Backend (ours) |
|---|---|---|---|
| 1 | **Register the company** | WorkOS AuthKit sign-in (Google/Microsoft SSO) | derive company from verified domain → create WorkOS Org + tenant + **provision the Proxy brain**. *Info collected: work identity + company name only.* |
| 2 | **Profile + admin** | one confirm screen | confirm name/domain/admin; domain ties future members + meetings to this tenant |
| 3 | **Connect code (scoped)** | GitHub App install (pick specific repos) | store install token host-side; **start clone + understanding** |
| 4 | **Connect workspace (scoped)** | Composio Connect UI (Slack/Chat + calendar) | store per-tenant connected accounts (`user_id`=tenant) |
| 5 | **Index + PROVE it worked** | status surface + a "here's what I understood" result | background index → **Proxy answers a repo question / posts a finding** (the "aha" before any meeting — time-to-value) |
| 6 | **Team access** | members list / roles | members via manual or SSO/SCIM; roles via WorkOS RBAC/FGA (who may approve Proxy's actions) |
| 7 | **Go live** | "invite Proxy / connect calendar" | Recall joins the meeting → spawn a body from the brain → work → deliver |
| 8 | **Persist** | console home | standing bot + GitHub App + brain; expand repos/seats over time |

**Startup path:** self-serve, first admin sign-in establishes the org — value in the first meeting.
**Enterprise path (same rails, added gates):** authorized-admin + **domain-claim/verification**
before the org is trusted (not "first-sign-in wins"), SSO enforcement, SCIM, admin-approved
tool installs.

## 6. Auth & multi-tenant mechanics (how isolation actually works)
- Company A's admin clicks "Connect Slack" → Composio hosted OAuth → Slack issues a
  **workspace-scoped token** → stored as a **connected account under `user_id = tenant_A`** (encrypted).
- **Outbound:** Proxy calls a tool with `user_id = tenant_A` → Composio uses *that* token → lands
  in Company A's workspace. Company B's token is unreachable in that call.
- **Inbound:** replies/comments arrive on our **one signed webhook**; the payload's connected-account
  identifies the tenant → routed to the right brain.
- **10 companies = 10 isolated tokens**, each under its own `user_id`. No mixing possible.
- **Two roles:** *we* create the apps once; the *customer's admin* approves each connection —
  gated by their own Slack/GitHub admin rights (their platform enforces "who may grant access").
- **Credential boundary:** the sandbox holds **no** tokens; the host executes Composio-scoped calls.

## 7. Security & admin model
- Work identity via SSO (domain-verified employee); connection grants gated by the customer's own
  platform admin controls; (enterprise) domain verification + SSO + SCIM = authoritative membership.
- **WorkOS RBAC/FGA = the human-control gate** (every staged action-behind-a-click is an FGA check).
- **Audit Logs** (+ SIEM streaming) = the immutable "what Proxy did / what admins changed" trail.
- **Offboarding/exit (build this):** revoke tokens, purge indexed code + transcripts + brain,
  confirm deletion — a security-review requirement.

## 8. The post-meeting loop (what "integrated" delivers downstream)
- **Claude** — action items, recipients, plans, interpret replies.
- **Human-gate (us)** — approve before any world-touching action (Law 3).
- **Composio** — execute: send Slack/email, create Jira/Linear tickets, write Docs, post PR comments.
- **E2B** — the actual code work (in-house); result posted out via Composio/GitHub.
- **Composio triggers → our webhook** — receive replies/comments → route to meeting context → loop.
- **Covered end-to-end** for Slack/email/Jira/Linear/Notion/Docs/GitHub.
- **Two caveats to design around:** Microsoft Teams can *send* but not *receive* replies via Composio
  (MS Graph fallback); email replies are delayed ~15 min (polling). Slack/tickets/GitHub are realtime.

## 9. Persistence (permanent standing tool)
Always-present Slack bot + GitHub App on repos · a persistent per-tenant brain (Postgres `tenant_id`
+ per-tenant GCS prefix) that keeps learning · a console they return to · SSO/SCIM binding so it
survives employee churn.

## 10. The console (one company workspace, role-scoped)
One shared company console with **role-scoped views** (admin sees all; member sees own). Sections:
company profile · connections + health · members & roles · activity/monitoring · controls (the
human-gate) · audit · security (SSO/SCIM via WorkOS Admin Portal) · billing (later). Proxy's presence
= the console + the Slack bot. The onboarding steps fill the console directly. Enterprise-selling
trio: **capabilities · transparency (audit/activity) · frictionless control.**

## 11. Enterprise-readiness — what this covers vs. the remaining gate
Enterprise-readiness = **① identity/access (WorkOS+Composio — done here) · ② secure cloud/data
(next pass) · ③ trust/compliance (mostly deferred, non-optional for big enterprise).**
WorkOS+Composio cover ~half (①). The other half is **ours and no tool provides it:** SOC 2, DPA +
subprocessor list (note: WorkOS/Composio/E2B/Recall/Anthropic all become disclosed subprocessors),
data residency, retention/deletion, pen test, documented AI-safety controls (no-training via
Anthropic ZDR, egress control, prompt-injection guardrail, human-in-the-loop), SLA/status page.
Startups/pilots don't need most of ③ yet; a big enterprise does.

## 12. Cost (rough)
Fixed ≈ **$200–400/mo** early (E2B $150 + GCP $50–100 + mostly-free SaaS tiers). WorkOS free ≤1M
users; **SSO/SCIM $125→$50/connection** (enterprise-revenue-funded). Composio free 20k calls →
$29/mo (per-tool-call; pricing changes Aug 15 2026 — budget per meeting). Per-meeting COGS ≈
**$1.50–5.50**, Claude-token-dominated.

---

**One-sentence flow:** admin signs in with their work identity → we provision their tenant + Proxy
→ they connect their systems with a few scoped OAuth clicks (our apps, operated by Composio,
isolated per tenant) → Proxy indexes and *proves* it understood → the team gets role-based access →
Proxy goes live in meetings and runs the full post-meeting loop (Claude + Composio + E2B,
human-gated) → and it persists as an always-on, continuously-learning tool with a console.
