# v2 build-loop — SDD progress ledger
Plan: docs/superpowers/plans/2026-07-22-v2-build-loop.md
Baseline: offline suite 745 passed / 2 pre-existing benign fails (see memory).
Env: venv rebuilt (iCloud corruption); restore = `uv sync --all-packages` + pinned tools.
Plan review: REWORK — folding fixes A1/A2/A3/A4/A5/B3/B5/C1/C2/C5 before subagent phases.

Task 0.1: complete (guard stand-down baseline committed)
Task 1.1: complete (coverage_gate salvaged, 2 tests green, commit 799341a)
Task 1.2: complete (extraction_count_gate salvaged + A5 fix + injectable evidence_dir, 3 tests green, commit 3e2f1fe)
Task 1.3: complete (history docs + orchestrator reports archived)
