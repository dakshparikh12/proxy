# Proxy — Provisioning & Setup Runbook (front-load everything, dev)
### Exact step-by-step to configure every account, key, and cloud resource BEFORE the build loop runs, so the foundation (`product/v0-spec/00-FOUNDATION.md`) builds and deploys with nothing blocking. Researched against official docs, July 2026.

## Read this first — the two framings that make this smart
1. **Doc 00 IS the infrastructure** (Terraform, Cloud Run, Cloud SQL, Secret Manager, the boot gate that validates keys). So its definition-of-done — *"deploy lands, container self-migrates, `/health` serves, secrets resolve at boot"* — cannot be proven without the real cloud existing. That's why we front-load, rather than tier.
2. **Free-to-hold vs costs-idle.** Every API *key* costs $0 to hold — create them all now. GCP *resources* mostly scale to zero (Cloud Run, GCS, Secret Manager, KMS ≈ free idle) **except Cloud SQL**, which bills per running second (~$10/mo). Stand up dev now; **stop the SQL instance between build sessions** (command at the end); do NOT stand up prod until you run real meetings.

**Division of labor:** *you* provision the accounts/billing/domain/keys and create the GCP project (this doc). The *build loop* writes the infra code (Terraform modules, Dockerfile, `add-secret.sh`, boot lifespan). Neither can do the other's half.

## Order of operations (respect the dependencies)
```
0. Decide: domain · GCP billing · region                    ← blocks everything below
1. Local tools: gcloud, terraform, docker, uv, node
2. GCP core: project → billing → enable APIs → TF service-account+state bucket
3. GCP data: Cloud SQL · GCS(+versioning) · Secret Manager · Artifact Registry · KMS
4. SaaS keys: Anthropic · Recall(+AssemblyAI BYOK) · AssemblyAI · Cartesia · E2B
5. Auth (needs the DOMAIN first): GitHub App → Google OAuth → Nango
6. Local secrets: SESSION_SECRET, AES keys, webhook secret
7. Fill .env (local) + push secrets to Secret Manager (cloud)
8. Verify + stop the SQL instance
```

---

