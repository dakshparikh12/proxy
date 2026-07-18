# === envs/dev/platform === dev platform stack.
# apply order per env: bootstrap -> platform (this stack is applied SECOND,
# after dev/bootstrap has provisioned remote state + registry + SAs).
module "platform" {
  source       = "../../../modules/platform"
  project_id   = "proxy-v0-dev"
  region       = "us-central1"
  env          = "dev"
  state_bucket = "tfstate-proxy-v0-dev-dev"
}
