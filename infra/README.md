# infra/ — superseded by `terraform/`

The old `infra/` Terraform described the **pre-pivot** architecture: a per-meeting
GCE managed instance group, a stateful `code_intel` host with a KMS-encrypted
per-tenant disk, a shared VPC + firewall, daily snapshots, and secret/IAM bindings
for services that no longer exist (code-intel, scribe, Nango, the GitHub App
private key).

The reactive-workroom pivot (SPEC v6, §9) replaced all of that with the **simplest
stack**: ONE Cloud Run service + Cloud SQL Postgres + Secret Manager + GCS +
Artifact Registry, with per-meeting sandboxes on **E2B** (an external SaaS) — no
GKE, no Pub/Sub, no multi-region, no GCE hosts, no KMS.

**The live infrastructure-as-code now lives in [`../terraform/`](../terraform/).**
See [`../deploy/README.md`](../deploy/README.md) for the deploy commands.
