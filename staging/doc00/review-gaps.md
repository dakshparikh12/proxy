 `covered_downstream`). This is a completeness hole in the disposition ledger: a reader can't tell whether the consent matrix was considered-and-deferred or simply missed. Low severity (it is genuinely a Doc 02/04 behavior), but by the bundle's own "atomize into a requirement OR carry a disposition" rule (dispositions.yaml header), it should be recorded.

---

## GAP-6 — OMITTED (minor): the guarded `add-secret.sh` behavior has no criterion

**Spec (§7):** *"OAuth/Claude values set out-of-band via a guarded `add-secret.sh` (**silent input, allowlisted kinds, fingerprint confirm** — copied from ~/platform)."*

This is a concrete, observable secret-handling tool with three named safety properties (silent input, allowlisted kinds, fingerprint confirm). No criterion asserts it exists or enforces those properties. AC-CFG-006/007/008 cover Secret Manager wiring and drift but not this operator entry-point. Minor, but it is a named foundation artifact with security-relevant guarantees left uncovered.

---

## GAP-7 — Un-routed stale-value contradiction (editorial): `heartbeat_s=30` literal vs `HEARTBEAT_S≈10`

**Spec (§5.1 signature):** `async def with_operation_run(db, scope_id, op_type, *, heartbeat_s=30)` — versus the same section's fencing paragraph *"HEARTBEAT_S≈10 / STALE_AFTER_S≈40 (config/defaults.toml)."*

Ambiguity A-002 resolves the `≈10`/`≈40` **tildes** to the config values, but it never addresses the concrete literal `heartbeat_s=30` in the canonical function signature — a third, contradictory number in the same code block. A-002's problem statement only cites the approximate tildes. This is minor (config wins, and AC-SUB-005 references config `~40s`), but it is a same-section numeric contradiction that was not routed, and a build could faithfully copy the `=30` default.

---

### Lower-confidence note (not counted as a hard gap)
The §12.3 delivery invariant — *"the projector is **pure rendering** … It never auto-extracts a headline from the raw stream and decides to speak"* — is part of build-order step 1b (the projector is built with Doc 00). It is *partially* protected by AC-SUB-035 (a projector that auto-speaks via an ungated verb would trip the emit-frontier completeness check), but no criterion positively asserts the projector is non-delivery-authority / model-chosen channel only. I judged this adequately backstopped by AC-SUB-035 + the §12.3 authority residing in Doc 04/08, so I record it as an observation rather than a gap.

---

**Verdict:** Not approved. The bundle is strong and its serious concurrency/security/schema frontiers are well-decomposed, but GAP-1 (silent-build sandbox reap) and GAP-3 (hardcoded-secret-literal has no gate) are substantive omitted behaviors, GAP-2 is a real ordering/race gap, and GAP-4 is a self-inconsistency against the bundle's own schema-parity principle. GAP-3, GAP-4, and GAP-7 additionally involve spec-level inconsistencies that should be routed through `ambiguities.yaml` rather than left implicit.
