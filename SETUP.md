# Proxy — Provisioning & Setup Checklist
### Everything a human must set up so the build loop constructs Doc 00 (the foundation) and runs cleanly. Derived from `product/v0-spec/00-FOUNDATION.md` §4–§13 + `config-defaults.toml`.

## The one framing that saves you money and time
Most of what Doc 00 names is what the **product needs at runtime**, not what the **build loop needs to write and test the code**. Provision in three tiers, in order — don't buy Tier B/C before you need them:

- **Tier A — build & unit/contract-test the foundation code.** Almost nothing external. This is where the loop spends most of its time (writing `libs/*`, `services/*` skeletons, the contract registry, the durable-ops substrate, migrations) and proving them with `ruff/mypy/pytest` + testcontainers Postgres. **Blocker to start: Tier A only.**
- **Tier B — the pre-build validation spike (Doc 00 §16 step 0.5) + deploying the foundation.** This is the first time real external infra is required: real Cloud Run + real E2B + real STT/TTS + Anthropic, to measure the sub-2s grounded answer and ORM blast-radius accuracy. Provision Tier B **before the spike**, which runs before the substrate is committed.
- **Tier C — running the full product.** Everything else (Recall production bots, Nango, GitHub App, Google OAuth, the connect-page domain). Needed to actually join a real meeting, not to build the foundation.

---

