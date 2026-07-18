ion — but three did not.

## Spec behaviors with NO covering criterion

**1. Sandbox egress is default-deny.**
> §15 Safety floor: *"…the SDK-isolation triad on every workroom/repo `query()` (tools land in the sandbox, not the host) · **sandbox egress default-deny** · no live secrets in sandboxes (scoped short-lived JWTs)…"*

No criterion asserts sandbox network egress is default-denied. The clause immediately after it (per-sandbox JWT / no-live-secrets) is covered by `AC-INV-009`, and the one before (isolation triad) by `AC-CI-003` — but egress-default-deny has no criterion, no requirement (`R-DOC00-15-*` skips it), and no oracle. `egress` appears in criteria only in `AC-HOST-004` (control_plane Direct-VPC egress — unrelated).

**2. Read-only repo access (`contents:read`).**
> §15 Safety floor: *"**read-only repo access (`contents:read`)** · secrets excluded from index/map/context/sandbox/logs…"*

The very first safety-floor clause. No criterion asserts the GitHub App / Nango grant is scoped read-only. `AC-INV-007` references only `contents:write` (the push *Expansion* seam); nothing pins the V0 baseline scope to `contents:read`. `contents:read` / "read-only repo" = 0 hits across both criteria and requirements files.

**3. Canonical clones never execute repo code.**
> §15 Safety floor: *"…staged-drafts-only for world-touching acts (enforced via `disallowed_tools`) · **canonical clones never execute repo code** · a build is 'verified' only past a separate critic…"*

No criterion asserts the code_intel clone/scan path never executes the cloned repo's own code (build scripts, git hooks, postinstall). The closest, `AC-OBS-006`, asserts the *host-hardening* clause "arbitrary code execution only ever inside E2B (never on our host)" — a general host property, not the specific "the clone is inert data, never run" invariant. (Weakest of the three — flag as partially-adjacent, not fully absent.)

---

**Scope note (not counted as gaps):** doc00's own `requirements.yaml` preamble states that behaviors "whose runtime enforcement lives in a downstream service doc (Doc 01/02/03/04/05) are carried as `dispositions.yaml` entries … NOT as uncoverable requirements." Accordingly I did **not** flag the five laws' output behaviors (grounded-or-silent `file:line`, never-overstate `resolved`/`lower-bound`, <200ms barge-in) or the §15 **agentic efficiency floor** (1-hr-TTL prompt caching, context compaction, per-disposition tool subsets, targeted extended-thinking, sampled quality gate, tool-result reuse) — all Doc 04/05-enforced and correctly outside doc00's provable surface. One caveat worth surfacing: **`acceptance/doc00/` has no `dispositions.yaml`** (doc01 does), so those delegated behaviors are not formally recorded anywhere — but that's a missing-artifact observation, not a missing-criterion gap.

The three items above are genuine: each is a foundation-provable safety invariant doc00 explicitly consolidates in §15, alongside siblings that *were* given criteria.
