# === modules/platform === per-env platform stack.
#
# The platform stack stands up the workloads and their wiring: Cloud Run
# (control_plane), the per-meeting harness MIG, the code_intel host, Cloud SQL,
# buckets, secrets, and IAM. It depends on the bootstrap stack's outputs.
#
# apply order per env: bootstrap -> platform. This stack is applied SECOND,
# after bootstrap has provisioned remote state, the registry, and the SAs.

variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "env" {
  type = string
}

variable "state_bucket" {
  type = string
}
