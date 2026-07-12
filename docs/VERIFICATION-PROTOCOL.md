# Proxy · Verification Protocol (the 7 layers) — from your doc, with two upgrades
Seven independent detectors so gaming all at once is implausible. Run all seven per section, in order.

1 **Spec→failing tests** — every acceptance criterion → a pytest assertion vs a fixture ground truth. (Attended, per section.)
2 **Contract tests** — every seam (doc §Seams) gets a shape/behavior test, implementation-independent.
3 **Implementation gates** — ruff + mypy --strict + bandit in verify.sh, before pytest.
4 **Mutation testing** — mutmut on the 1–2 highest-judgment files, ≥80% kill rate.
5 **Adversarial review** — 5a: **`spec-compliance-review` subagent** (fresh context, automated — UPGRADE from a manual fresh session) + 5b: Pranav live-demo of every promise, <2 min each.
6 **Real-repo confrontation → REAL-DATA EVAL GATE** (UPGRADE: automated + thresholded). The `eval-gate` skill / `eval-runner` subagent pulls real repos, builds the component, and scores Tier-2 [eval] criteria against golden keys at thresholds — on messy/single/monorepo estates. This is the product arbiter; a section is not done until it passes here.
7 **Canary + monitoring** — post-launch only: staged rollout + telemetry; reuses the Layer-6 eval graders on sampled production traces.

Two arbiters, two cadences: **pytest (Layers 1–4) per pass; eval gate (Layer 6) per section.** Evidence folder + dual-founder signoff + branch protection (incl. admins) make done self-enforcing.
