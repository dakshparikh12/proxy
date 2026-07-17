ct', **GCE-per-meeting**)."

But AMENDMENT A1 (`R-DOC00-4-05`, `AC-HOST-005`) mandates `meeting_runtime` as **GCE MIG, one process per meeting**, and AGENTS.md §Deployables describes it as "GCE MIG … one asyncio harness process per meeting." A banned-strings guard that trips on "GCE per meeting" collides head-on with the A1 deploy definition. `AC-CI-007`'s example tokens conveniently sidestep this, but the requirement's own source authority bans it. This contradiction is nowhere in `ambiguities.yaml` and must be resolved (which token is actually banned, given A1 revived GCE-per-meeting).

**GAP-17 (MODERATE). Intra-Doc-00 topology contradiction on `meeting_runtime` is unrouted, while a lesser one was.** §5.3 (line 164) still reads:
> "the `meeting_runtime` harness itself is a **Cloud Run process**, not a provisioned resource."

This directly contradicts A1 / `AC-HOST-005` (`{meeting_runtime_on_cloudrun: 0}`). The bundle *did* route the analogous stale-prose issue — `A-004` handles the stale `in_meeting` status wording — so leaving this one unrouted is an inconsistency in treatment. A build-loop reading §5.3 literally is led to the wrong topology; it deserves an ambiguity/conformance note.

**GAP-18 (MODERATE). The cost-breaker's cap basis is contradictory between `AC-INV-002` and `AC-INV-003`.** §12.7 insists task spend is "never folded into the baseline" (`AC-INV-002`), yet §15's lean meter says "`check_meeting_budget()` reads **the sum** [model+transport+e2b], gated by … soft cap → degrade; hard cap → notes-only" (`AC-INV-003`). If the breaker applies the listening-baseline caps (`hard_cap_frac=1.0`) to a sum that *includes* `e2b_usd`, then task work trips the listening breaker and forces "notes-only" — the precise outcome §12.7 says is "arithmetically false and would force the breaker to kill the product's real work." The two criteria encode conflicting behaviors and the cap's basis (listening-tier subset vs full sum) is never pinned. This belongs in `ambiguities.yaml`, not silently split across two P1 criteria.

---

### Lower-confidence notes (surfaced, not blocking)
- **BehaviorConfig typed-not-YAML** (CANONICAL §12.5) has no criterion; `D-LAW-04` cites `AC-CFG-005` as its cover, but `AC-CFG-005` tests `config/defaults.toml` tunables, not that behaviors are typed Python (not YAML). Mis-routed disposition.
- **"No raw source in logs/artifacts"** (AGENTS.md invariant 3, amended) is broader than `AC-INV-008`'s secret-sentinel redaction and is uncovered (Sentry scrubbing / structlog have no source-redaction criterion).
- **Multi-repo tenant "pick at invite time"** (§5.7) and **code_intel volume daily snapshots** (§13) — observable, no criterion; plausibly downstream but undispositioned.

The strongest items to fix before seal: **GAP-1, GAP-2, GAP-11, GAP-13, GAP-16** (a P1 contract type unchecked, the cost-meter metadata contract unchecked, the headline latency vision uncaptured, a P0 emit-frontier gap, and a self-contradicting banned-string).
