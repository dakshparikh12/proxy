# Proxy v0 deploy — the reactive-workroom system on Google Cloud

The simplest production-ready stack (SPEC §9), nothing more:

| Piece | What | Where |
|---|---|---|
| **Control plane** | ONE Cloud Run service — webhook/orchestrator, connect page, `/m` surface, Output-Media, WS gateway, **the sandbox→host `/meetings/{id}/relay`** | `deploy/control-plane/Dockerfile`, `terraform/cloud_run.tf` |
| **Database** | ONE Cloud SQL Postgres 15 (small tier) | `terraform/cloudsql.tf` |
| **Secrets** | Secret Manager (the ONLY home for secret values) | `terraform/secrets.tf` |
| **Storage** | ONE GCS bucket (Object Versioning on) — notes/artifacts | `terraform/storage.tf` |
| **Images** | Artifact Registry (one Docker repo) | `terraform/registry.tf` |
| **Per-meeting sandbox** | **E2B** microVM (external SaaS) — `proxy-workroom` template | `deploy/e2b/` |

No GKE, no Pub/Sub, no multi-region, no observability stack (SPEC §9). Per-meeting
work runs in E2B, so the Cloud Run host is stateless — truth lives in Postgres + GCS.

The Terraform module is parameterized (`project_id`, `region`, …) so the SAME code
applies to the Proxy dev project **and** a customer self-host (see §5).

---

## 0. One-time prerequisites (founder-gated — needs `gcloud` + billing)

Terraform provisions everything EXCEPT the two things it structurally cannot:

1. **Billing + project.** The GCP project (`proxy-meeting-dev`) must exist and have
   **billing linked** — enabling the first API needs it. This is a founder step.
   ```bash
   gcloud auth login
   gcloud config set project proxy-meeting-dev
   ```
2. **Vendor accounts + keys** (Recall.ai, Cartesia, AssemblyAI, E2B, Anthropic /
   the Claude subscription token, Google OIDC client). Their *values* go into Secret
   Manager in §3 — never into Terraform or the image.

Everything else (API enablement, SQL, bucket, registry, service account, secret
resources, Cloud Run) is `terraform apply`.

---

## 1. Provision the estate (Terraform)

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # set project_id (+ any overrides)
terraform init
# First apply: stands up APIs, SQL, bucket, registry, secrets, SA, and the Cloud Run
# shell on a placeholder image (public_base_url left "" — wired in §4).
terraform apply -var="project_id=proxy-meeting-dev"
```

This creates the Artifact Registry repo the image push (§2) targets, and every
Secret Manager secret resource. The Terraform-generated secrets (AES keys,
`SESSION_SECRET`, `SESSION_SIGNING_KEY`, the two internal tokens,
`GITHUB_WEBHOOK_SECRET`, and the `DATABASE_URL` built from the SQL instance) are
populated automatically; the vendor/auth secrets are set next.

---

## 2. Build + push the control-plane image

```bash
# from the repo root
PROJECT=proxy-meeting-dev REGION=us-central1 TAG=v0 bash deploy/build-and-push.sh
# prints: us-central1-docker.pkg.dev/proxy-meeting-dev/proxy/proxy-control-plane:v0
```

(Equivalent by hand:
`docker build -f deploy/control-plane/Dockerfile -t <repo>/proxy-control-plane:v0 . && docker push <repo>/proxy-control-plane:v0`.)

---

## 3. Set the vendor / auth secret VALUES (out-of-band)

Secret values NEVER go in Terraform or the image (CLAUDE.md hard rule). Export them
(e.g. `source` a local, gitignored `.env`) and run the helper:

```bash
export RECALL_API_KEY=... RECALL_WEBHOOK_SECRET=... \
       CARTESIA_API_KEY=... ASSEMBLYAI_API_KEY=... E2B_API_KEY=... \
       GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... \
       CLAUDE_CODE_OAUTH_TOKEN=...        # DEV: the subscription token
       # PROD instead: export ANTHROPIC_API_KEY=...  (SPEC §9 — ToS forbids
       # routing customers on a personal subscription; a one-line seam swaps them)
PROJECT=proxy-meeting-dev bash terraform/set-secrets.sh
```

`RECALL_WEBHOOK_SECRET` is the signing secret (`whsec_…`) of the **status webhook**
you register in the Recall dashboard (§4). `ASSEMBLYAI_API_KEY` is BYOK — the SAME
key must ALSO be pasted into the Recall **Transcription** dashboard, per region.

---

## 4. Wire the public URL + deploy the real image (second apply)

The control-plane needs its OWN public URL for the sandbox→host relay
(`PUBLIC_BASE_URL`, SPEC §5) and the Recall webhook/output-media facts. That URL
isn't knowable until the service exists, so this is a one-line second apply:

```bash
cd terraform
terraform apply \
  -var="project_id=proxy-meeting-dev" \
  -var="image=us-central1-docker.pkg.dev/proxy-meeting-dev/proxy/proxy-control-plane:v0" \
  -var="public_base_url=$(terraform output -raw service_url)"
