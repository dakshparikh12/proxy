# === modules/customer-platform === enterprise dedicated-project tenancy.
#
# RECORDED BUT NOT BUILT in V0. This module is the future home of the
# customer-platform path: standing up an isolated, dedicated cloud project for
# enterprise tenants who require their own project boundary. In V0 it
# instantiates ZERO resources — it exists only to reserve the name and the
# variable contract. It is deliberately NOT instantiated anywhere (no
# `module "customer_platform"` call), and it declares no `resource` blocks.

variable "customer_id" {
  type        = string
  description = "The enterprise customer this dedicated project belongs to."
  default     = null
}

variable "billing_account" {
  type        = string
  description = "Billing account for the dedicated project (unused in V0)."
  default     = null
}

variable "region" {
  type    = string
  default = "us-central1"
}

# No resources are declared here in V0. See Doc 00 §8 (enterprise tenancy).
