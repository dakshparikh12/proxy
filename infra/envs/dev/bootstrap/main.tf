# === envs/dev/bootstrap === dev bootstrap stack.
# apply order per env: bootstrap -> platform (this stack is applied FIRST).
module "bootstrap" {
  source     = "../../../modules/bootstrap"
  project_id = "proxy-v0-dev"
  region     = "us-central1"
  env        = "dev"
}
