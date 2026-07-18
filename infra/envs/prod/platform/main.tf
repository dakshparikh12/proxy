# === envs/prod/platform === prod platform stack.
# apply order per env: bootstrap -> platform (this stack is applied SECOND,
# after prod/bootstrap has provisioned remote state + registry + SAs).
module "platform" {
  source       = "../../../modules/platform"
  project_id   = "proxy-v0-prod"
  region       = "us-central1"
  env          = "prod"
  state_bucket = "tfstate-proxy-v0-prod-prod"
}
