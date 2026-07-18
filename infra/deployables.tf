# Deploy target set — exactly three deployables (Doc 00 §"Deployables", A1 amendment).
#   1. control_plane   — Cloud Run, autoscaling: webhooks, connect page, API, WS gateway, auth.
#   2. meeting_runtime — GCE MIG (A1: moved off Cloud Run): one asyncio harness process/meeting.
#   3. code_intel      — stateful GCE host, per-tenant encrypted volume: clones/index/graph.
#
# There is no fourth network deployable: transport/scribe/workroom are in-process
# packages hosted by meeting_runtime, not separate services.

locals {
  deployables = ["control_plane", "meeting_runtime", "code_intel"]
}