# PART 0 — Decisions to make first (nothing can decide these for you)
- **Domain** — you need one (e.g. `app.yourco.com`). It's required for the GitHub webhook URL *and* the Google OAuth redirect *and* the connect/`/m/` surface. Everything in Part 5 hard-depends on it. Register it now; a subdomain of a domain you own is fine.
- **GCP billing account** — must exist with a payment method (Console → Billing → Manage billing accounts → Create account; can't be done headless). This is the #1 first-time blocker downstream.
- **Region** — pick one and keep it everywhere. This runbook uses `us-central1` (cheapest common). Recall.ai has its *own* region (Part 4) that must match its key.
- **Anthropic auth mode** — API key (recommended for dev) vs OAuth vs Vertex.

Set these shell vars once and reuse across every command below:
```bash
export PROJECT_ID="proxy-dev"            # globally unique, 6–30 chars lowercase; if taken, add a suffix
export REGION="us-central1"
export ZONE="us-central1-a"
export DOMAIN="app.yourco.com"           # <-- your real domain
export SQL_INSTANCE="proxy-dev-pg"
export DB_NAME="proxy"; export DB_USER="proxy_app"
export DB_PASS="$(openssl rand -hex 24)"; echo "DB_PASS=$DB_PASS"   # save this
export BUCKET="proxy-dev-notes-${PROJECT_ID}"   # globally unique
export AR_REPO="proxy-images"
export TF_SA="terraform-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
export RUNTIME_SA="proxy-run@${PROJECT_ID}.iam.gserviceaccount.com"
```

---

# PART 1 — Local tooling (macOS)
```bash
# gcloud CLI (official tarball; brew cask also works)
curl -O https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-darwin-arm.tar.gz
tar -xf google-cloud-cli-darwin-arm.tar.gz && ./google-cloud-sdk/install.sh   # open a NEW terminal after

# Terraform (HashiCorp tap) + tfenv optional
brew tap hashicorp/tap && brew install hashicorp/tap/terraform && terraform -version

# uv (Python 3.12 workspace) + Docker (testcontainers) + node/pnpm (apps/*)
curl -LsSf https://astral.sh/uv/install.sh | sh
# install Docker Desktop; install node + `npm i -g pnpm`
```

---

# PART 2 — GCP core (project, billing, APIs, Terraform SA + state)
> The single most-cited gotcha: **do NOT set `GOOGLE_APPLICATION_CREDENTIALS`** if you use the ADC login below — that env var overrides ADC and silently points Terraform at the wrong/missing key.

```bash
# 1. Auth — you need BOTH. (A) authenticates the gcloud CLI; (B) writes ADC for Terraform/libraries.
gcloud auth login                             # (A)
gcloud auth application-default login         # (B) → ~/.config/gcloud/application_default_credentials.json

# 2. Project → this string is your GCP_PROJECT_ID env var
gcloud projects create "$PROJECT_ID" --name="Proxy Dev"
gcloud config set project "$PROJECT_ID"

# 3. Billing (must be linked before APIs work; wait ~1 min to propagate)
gcloud billing accounts list                  # copy the ACCOUNT_ID (format XXXXXX-XXXXXX-XXXXXX, OPEN=True)
export BILLING_ACCOUNT_ID="XXXXXX-XXXXXX-XXXXXX"
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT_ID"
gcloud billing projects describe "$PROJECT_ID"   # expect billingEnabled: true

# 4. Enable ALL APIs at once (idempotent). WAIT ~2 min after this before any terraform apply
#    or you'll hit SERVICE_DISABLED even though enable "succeeded".
gcloud services enable \
  run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com compute.googleapis.com \
  cloudscheduler.googleapis.com cloudkms.googleapis.com servicenetworking.googleapis.com \
  dns.googleapis.com monitoring.googleapis.com iam.googleapis.com storage.googleapis.com

# 5. Terraform service account + IMPERSONATION (no key file — the safe path)
gcloud iam service-accounts create terraform-deployer --display-name="Terraform deployer (dev)"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${TF_SA}" --role="roles/editor" --condition=None   # dev fast-path
gcloud iam service-accounts add-iam-policy-binding "$TF_SA" \
  --member="user:$(gcloud config get-value account)" \
  --role="roles/iam.serviceAccountTokenCreator"                               # lets YOU impersonate it

# 6. Terraform remote state bucket (create OUT-OF-BAND; never manage it inside its own state)
gcloud storage buckets create "gs://${PROJECT_ID}-tfstate" --project="$PROJECT_ID" \
  --location="US" --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://${PROJECT_ID}-tfstate" --versioning
```
The build loop's `backend.tf` then uses literals (a `backend` block can't use variables): `bucket = "<PROJECT_ID>-tfstate"`, `prefix = "proxy/dev"`, `impersonate_service_account = "<TF_SA>"`. In the provider block set `impersonate_service_account = <TF_SA>` too.

**Least-privilege alternative to `roles/editor`** (recommended even in dev): grant `roles/{run.admin, cloudsql.admin, storage.admin, secretmanager.admin, artifactregistry.admin, cloudscheduler.admin, compute.networkAdmin, servicenetworking.networksAdmin, iam.serviceAccountAdmin, iam.serviceAccountUser, resourcemanager.projectIamAdmin}` instead.

**Errors to expect:** `billing account is not open` → billing not linked/propagated · `SERVICE_DISABLED` on apply → API propagation, wait 2 min · `could not find default credentials` → ADC not set (run step 1B) · `iam.serviceAccounts.getAccessToken denied` → the tokenCreator binding hasn't propagated (~60s).

---

# PART 3 — GCP data services

