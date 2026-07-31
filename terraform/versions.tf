# Provider pins. The Google provider version is pinned to a compatible range so a
# `terraform init` in a fresh checkout (or a customer self-host) resolves the same
# provider a plan was authored against. Keep this the ONLY provider block.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0, < 7.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
