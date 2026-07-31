# Inputs — parameterized so `terraform apply` works for the Proxy dev project
# (proxy-meeting-dev) AND for a customer self-host with only these values changed.
# NOTHING here is a secret: secret VALUES only ever come from Secret Manager
# (populated out-of-band by scripts/set-secrets.sh), never from a Terraform literal
# or a *.tfvars committed to the repo.

variable "project_id" {
  description = "GCP project id (dev: proxy-meeting-dev; customer self-host: their own project)."
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run, Cloud SQL, GCS, and Artifact Registry. Keep ONE region (SPEC §9: no multi-region)."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name for the control-plane. Also the Artifact Registry image name."
  type        = string
  default     = "proxy-control-plane"
}

variable "image" {
  description = <<-EOT
    Full control-plane image ref to deploy, e.g.
    us-central1-docker.pkg.dev/proxy-meeting-dev/proxy/proxy-control-plane:v0.
    Built + pushed by the deploy/README.md build step BEFORE `terraform apply`.
    Defaults to Google's placeholder so a first apply stands the estate up before
    the image exists; re-apply with -var="image=..." once the image is pushed.
  EOT
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "db_tier" {
  description = "Cloud SQL machine tier. Small by default (SPEC §9: small tier); resize later without data loss."
  type        = string
  default     = "db-g1-small"
}

variable "db_name" {
  description = "The logical application database on the Cloud SQL instance."
  type        = string
  default     = "proxy"
}

variable "db_user" {
  description = "The application Postgres user the app DSN connects as."
  type        = string
  default     = "proxy"
}

variable "recall_region" {
  description = "The Recall.ai region your RECALL_API_KEY + AssemblyAI BYOK live in (Recall regions are ISOLATED)."
  type        = string
  default     = "us-east-1"
}

variable "min_instances" {
  description = "Cloud Run minimum instances. MUST be >= 1: keeps the webhook-drain loop + WS gateway warm (SPEC §4/§8), so no cold start on the first ask."
  type        = number
  default     = 1
}

variable "max_instances" {
  description = "Cloud Run maximum instances (simple autoscale ceiling)."
  type        = number
  default     = 10
}

variable "allow_unauthenticated" {
  description = "Whether the Cloud Run service is publicly reachable. TRUE for v0: Recall status webhooks, the Output-Media page, and Google OIDC callback are all inbound public HTTPS."
  type        = bool
  default     = true
}

variable "public_base_url" {
  description = <<-EOT
    The service's own public HTTPS origin, e.g. https://proxy-control-plane-xxxx.a.run.app
    (or a mapped custom domain). Wires PUBLIC_BASE_URL (the sandbox→host relay,
    SPEC §5) + RECALL_WEBHOOK_URL + RECALL_OUTPUT_MEDIA_URL. Leave "" on the FIRST
    apply (the URL is not knowable until the service exists); on the SECOND apply pass
        -var="public_base_url=$(terraform output -raw service_url)"
    to wire the real origin. Empty ⇒ the code honest-degrades (relay unreachable →
    result-text speak; no live-transcript webhook) — see cloud_run.tf.
  EOT
  type        = string
  default     = ""
}
