## v2 loop smoke — 4259699 — 2026-07-22
- Stop gate: BLOCKS on passes:false, ALLOWS on passes:true (done-check --task exit 1->0).
- BUG FOUND + FIXED: stop_gate.sh '$(done-check) || true; RC=$?' clobbered exit code -> gate never blocked. Removed '|| true'.
- Regression locked: regressions/test_stop_gate_blocks_red_task.py (fails on bug, passes on fix).
- pretool_guard: live-blocked an Edit to tests/scripts/test_smoke_task.py ('builder-read-only path').