### 3.1 Cloud SQL Postgres 15 (dev — zonal `db-f1-micro`)
```bash
gcloud sql instances create "$SQL_INSTANCE" \
  --database-version=POSTGRES_15 --tier=db-f1-micro --region="$REGION" \
  --storage-type=SSD --storage-size=10GB --no-storage-auto-increase \
  --availability-type=zonal --root-password="$DB_PASS"
gcloud sql databases create "$DB_NAME" --instance="$SQL_INSTANCE"
gcloud sql users create "$DB_USER" --instance="$SQL_INSTANCE" --password="$DB_PASS"
export CONN=$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')
echo "CONN=$CONN"   # e.g. proxy-dev:us-central1:proxy-dev-pg
```
**Cloud SQL Auth Proxy** (local encrypted tunnel; app connects to a plain Unix socket, no SSL config):
```bash
curl -o cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.23.0/cloud-sql-proxy.darwin.arm64
chmod +x cloud-sql-proxy && mkdir -p ./cloudsql
./cloud-sql-proxy --unix-socket "$PWD/cloudsql" "$CONN"   # leave running in its own terminal
```
`DATABASE_URL` forms (note the empty host + `host=<socket dir>`; **no `sslmode`** — the proxy encrypts):
- **Local:** `postgresql://proxy_app:<DB_PASS>@/proxy?host=<ABS_PATH>/cloudsql/proxy-dev:us-central1:proxy-dev-pg`
- **Cloud Run:** `postgresql://proxy_app:<DB_PASS>@/proxy?host=/cloudsql/proxy-dev:us-central1:proxy-dev-pg` (Cloud Run mounts the socket at `/cloudsql/<CONN>` when you pass `--add-cloudsql-instances=<CONN>`).

⚠️ **This is the one always-on idle cost** (~$8–10/mo compute + ~$2 storage). `gcloud sql instances patch $SQL_INSTANCE --activation-policy=NEVER` stops compute between sessions (storage/IP still bill). Also grant your user `roles/cloudsql.client` if the proxy 403s.

### 3.2 GCS notes/artifacts bucket (**Object Versioning REQUIRED**)
```bash
gcloud storage buckets create "gs://$BUCKET" --project="$PROJECT_ID" \
  --location="$REGION" --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://$BUCKET" --versioning        # REQUIRED (Doc 03 optimistic concurrency)
gcloud storage buckets describe "gs://$BUCKET" --format="default(versioning_enabled)"   # → true
```
→ `GCS_BUCKET=$BUCKET` (bare name, no `gs://`). Idle cost ≈ pennies.

### 3.3 Secret Manager (store DATABASE_URL, SESSION_SECRET, all API keys)
```bash
# example: DATABASE_URL (stdin so the value isn't in shell history)
printf '%s' "postgresql://${DB_USER}:${DB_PASS}@/${DB_NAME}?host=/cloudsql/${CONN}" \
  | gcloud secrets create DATABASE_URL --replication-policy=automatic --data-file=-
gcloud secrets add-iam-policy-binding DATABASE_URL \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/secretmanager.secretAccessor"
gcloud secrets versions access latest --secret=DATABASE_URL      # verify
```
Repeat `secrets create` + `add-iam-policy-binding` for `SESSION_SECRET`, `ANTHROPIC_API_KEY`, `RECALL_API_KEY`, `ASSEMBLYAI_API_KEY`, `CARTESIA_API_KEY`, `E2B_API_KEY`, `GOOGLE_CLIENT_SECRET`, `NANGO_SECRET_KEY`, the GitHub App private key, and the three AES keys. Cloud Run consumes them at deploy: `--set-secrets="DATABASE_URL=DATABASE_URL:latest,..."`; non-secret config (`GCP_PROJECT_ID`, `GCS_BUCKET`) via `--set-env-vars`. In Terraform, put `lifecycle { ignore_changes = [secret_data] }` on any `google_secret_manager_secret_version` so rotations survive apply and plaintext never churns through state.

