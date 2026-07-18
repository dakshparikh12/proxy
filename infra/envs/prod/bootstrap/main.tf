# === envs/prod/bootstrap === prod bootstrap stack.
# apply order per env: bootstrap -> platform (this stack is applied FIRST).
module "bootstrap" {
  source     = "../../../modules/bootstrap"
  project_id = "proxy-v0-prod"
  region     = "us-central1"
  env        = "prod"
}
