# Deploy target set — exactly three network deployables (Doc 00 §"Deployables",
# A1 amendment). Each has its own <name>.tf resource file in this directory:
#   1. control_plane — Cloud Run, autoscaling: webhooks, connect page, API, WS gateway, auth.
#   2. per-meeting harness — GCE MIG (A1: moved off Cloud Run), one asyncio process per meeting.
#   3. code_intel — stateful GCE host, per-tenant encrypted disk: clones/index/graph.
#
# There is no fourth network deployable: transport/scribe/workroom are in-process
# packages hosted inside the per-meeting harness process, not separate services.

variable "project_id" {
  type    = string
  default = "proxy-v0"
}

variable "region" {
  type    = string
  default = "us-central1"
}