### 3.4 Artifact Registry (Docker) + 3.5 KMS (code_intel disk CMEK)
```bash
gcloud artifacts repositories create "$AR_REPO" --repository-format=docker \
  --location="$REGION" --description="Proxy dev images"
gcloud auth configure-docker "${REGION}-docker.pkg.dev"          # image URL: REGION-docker.pkg.dev/PROJECT/REPO/img:tag

gcloud kms keyrings create proxy-keyring --location="$REGION"
gcloud kms keys create code-intel-disk --location="$REGION" --keyring=proxy-keyring --purpose=encryption
```
CMEK can only be set at disk *creation* and the key must be in the disk's region; grant the Compute service agent `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the key.

---

# PART 4 — SaaS API keys (all self-service, same-day)
| Service | Get the key | → env var | REQUIRED extra config |
|---|---|---|---|
| **Anthropic** | platform.claude.com → Settings → Billing (add card) → API keys → Create Key (`sk-ant-…`) | `ANTHROPIC_API_KEY` | Billing must be active or calls 400. Confirm model access via the Models list (opus-4-8 / sonnet-4-6 / haiku-4-5). |
| **Recall.ai** | Sign up (Pay-As-You-Go, no sales call) → pick a **region** → region dashboard → Developers → API Keys & Secrets | `RECALL_API_KEY` + `RECALL_WORKSPACE_VERIFICATION_SECRET` + `RECALL_REGION` | **See the BYOK step below — easy to miss.** Auth header is `Authorization: Token <key>`. |
| **AssemblyAI** | assemblyai.com/dashboard → API Keys → Create | `ASSEMBLYAI_API_KEY` | **Same key also pasted into Recall (below).** Confirm streaming is enabled for the account. |
| **Cartesia** | play.cartesia.ai/keys → Create | `CARTESIA_API_KEY` | Requests need `X-API-Key` + a `Cartesia-Version` date header; model id `sonic-3.5`. |
| **E2B** | e2b.dev/dashboard (under the Team selector) | `E2B_API_KEY` | For baking the golden sandbox: `npm i -g @e2b/cli` → `e2b auth login` → `e2b template build`. (`E2B_ACCESS_TOKEN` is a separate CLI token — don't conflate.) |

**⚠️ The Recall ↔ AssemblyAI BYOK passthrough (the step people miss):**
1. Get the AssemblyAI key first.
2. In the Recall **region** dashboard open **Transcription** (e.g. `https://us-east-1.recall.ai/dashboard/transcription`) and **paste the AssemblyAI key** there. Recall regions are fully isolated — paste it in **every region** you record in, and `RECALL_REGION` must match the key's region.
3. When creating bots, set provider **`assembly_ai_v3_streaming`** (not the older `assembly_ai_streaming`). Without the paste, streaming bots fail to transcribe.
4. Subscribe Recall webhooks (`bot.status_change`, `recording.done`, `transcript.done`, `…failed`) → your `https://<domain>/webhooks/recall`; verify with the workspace secret. For local dev use a **static** ngrok URL.

---

# PART 5 — Auth (needs `$DOMAIN` first): GitHub App → Google OAuth → Nango

### 5.1 GitHub App (NOT an OAuth App)
`github.com` → Settings → Developer settings → **GitHub Apps → New GitHub App** (`github.com/settings/apps/new`). Fields:
- **Name** `Proxy Code Reader` (sets the install slug) · **Homepage** `https://$DOMAIN`
- **Callback URL** `https://api.nango.dev/oauth/callback` (so Nango captures the install)
- ✅ **Request user authorization (OAuth) during installation** · ✅ **Redirect on update**
- **Webhook Active** ✅ · **Webhook URL** `https://$DOMAIN/webhooks/github` · **Webhook secret** = `openssl rand -hex 32` → `GITHUB_WEBHOOK_SECRET`
- **Repository permissions:** **Contents = Read-only**, **Metadata = Read-only**, everything else **No access**
- **Subscribe to events:** **Push** only
- **Where can this be installed:** **Any account** → **Create GitHub App**

After creation, on the **General** page collect: **App ID** → `GITHUB_APP_ID` · **Client ID** → `GITHUB_APP_CLIENT_ID` · **Generate a client secret** → `GITHUB_APP_CLIENT_SECRET` · **Generate a private key** (downloads a `.pem`) → save the file, path → `GITHUB_APP_PRIVATE_KEY_PATH` (contents → Secret Manager in prod). Users install from your connect page via `https://github.com/apps/<app-slug>/installations/new`. The private key **never expires — manual rotation.**

### 5.2 Google OAuth 2.0 Web client — **configure the consent screen FIRST**
Console → **Google Auth Platform**:
1. **Branding** → Get Started → App name + support email.
2. **Audience** → User type **External** → add **Test users** (each dev Google account; ≤100 in Testing mode, no verification needed for the non-sensitive scopes).
3. **Data Access** → add scopes `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile` → Save.
4. **Clients → Create client → Web application** → **Authorized redirect URIs:** `https://$DOMAIN/auth/callback` **and** `http://localhost:8080/auth/callback` → Create.
5. Copy **Client ID** → `GOOGLE_CLIENT_ID` and **Client secret** → `GOOGLE_CLIENT_SECRET` (secret shown once). A one-char redirect mismatch → `redirect_uri_mismatch`.

### 5.3 Nango (use Nango Cloud for dev)
1. Create account at **app.nango.dev** → **Environment Settings** → copy the **dev Secret Key** → `NANGO_SECRET_KEY`; set `NANGO_HOST=https://api.nango.dev`.
2. **Integrations → Configure New Integration → GitHub App** (provider **`github-app`** — this mints short-lived *installation* tokens; not `github-app-oauth`, not `github`).
3. Fill from 5.1: **App ID**, **App Public Link** `https://github.com/apps/<app-slug>`, **App Private Key** (paste full `.pem` incl. BEGIN/END lines), **Client ID**, **Client Secret**.
4. Confirm the GitHub App **Callback URL** = `https://api.nango.dev/oauth/callback`. The connect flow: your page → GitHub install URL → user picks repos → redirect → Nango records a **Connection** = one installation → your backend calls Nango with `NANGO_SECRET_KEY` to mint a fresh installation token per operation (never handles the raw key).

---

# PART 6 — Local secrets (generate now)
```bash
openssl rand -hex 32   # SESSION_SECRET
openssl rand -hex 32   # AES_KEY_RECALL
openssl rand -hex 32   # AES_KEY_STT
openssl rand -hex 32   # AES_KEY_CALENDAR
openssl rand -hex 32   # GITHUB_WEBHOOK_SECRET (also entered into the GitHub App form)
```

# PART 7 — Fill config
- **Local:** `cp .env.example .env` and fill every value collected above. For a pure local build you only strictly need `DATABASE_URL` + `SESSION_SECRET`; fill the rest before the deploy/spike.
- **Cloud:** each secret → Secret Manager (Part 3.3), granted to `$RUNTIME_SA`. Non-secret config (`GCP_PROJECT_ID`, `GCS_BUCKET`, `GCP_REGION`) → `--set-env-vars` at deploy.
- **`config/defaults.toml`** — numeric tunables already authored at `product/v0-spec/config-defaults.toml`; the loop copies it to `config/defaults.toml` during the Doc 00 build.

# PART 8 — Verify, then stop the meter
```bash
gcloud config get-value project                                   # your PROJECT_ID
gcloud services list --enabled | grep -E 'run|sqladmin|secret'    # APIs on
gcloud sql instances describe "$SQL_INSTANCE" --format='value(state)'   # RUNNABLE
gcloud storage buckets describe "gs://$BUCKET" --format='value(versioning_enabled)'  # true
gcloud secrets versions access latest --secret=DATABASE_URL       # prints the DSN
# ...then, between build sessions, stop the one always-on cost:
gcloud sql instances patch "$SQL_INSTANCE" --activation-policy=NEVER   # resume: --activation-policy=ALWAYS
```

---

## Idle-cost summary
| Resource | Idle cost | Action |
|---|---|---|
| **Cloud SQL db-f1-micro** | ~$10/mo (per running second) | **Stop between sessions** (`--activation-policy=NEVER`) |
| Cloud Run | ~$0 (scales to zero; a warm `meeting_runtime min_instances≥1` costs only once deployed) | leave undeployed until testing meetings |
| GCS / Secret Manager / KMS / Artifact Registry | cents/mo | leave |
| All SaaS keys | $0 to hold; usage-billed | create all now |

## What's still deferred (do NOT set up now)
Prod GCP env (REGIONAL Cloud SQL + PITR) · self-hosted transport (Recall/STT/TTS OSS path) · the per-customer-GCP-project onboarding · Langfuse self-host (scaffold stays inert). And, separate from provisioning: the **build machinery** (`AGENTS.md`, `.claude/` skills, `harness/prompts/pass_prompt.md`) still references the retired estate-graph architecture and must be rewired to `product/v0-spec/` + the Doc 00 §16 build order before a build run.

---

# ✅ AS-BUILT STATE (provisioned 2026-07-16 — this section reflects reality)
**Everything below exists and every `.env` key is filled. Local `.env` is the source; secrets never committed.**
- **GCP:** project `proxy-meeting-dev` (account `proxy.meeting@gmail.com`, $300 trial credit) · region `us-central1` · 13 APIs enabled · SA `terraform-deployer@` (impersonation by the user, no key file) · SA `proxy-run@` (runtime) · tfstate bucket `proxy-meeting-dev-tfstate` (versioned) · Cloud SQL `proxy-dev-pg` (Postgres 15, db-f1-micro, zonal; db `proxy`, user `proxy_app`; **STOPPED — wake with** `gcloud sql instances patch proxy-dev-pg --activation-policy=ALWAYS`) · bucket `proxy-dev-notes-proxy-meeting-dev` (versioned) · Secret Manager `DATABASE_URL` (bound to proxy-run) · Artifact Registry `proxy-images` · KMS `proxy-keyring/code-intel-disk`.
- **SaaS keys live:** Anthropic (personal-account key for spike/runtime; **build loop runs on the Max subscription**) · Recall (+ workspace secret; AssemblyAI key pasted in the region's Transcription dashboard, default US endpoints) · AssemblyAI · Cartesia · E2B.
- **Auth chain:** GitHub App `proxy-meeting-agent` under `dakshparikh12` (contents:read + metadata:read, Push events; **webhook = smee.io channel, dev-only**; private key at `~/secrets/proxy-meeting-agent.2026-07-16.private-key.pem`) · Google OAuth client `Proxy Web Client` in `proxy-meeting-dev` (consent screen Testing/External; **redirect = localhost:8080 only**) · Nango Cloud dev env, integration `github-app` (App ID + public link + private key; the github-app provider takes no client id/secret — kept in `.env` regardless).
- **Deferred (swap when the domain exists):** GitHub App webhook URL smee→`https://<domain>/webhooks/github` · add `https://<domain>/auth/callback` to the OAuth client · Recall webhook endpoints · prod GCP env. Anthropic usage: build = subscription, product = API key.

## Confirm-at-build (the loop must fetch live docs — CANONICAL §11.10)
Recall, E2B, Nango, AssemblyAI, Cartesia, and the `claude_agent_sdk` message shape can't be pinned from a design doc; the loop verifies each vendor's live wire shape when it builds Docs 02/04/05. Keep those dashboards reachable then.