## TIER A — local build prerequisites (do first; unblocks the loop)
- [ ] **Python 3.12** and **uv** (`curl -LsSf https://astral.sh/uv/install.sh | sh`). The repo is a uv workspace (Doc 00 §3).
- [ ] **Docker** — for `testcontainers` Postgres in the test suite (Doc 00 §11). No local Postgres install needed if Docker is present.
- [ ] **git** + the GitHub repo (already: `dakshparikh12/proxy`).
- [ ] **Node + pnpm** — only for `apps/*` (connect + tile) Vite builds (Doc 00 §3); not needed for the backend/foundation.
- [ ] **`.env` created** from `.env.example` (below). For Tier A you only need: `DATABASE_URL` (local Postgres) + `SESSION_SECRET`. The boot gate (§6) fails fast on missing required keys, so fill these before running the server.
- [ ] (If the loop's own Claude Code sessions need it) an **Anthropic API key** — but note the *product's* `ANTHROPIC_API_KEY` is a runtime secret, only exercised by the spike/tests that actually call a model. The foundation's own tests (contract registry, db, ops) make **zero** model calls.

**Provable at end of Tier A (Doc 00 §16 step 1):** CI green (`ruff`+`mypy`+pytest), the container self-migrates (Alembic advisory-lock) and serves `/health`, `assert_registry_closed()` passes, a meeting-harness `operation_run` heartbeats and self-reaps. All of this is local/CI — **no cloud required.**

---

## TIER B — external accounts & API keys (for the spike + deploy)
Create each account, grab the key, put it in `.env` (local) and later GCP Secret Manager (cloud). One-per-domain AES keys are generated locally with `openssl rand -hex 32`.

| # | Service | What it's for | Where | `.env` var(s) |
|---|---|---|---|---|
| 1 | **Anthropic** | the Agent SDK (Scribe, orchestrator, workroom) | console.anthropic.com → API Keys | `ANTHROPIC_API_KEY` |
| 2 | **Recall.ai** | meeting bot join + per-speaker audio + Output Media + chat | recall.ai dashboard | `RECALL_API_KEY` |
| 3 | **AssemblyAI** | STT (Universal-Streaming), **BYOK** | assemblyai.com | `ASSEMBLYAI_API_KEY` — **also paste into the Recall dashboard** (passthrough) |
| 4 | **Cartesia** | TTS (Sonic 3) | cartesia.ai | `CARTESIA_API_KEY` |
| 5 | **E2B** | per-meeting Workroom sandbox | e2b.dev | `E2B_API_KEY` |
| 6 | **Sentry** (opt.) | error tracking | sentry.io | `SENTRY_DSN` |
| 7 | **Langfuse** (opt.) | tracing — scaffold **inert** in V0 | cloud free tier | `LANGFUSE_*` (leave unset for V0) |
| — | **AES domain keys** | encrypt stored integration creds | `openssl rand -hex 32` ×3 | `AES_KEY_RECALL` `AES_KEY_STT` `AES_KEY_CALENDAR` |
| — | **Session secret** | signed session cookie | `openssl rand -hex 32` | `SESSION_SECRET` |

**Confirm-at-build (Doc 00 / CANONICAL §11.10):** Recall, E2B, Nango, AssemblyAI, Cartesia, and the `claude_agent_sdk` message shape are **not** guessable from a design doc — the build loop must fetch each vendor's *live* API docs and confirm the wire shape before coding against it. Have the dashboards/API references reachable when the loop hits Docs 02/04/05.

---

## TIER C — GCP + Terraform + the product-only auth (deploy & run)

### C1 · GCP project & foundations (Doc 00 §4, §8)
- [ ] A **GCP org/billing account** + **two projects**: `proxy-dev` and `proxy-prod` (Terraform `envs/dev` + `envs/prod`).
- [ ] Enable APIs (Terraform `modules/bootstrap` does this): Cloud Run, Cloud SQL Admin, Secret Manager, Artifact Registry, Cloud Build, Compute Engine (the `code_intel` GCE/MIG host), Cloud Scheduler, Cloud KMS, Service Networking/VPC, DNS, Monitoring.
- [ ] **Remote TF state bucket**: `gs://proxy-tf-state/` (`infra/setup-remote-state.sh`), versioned + `prevent_destroy`.
- [ ] **Service-account-per-role**, least privilege (Doc 00 §8) — Terraform-managed.

### C2 · The two Terraform modules (Doc 00 §8) — apply order **bootstrap → platform**, per env
- [ ] `infra/modules/bootstrap/` — enable APIs, org policy, **project-deletion lien**.
- [ ] `infra/modules/platform/` — Cloud Run services (`control_plane`, `meeting_runtime`), the `code_intel` **GCE/MIG + per-tenant encrypted PD**, **Cloud SQL Postgres 15** (dev `db-f1-micro` ZONAL; prod `db-custom-1-3840` REGIONAL + PITR, backups 03:00), **GCS buckets** (notes/artifacts — **Object Versioning ON**), Secret Manager secret *resources*, network/NAT, DNS, monitoring, a smoke-test.
- [ ] Disciplines to keep verbatim (Doc 00 §8): `prevent_destroy` on every data-bearing resource (both SQL DBs, buckets, credential-key `random_id`s, the project lien); `ignore_changes=[secret_data]` on secret versions; **Terraform owns the service shell, the deploy script owns image/env/secrets** (`ignore_changes=[template]` on Cloud Run); **dev auto-deploys on a Cloud Build trigger; prod is promote-based** (Artifact Registry immutable tags → a promote job ships the exact-tested image).

### C3 · Secrets — GCP Secret Manager (Doc 00 §7, §12)
- [ ] Terraform **creates the secret resources** and auto-populates `database-url`, `session-secret`, and the AES credential keys as `random_id` (with `lifecycle.ignore_changes=[secret_data]` so out-of-band rotations survive `apply`).
- [ ] **Set out-of-band** (via the guarded `add-secret.sh`, silent input): `ANTHROPIC_*`, `RECALL_API_KEY`, `ASSEMBLYAI_API_KEY`, `CARTESIA_API_KEY`, `E2B_API_KEY`, `GOOGLE_CLIENT_ID/SECRET`, `NANGO_*`, and the **GitHub App private key**.
- [ ] The **`check-secret-bindings` CI + pre-commit job** must pass — it diffs the Terraform secret map against the deploy config and fails on drift (a secret in the module but not the deploy crashes prod at boot).
- [ ] Per-tenant encryption (Doc 00 §4): GCP-KMS-encrypted Persistent Disk **plus** a per-tenant envelope key over each tenant's subdirectory (offboarding = destroy the key = crypto-shred).

### C4 · The GitHub App (Doc 01 §3.1 — the product's repo connector)
- [ ] **Create a GitHub App** (not an OAuth App): scopes **exactly** `contents:read` + `metadata:read` — nothing else.
- [ ] **Webhook**: URL = `https://<domain>/webhooks/github`, secret = `GITHUB_WEBHOOK_SECRET`; subscribe to **push**.
- [ ] Generate the **private key (.pem)** → Secret Manager (never expires — put on the manual-rotation runbook).
- [ ] Installs are launched **from our connect page** (GitHub requires this, not the Marketplace).
- [ ] Record `GITHUB_APP_ID`.

### C5 · User login — Google OAuth / OIDC (Doc 00 §7)
- [ ] GCP console → OAuth 2.0 Client (Web) → `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
- [ ] Authorized redirect URI: `https://<domain>/auth/callback` (routes `/auth/{login,callback,logout}` on `control_plane`).

### C6 · Nango — end-user GitHub grant (Doc 00 §7, Doc 01 §3.1)
- [ ] Stand up **Nango** (self-host or cloud) → `NANGO_SECRET_KEY`, `NANGO_HOST`.
- [ ] Configure a GitHub integration in Nango (it mints the short-lived installation token per operation; never cache/log tokens).
- [ ] Boundary: **Nango holds end-user GitHub OAuth; Secret Manager holds platform credentials + the GitHub-App private key.**

### C7 · Domain, DNS & monitoring (Doc 00 §13)
- [ ] A **domain** for: the connect page + `/m/{meeting_id}` surface, the GitHub webhook receiver, and the OAuth redirect.
- [ ] **UptimeRobot** on `/health` + **Healthchecks.io** heartbeats from the harness.
- [ ] The **one hardening script** (Doc 00 §13): key-only SSH, no root login, fail2ban, unattended-upgrades, non-root services, host firewall AND security group, customer code only on the encrypted volume, code execution only ever inside E2B.

---

## The config files (this repo)
- [x] **`.env.example`** — the config contract (created). Copy to `.env`; fill Tier A first, Tier B/C as you reach them. It's also the required-key manifest for the §6 boot gate.
- [x] **`.gitignore`** — hardened (created): ignores `.env`, `*.pem`/`*.key`, service-account json, all Terraform state/tfvars. **Verify `.env` and any `*-sa.json` are never staged.**
- [ ] **`config/defaults.toml`** — the numeric tunables live here (already authored at `product/v0-spec/config-defaults.toml`; the loop copies it to `config/defaults.toml` at repo root during the Doc 00 build). Code reads this; env vars override only secrets/seats.

---

## Human-only decisions to make before the loop runs (nothing can decide these for you)
1. **GCP org + billing** — which billing account; dev/prod project names; region (single region, no multi-region in V0).
2. **The domain name** — needed for GitHub webhook URL, OAuth redirect, and the connect/`/m/` surface.
3. **Anthropic auth mode** — API key (simplest) vs OAuth token vs Vertex.
4. **Managed vs self-hosted transport** — V0 is **managed** (Recall + AssemblyAI + Cartesia ≈ $0.75–0.85/hr). The self-hosted cost-down path is explicitly out of V0 scope; don't set it up now.
5. **Where the build happens** — does this `proxy-build` repo *become* the `proxy/` product repo (the loop fills in `services/`, `libs/`, `infra/`, `apps/`), or a fresh repo? (Recommend: this repo becomes it; the build machinery — `harness/`, `runner.py`, `.claude/` — stays as the meta-driver.)

## What to do first (the critical path)
1. **Tier A** — Python/uv/Docker + `.env` (DATABASE_URL + SESSION_SECRET). → the loop can start building the foundation and pass CI/tests **today**, no cloud.
2. **Tier B keys** — before the **§16 step-0.5 spike** (the #1 de-risk: sub-2s answer + ORM accuracy on real infra). Provision Recall/E2B/AssemblyAI/Cartesia/Anthropic + a minimal Cloud Run + Cloud SQL for the spike.
3. **Tier C** — full GCP/Terraform + GitHub App + Google OAuth + Nango + domain, before the foundation **deploys** and before real meetings.

*Note (repo hygiene): the build machinery (`AGENTS.md`, `CLAUDE.md`, `.claude/` skills, `harness/prompts/pass_prompt.md`) still references the retired estate-graph architecture and must be rewired to `product/v0-spec/` + the Doc 00 §16 build order before a build run — tracked separately from this provisioning list.*