```

Then, one-time in the **Recall dashboard** (per region):
- **Webhooks** → add the status endpoint `${service_url}/webhooks/recall`, copy its
  signing secret into the `RECALL_WEBHOOK_SECRET` secret (re-run §3 for that key).
- **Transcription** → paste your AssemblyAI key (BYOK).

And in **Google Cloud Console** → OAuth client → add redirect URI
`${service_url}/auth/callback`.

**Verify:**
```bash
curl -fsS "$(terraform -chdir=terraform output -raw service_url)"/health     # {"status":"healthy"}
curl -fsS "$(terraform -chdir=terraform output -raw service_url)"/readiness  # {"status":"ready"}
```
A missing hard-gate secret shows in Cloud Run logs as
`fail-fast boot gate: missing required config keys (<NAMES>)` — fix that secret and
redeploy (`control_plane/settings.py`).

---

## 5. Bake the E2B `proxy-workroom` template (a bake step, NOT a Cloud Run thing)

The per-meeting sandbox is an E2B microVM. Baking a template pre-installs the
toolchain (native `claude` + `mcp==1.28.1` + git) so each meeting's sandbox is warm,
and sets a **bigger machine (4 vCPU / 8 GB)** — the OOM fix (SPEC §12 founder gate #2).

```bash
cd deploy/e2b
export E2B_API_KEY=...          # same key as the E2B secret
e2b template build              # builds from e2b.Dockerfile per e2b.toml; prints a template id
```

Then set that template id as the workroom default so provisioning uses it:
`services/in-meeting/src/in_meeting/workroom.py` → `DEFAULT_TEMPLATE = "<template id>"`
(that file is outside this deploy change; wire it in a follow-up). Until then the
workroom provisions a BASE sandbox and installs the toolchain at join (slower, and
the base size is what OOMs — bake before real-meeting load).

---

## The final secret list (what Secret Manager holds)

Verified against the code the new system actually reads (`control_plane/settings.py`,
`control_plane/{meetings,app,session,provisioner,github_webhook,relay}.py`,
`in_meeting/{transport,workroom}`) and `.env.example`.

**Generated by Terraform** (random material; never rotated on apply):
`AES_KEY_RECALL`, `AES_KEY_STT`, `AES_KEY_CALENDAR`, `SESSION_SECRET`,
`SESSION_SIGNING_KEY`, `PROXY_INTERNAL_TOKEN`, `INTERNAL_RECONCILE_TOKEN`,
`GITHUB_WEBHOOK_SECRET`, and `DATABASE_URL` (built from the SQL instance + password).

**Set out-of-band** (vendor keys / auth — `terraform/set-secrets.sh`):
`RECALL_API_KEY`, `RECALL_WEBHOOK_SECRET`, `CARTESIA_API_KEY`, `ASSEMBLYAI_API_KEY`,
`E2B_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`CLAUDE_CODE_OAUTH_TOKEN` (dev subscription — the workroom sandbox auth),
`ANTHROPIC_API_KEY` (prod cloud API — the control-plane Claude auth).

**Non-secret config** (plain Cloud Run env, not Secret Manager): `PROXY_ENV=prod`,
`GCP_PROJECT_ID`, `GCP_REGION`, `GCS_BUCKET` (the bucket NAME), `RECALL_REGION`,
`PUBLIC_BASE_URL`, `RECALL_WEBHOOK_URL`, `RECALL_OUTPUT_MEDIA_URL`, `PORT`.

Removed vs the old infra (deleted services): no code-intel / scribe secrets, and the
old KMS / Nango / `GITHUB_APP_*` bindings are gone — the reactive-workroom live path
does not read them.

---

## Self-host (a customer running Proxy in their own GCP project)

The whole estate is ONE container + ONE Terraform module (SPEC §9), so self-host is
the same four steps in the customer's project:

1. `gcloud config set project <their-project>` (billing linked).
2. `terraform apply -var="project_id=<their-project>"` (+ `-var="region=..."` if not
   `us-central1`). Nothing is Proxy-org-specific — bucket, SQL, registry, SA, and
   secrets are all derived from `project_id`.
3. Build+push the image to THEIR registry (`PROJECT=<their-project> bash
   deploy/build-and-push.sh`) and set their own vendor secrets
   (`terraform/set-secrets.sh`). For a customer, use `ANTHROPIC_API_KEY` (the cloud
   API), not the subscription token.
4. Second apply with `image=` + `public_base_url=`; bake their own E2B template.

Non-GCP self-host: the image runs anywhere that gives it Postgres + a GCS-compatible
bucket + the env contract above (the Cloud SQL connector volume is the only
GCP-specific bit — a plain `DATABASE_URL` DSN replaces it).

---

## Gaps that need the founder

- **Billing + org policy** on `proxy-meeting-dev` (Terraform can't self-enable the
  first API without billing linked; some orgs restrict `allUsers` invoker /
  public buckets).
- **Prod Claude auth** (SPEC §12 gate #3): dev uses `CLAUDE_CODE_OAUTH_TOKEN`; prod
  MUST use `ANTHROPIC_API_KEY` (ToS). The seam is one secret swap.
- **E2B machine size** (SPEC §12 gate #2): `deploy/e2b/e2b.toml` sets 4 vCPU / 8 GB;
  confirm/raise on the real-meeting battery.
- **Domain** (optional): map a custom domain to the Cloud Run service, then use it as
  `public_base_url` instead of the `run.app` URL.
