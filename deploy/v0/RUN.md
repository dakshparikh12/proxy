# Proxy v0 — production runbook (any custom deployment)

One deployable: **proxy-control-plane** (`deploy/v0/Dockerfile`, serves
`python -m control_plane.server`). One Postgres. One GCS bucket. Secrets in
Secret Manager. That is the whole estate.

**Why Cloud Run (not a VM):** the host is stateless (meeting sandboxes run in
E2B, the code-intel clone/index cache is rebuildable, truth lives in
Postgres + GCS), so Cloud Run gives TLS + a public HTTPS host (which Recall
webhooks and Output Media require), secret injection, revisions/rollback and
scale-to-zero with zero host hardening. A single VM adds patching, TLS, and
process supervision for no v0 benefit.

## 1. Environment contract (verified against the code that reads it)

Boot is fail-fast: a missing hard-gate key crashes at import **naming the key**
(`services/control-plane/src/control_plane/settings.py:110-118`).

### Hard boot gates (settings.py:41-69, checked at :84-107)
| Env | Read at | Notes |
|---|---|---|
| `DATABASE_URL` | settings.py:41 | Postgres DSN. Cloud Run: `postgresql://proxy:<pw>@/proxy?host=/cloudsql/<project>:<region>:proxy-pg` |
| `GCS_BUCKET` | settings.py:42 → server.py:410 → libs/http/src/http/external.py:94 | Finalized meeting notes (`meetings/<id>/notes.md`). Enable Object Versioning. |
| `RECALL_API_KEY` | settings.py:43 (join: control_plane/meetings.py:44) | From the Recall dashboard of YOUR region. |
| `AES_KEY_RECALL` / `AES_KEY_STT` / `AES_KEY_CALENDAR` | settings.py:60-62 | One AES-256-GCM key per domain: `openssl rand -hex 32` each. |
| Claude auth (one of) `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `CLAUDE_CODE_USE_VERTEX` | settings.py:65-69 | API key is the simple v0 mode. |
| `PROXY_ENV=prod` gates: `SESSION_SECRET`, `GCP_PROJECT_ID` | settings.py:29-32, 72-73 | Set `PROXY_ENV=prod` in any real deployment. |

### Recall / transport wiring
| Env | Read at | Must point to |
|---|---|---|
| `RECALL_REGION` | in-meeting `src/transport/recall.py:43` | The Recall region your key lives in (regions are isolated). |
| `RECALL_WEBHOOK_SECRET` | settings.py:49 | Signing secret of the **status webhook** you register in the Recall dashboard; route fails closed (401) when unset. |
| `RECALL_WEBHOOK_URL` | control_plane/meetings.py:49 | `https://<public-host>/webhooks/recall` — drives realtime transcript delivery on bot create (transport/recall.py:199-209). Unset ⇒ the bot joins with no live transcripts. |
| `RECALL_OUTPUT_MEDIA_URL` | control_plane/meetings.py:50 | `https://<public-host>/output-media/{meeting_id}` — sent verbatim as the bot's webpage camera (transport/recall.py:211-217); page served at in_meeting/output_media.py:302. |
| `ASSEMBLYAI_API_KEY` | in-meeting `src/in_meeting/settings.py:44` | BYOK: this key must ALSO be pasted into the Recall **Transcription** dashboard, per region (the bot config selects `assembly_ai_v3_streaming`, transport/recall.py:201). |
| `CARTESIA_API_KEY` | in_meeting/settings.py:38 (used transport/tts.py) | Proxy's voice (Sonic TTS). |
| `CARTESIA_VOICE_ID` (optional) | transport/tts.py:77 | Defaults to the library voice when unset. |
| `E2B_API_KEY` | in_meeting/settings.py:41 | Per-meeting sandbox. |
| `GITHUB_WEBHOOK_SECRET` | settings.py:57 | GitHub App webhook (push → reindex); route fails closed when unset. |

### Sessions, internal plane, models (set in any real deployment)
| Env | Read at | Notes |
|---|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | control_plane/app.py:37-38 | Google OIDC sign-in; redirect URI `https://<public-host>/auth/callback`. |
| `SESSION_SIGNING_KEY` | control_plane/session.py:22 | HMAC for the durable session cookie — DISTINCT from `SESSION_SECRET`; has an insecure dev default, so set it. |
| `PROXY_INTERNAL_TOKEN` | libs/http/src/http/internal.py:21-22 (checked control_plane/internal.py:145) | Server-to-server bearer for `/internal/*`; has a dev default, so set it. |
| `INTERNAL_RECONCILE_TOKEN` | libs/ops/src/ops/reconcile.py:104 | Reconcile-endpoint bearer; dev default, so set it. |
| `PROXY_WS_ALLOWED_ORIGINS` (optional) | control_plane/gateway_route.py:59 | Comma-separated WS origin allowlist. |
| `PROXY_MODEL_<SEAT>` (optional) | libs/llm/src/llm/routing.py:29 | Overrides the canonical seat table. |
| `PROXY_MAX_INFLIGHT_LLM` | libs/llm/src/llm/client.py:36 | Global LLM concurrency cap (default 16). |
| `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` (optional) | libs/agentkit/src/agentkit/tracing.py:22-24 | Tracing stays inert unless both are set. |
| `PROXY_GITHUB_APP_SLUG` (optional) | control_plane/connect.py:451 | Your GitHub App slug for the install URL. |
| `PORT` | server.py `main()` | Injected by Cloud Run; defaults to 8080. |

