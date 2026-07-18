# === modules/bootstrap === per-env bootstrap stack.
#
# The bootstrap stack stands up the foundational, slow-changing substrate that
# everything else depends on: the remote Terraform state bucket, the project
# liens/destroy-guards, the Artifact Registry repo, and the base service
# accounts. It is applied FIRST.
#
# apply order per env: bootstrap -> platform. The platform stack reads the
# outputs of this stack (state bucket, registry, SAs), so bootstrap must be
# applied before platform, never the other way around.

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

output "state_bucket" {
  value = "tfstate-${var.project_id}-${var.env}"
}