Declared in `.env.example` but not read by v0 live code (dashboard-side /
reserved): `NANGO_*`, `GITHUB_APP_ID/CLIENT_ID/CLIENT_SECRET/PRIVATE_KEY_PATH`.

## 2. Minimal GCP estate (once per deployment)

```bash
export PROJECT=<gcp-project> REGION=us-central1
gcloud config set project $PROJECT

# Postgres (smallest HA-less tier; resize later)
gcloud sql instances create proxy-pg --database-version=POSTGRES_16 \
  --region=$REGION --tier=db-g1-small
gcloud sql databases create proxy --instance=proxy-pg
gcloud sql users create proxy --instance=proxy-pg --password=<db-password>

# Notes bucket — Object Versioning ON
gcloud storage buckets create gs://$PROJECT-proxy-notes --location=$REGION
gcloud storage buckets update gs://$PROJECT-proxy-notes --versioning

# Secrets: one per env key above (repeat per key)
printf '%s' '<value>' | gcloud secrets create RECALL_API_KEY --data-file=-

# Image → Artifact Registry
gcloud artifacts repositories create proxy --repository-format=docker --location=$REGION
docker build -f deploy/v0/Dockerfile -t $REGION-docker.pkg.dev/$PROJECT/proxy/proxy-control-plane:v0 .
docker push $REGION-docker.pkg.dev/$PROJECT/proxy/proxy-control-plane:v0

# The one service (container self-migrates on boot under an advisory lock —
# `alembic upgrade head` retry loop in the CMD; no separate migrate step needed)
gcloud run deploy proxy-control-plane \
  --image=$REGION-docker.pkg.dev/$PROJECT/proxy/proxy-control-plane:v0 \
  --region=$REGION --allow-unauthenticated --port=8080 \
  --min-instances=1 --timeout=3600 \
  --add-cloudsql-instances=$PROJECT:$REGION:proxy-pg \
  --set-env-vars=PROXY_ENV=prod,GCP_PROJECT_ID=$PROJECT,GCS_BUCKET=$PROJECT-proxy-notes,RECALL_REGION=us-east-1,RECALL_WEBHOOK_URL=https://<public-host>/webhooks/recall,RECALL_OUTPUT_MEDIA_URL=https://<public-host>/output-media/{meeting_id} \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest,RECALL_API_KEY=RECALL_API_KEY:latest,RECALL_WEBHOOK_SECRET=RECALL_WEBHOOK_SECRET:latest,ASSEMBLYAI_API_KEY=ASSEMBLYAI_API_KEY:latest,CARTESIA_API_KEY=CARTESIA_API_KEY:latest,E2B_API_KEY=E2B_API_KEY:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,AES_KEY_RECALL=AES_KEY_RECALL:latest,AES_KEY_STT=AES_KEY_STT:latest,AES_KEY_CALENDAR=AES_KEY_CALENDAR:latest,SESSION_SECRET=SESSION_SECRET:latest,SESSION_SIGNING_KEY=SESSION_SIGNING_KEY:latest,PROXY_INTERNAL_TOKEN=PROXY_INTERNAL_TOKEN:latest,INTERNAL_RECONCILE_TOKEN=INTERNAL_RECONCILE_TOKEN:latest,GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,GITHUB_WEBHOOK_SECRET=GITHUB_WEBHOOK_SECRET:latest
```

After the first deploy, note the service URL (or map a domain) and re-deploy
with `<public-host>` filled into `RECALL_WEBHOOK_URL` / `RECALL_OUTPUT_MEDIA_URL`.
`min-instances=1` keeps the webhook drain loop warm; `--timeout=3600` covers the
meeting-length WS connections. Migrations: every boot runs `alembic upgrade head`
(bounded retry; the winner holds a Postgres advisory lock — `migrations/env.py`).

## 3. Recall dashboard (one-time, per region)

1. Create the API key in the SAME region as `RECALL_REGION`.
2. Transcription → paste your AssemblyAI key (BYOK). Regions are isolated —
   repeat per region you deploy in.
3. Webhooks → add the **status webhook** endpoint
   `https://<public-host>/webhooks/recall` and copy its signing secret
   (`whsec_…`) into the `RECALL_WEBHOOK_SECRET` secret.

## 4. Verify

```bash
curl -fsS https://<public-host>/health      # {"status":"healthy"}
curl -fsS https://<public-host>/readiness   # {"status":"ready"}
```

A missing hard-gate key shows up in Cloud Run logs as
`fail-fast boot gate: missing required config keys (<NAMES>)` — fix the secret
binding it names and redeploy.
