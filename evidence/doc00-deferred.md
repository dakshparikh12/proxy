
DEFERRED (genuinely spec-blocked, needs founder spec fix): ## SPEC_BLOCKED — M11 registry (AC-REG-002 vs AC-REG-004/005), 2026-07-17
; assert union == registry`, `test_m10_reg.py:75-77`) is jointly unsatisfiable with AC-REG-005's `issubclass(MessageType, enum.Enum)` (`:211`) and AC-REG-004's non-empty-registry requirement (`:158`): `typing.get_args` returns `()` for any Enum class (verified empirically), and no object is both an `isinstance(x, type)` Enum subclass and a subscripted generic with non-empty `get_args`, so with the CANONICAL-mandated Enum (`CANONICAL-DECISIONS.md:18`, `08-EXPERIENCE.md:188`) the union is always `set()` and can never equal the non-empty registry; AC-REG-002 was mis-transcribed from the stale Literal-era snippet at `00-FOUNDATION.md:303` and must be changed in the sealed bundle to `set(MessageType) == set(CHANNEL_REGISTRY)` per `09-VERIFICATION.md:16`, which the builder is forbidden to edit.


DEFERRED (genuinely spec-blocked, needs founder spec fix): is the only green" / "impossible without changing the arbiter ⇒ SPEC_BLOCKED, not license to edit the arbiter").
sageType)} == {str(k) for k in CHANNEL_REGISTRY}`, but sealed `AC-REG-005` (line 211) and CANONICAL-DECISIONS §1 lock `MessageType` as an `enum.Enum`, and `typing.get_args()` of any Enum class is `()` at the language level; with a non-empty registry (required by `AC-REG-004`), the equality is unsatisfiable by any product implementation. CANONICAL-DECISIONS §1 ("MessageType (an Enum)") and 09-VERIFICATION.md §2 ("`set(MessageType) == set(CHANNEL_REGISTRY)`") both confirm the Enum-iteration form is canonical and the §12 `get_args` snippet is superseded — so the defect is the sealed test transcribing the pre-Enum form, fixable only by rewriting line 77 to `{m.value for m in MessageType} == {str(k) for k in CHANNEL_REGISTRY}` (or `set(MessageType)`), which lives in builder-forbidden `tests/`.


DEFERRED (genuinely spec-blocked, needs founder spec fix): two-part fix above is the only path forward. Session ends here per the SPEC_BLOCKED protocol.
ich the shipped `registry.py` already implements correctly), whereas the criterion encodes the superseded `00-FOUNDATION §12` `get_args` idiom. Independently, `test_reg_001`'s inline `_AcReg001Probe` permanently pollutes the module-global `CHANNEL_REGISTRY` with `ac-reg-001-probe` and there is no reset fixture in either conftest, so reg_002 line 77 and reg_003's baseline fail on state no product code can un-taint. Both required fixes live in sealed `tests/` and are builder-forbidden, so the block stands: founder must (a) rewrite reg_002 line 75 to `{str(m.value) for m in MessageType}` and (b) add autouse snapshot/restore of `CHANNEL_REGISTRY` (or have reg_001 pop its probe in a `finally`); on that change the shipped `registry.py` passes reg_001..006 unchanged and the build resumes at M12.


DEFERRED (genuinely spec-blocked, needs founder spec fix): only founder action on the two-part sealed-file fix unblocks it. Session ends here per the SPEC_BLOCKED protocol.
uage level (empirically confirmed, and acknowledged by reg_005's own line-214 comment), the union is always `set()` and the assertion is unsatisfiable with a non-empty registry. reg_002's `get_args` predicate was transcribed verbatim from the stale, pre-Enum `00-FOUNDATION.md:303` snippet, which `CANONICAL-DECISIONS.md:18` and `09-VERIFICATION.md:16` explicitly supersede; because the offending expression lives inside the sealed test body (which the builder may not edit) and no product code can make `get_args` of an Enum non-empty, only a spec/test-bundle correction — rewriting the predicate to `set(m.value for m in MessageType) == set(CHANNEL_REGISTRY)` — can unblock this, and the shipped `registry.py` is already CANONICAL-correct and expected to pass reg_001–006 unchanged once corrected.


DEFERRED (genuinely spec-blocked, needs founder spec fix): re-joins onto ROOT → reads 0 bytes] remain SPEC_BLOCKED exactly as documented in sessions 3–7. Re-confirmed live.)
ap: `ten_001`'s `NON_SCOPED` (test line 111) omits `operation_runs`, forcing it to declare a `tenant_id`-reaching FK, while CANONICAL §2 + §11.2 and `AC-SUB-001`'s exact-column-set assertion lock `operation_runs` to 12 columns with a `text` `scope_id` and *no new column and no uuid handle to FK* — so no product schema can satisfy both, and the test itself already exempts the structurally identical text-keyed coordination store `sessions`, proving `operation_runs` was an oversight. Only a spec/bundle edit adding `operation_runs` to `test_m15_ten.py:111`'s `NON_SCOPED` resolves it. (Note: `inv_010` in this same last entry is an independent, equally genuine sealed-test contradiction — a text `'tenant-OFF'` seeded into a CANONICAL-mandated `uuid` column — and needs the same founder-side fix.)


DEFERRED (genuinely spec-blocked, needs founder spec fix): **reg_002 & obs_006 independently re-confirmed genuine SPEC_BLOCKED (not just trusted from prior logs):**
005) and a subscripted generic with non-empty `get_args` (reg_002), so the three sealed criteria are jointly unsatisfiable by any product implementation — confirmed live (reg_004/reg_005 and the shipped `assert_registry_closed()` all pass; reg_002 fails only at line 77 with `registry-only={'connect-repo','approve-draft','invite-proxy'}`). The criterion (`criteria.yaml:2493`, authority `R-DOC00-12-02` → Doc 00 §12) was frozen from `00-FOUNDATION.md:303`, which CANONICAL and `09-VERIFICATION.md:16` (`set(MessageType) == set(CHANNEL_REGISTRY)`) supersede. Only a sealed-file change — rewriting AC-REG-002/line 77 to the canonical member-iteration form — can resolve it, and that file is builder-forbidden; the shipped `libs/contracts/registry.py` is already CANONICAL-correct and needs no change.


DEFERRED (genuinely spec-blocked, needs founder spec fix): **reg_002 & obs_006 independently re-confirmed genuine SPEC_BLOCKED (not just trusted from prior logs):**
m in MessageType) == set(CHANNEL_REGISTRY)`.

ADJUDICATION: DEFER tests/doc00/test_m10_reg.py::test_reg_002_assert_registry_closed_passes_when_set_equal — Its inline predicate `{str(m) for m in get_args(MessageType)} == {str(k) for k in CHANNEL_REGISTRY}` is jointly unsatisfiable with AC-REG-005 (MessageType must be `enum.Enum` ⇒ `get_args(MessageType)==()` always) and AC-REG-004 (registry must be non-empty), since `set()` can never equal a non-empty set and no object is simultaneously an Enum class and a subscripted alias with non-empty `get_args`; the Enum reading is fixed by CANONICAL-DECISIONS.md:18 and 09-VERIFICATION.md:16, so the criterion is a stale-snippet mis-transcription that only a sealed-bundle spec change (rewrite to `set(MessageType) == set(CHANNEL_REGISTRY)`) can resolve.


DEFERRED (genuinely spec-blocked, needs founder spec fix): expected green with no further product change. Session ends here per the SPEC_BLOCKED protocol.
NONICAL-DECISIONS.md:18 lock `MessageType` as an `enum.Enum`, for which `get_args()` is always `()` (reproduced live: `get_args(MessageType) == ()`, registry `= {approve-draft, connect-repo, invite-proxy}`), yet the sealed body of `test_reg_002` (line 75-77) asserts `{str(m) for m in get_args(MessageType)} == set(CHANNEL_REGISTRY)`, which is `set() == {3 keys}` and cannot be satisfied by any product while the registry is non-empty (itself required by AC-REG-004). The authoritative reconciled spec (09-VERIFICATION.md:16) already specifies the correct Enum-member oracle `set(MessageType) == set(CHANNEL_REGISTRY)`, under which the shipped product is consistent — so the fix is a change to the sealed test's oracle line (a founder/spec action the builder is forbidden to make), not product code.


DEFERRED (genuinely spec-blocked, needs founder spec fix): **Founder fixes (one line each, unchanged):** (1) reg_002 line 77 → `set(m.value for m in MessageType) == set(CHANNEL_REGISTRY)`; (2) obs_006 read the absolute path directly; (3) inv_010 seed a real uuid; (4) add `operation_runs` to `test_m15_ten.py` `NON_SCOPED`. **Recommendation: halt builder re-invocation** — 13 independent sessions reproduce the identical result; only founder action on the four sealed one-liners advances doc00. Session ends per SPEC_BLOCKED protocol.
s_when_set_equal — The sealed test computes `union` via `get_args(MessageType)` in its own body, which is unconditionally `set()` because `AC-REG-005`/CANONICAL §1 lock `MessageType` as an `enum.Enum` (`get_args` of any Enum is `()`), while `AC-REG-004` locks the registry non-empty; no product code can make `set() == {non-empty}`, so reg_002 and reg_005 are mutually unsatisfiable. This is a genuine spec-level contradiction — `00-FOUNDATION.md:303`'s `set(get_args(MessageType))` presupposes a Literal/Union alias, directly conflicting with the locked `CANONICAL-DECISIONS.md §1` ("MessageType (an Enum)") and `09-VERIFICATION.md §2`'s `set(MessageType) == set(CHANNEL_REGISTRY)`; only a spec/sealed-test change (line 77 → `set(m.value for m in MessageType) == set(CHANNEL_REGISTRY)`) can fix it.


DEFERRED (genuinely spec-blocked, needs founder spec fix): 163/167; only founder action on the four sealed one-liners advances doc00. Session ends per SPEC_BLOCKED protocol.
be an Enum, for which `get_args(MessageType) == ()` unconditionally, yet `test_reg_002:75–77` requires `{str(m) for m in get_args(MessageType)} == set(CHANNEL_REGISTRY)` with a registry that `test_reg_004` forbids from being empty — and no object can be simultaneously an `isinstance(_, type)` Enum subclass and a subscripted generic with non-empty `get_args`. The criterion's `source_quote` is the superseded Literal-era `get_args` snippet from `00-FOUNDATION.md:303`, which `CANONICAL-DECISIONS.md:18` overrode; the canonical closure is member-iteration (`09-VERIFICATION.md:16`: `set(MessageType) == set(CHANNEL_REGISTRY)`). The fix (`reg_002:77` → `set(m.value for m in MessageType) == set(CHANNEL_REGISTRY)`) lives in builder-forbidden sealed files, so only a spec/bundle change can unblock it.


DEFERRED (genuinely spec-blocked, needs founder spec fix): Session ends per the SPEC_BLOCKED protocol.
est `test_reg_005:211-212` hard-enforces `issubclass(MessageType, enum.Enum)` while `test_reg_004` forces a non-empty registry; since `typing.get_args()` is non-empty only for generic aliases (all of which are not `type` instances) and returns `()` for every Enum class, the test's inline `union = {str(m) for m in get_args(MessageType)}` is permanently `set()`, making `union == registry` unsatisfiable for any non-empty registry — a language-level impossibility no product code can resolve, and one the builder cannot fix because the offending assertion is in the sealed test body; the correct closure `set(MessageType) == set(CHANNEL_REGISTRY)` is already mandated by `09-VERIFICATION.md:16`, so only a founder edit of the sealed criterion/test to that form (not a product change) can unblock it.


DEFERRED (genuinely spec-blocked, needs founder spec fix): SPEC_BLOCKED protocol.
 spec §11), and a `created_by text` instance-id; `sub_001` pins that exact set GREEN, while `ten_001` requires `operation_runs` to reach `tenant_id` via a declared FK chain. No existing column can bear such an FK without a type mismatch and referential-integrity failure against other GREEN tests, adding a column violates the canonical lock and `sub_001`, and the suite's own A-009 disposition resolves this AC-TEN-001 schema-pair conflict only for `meeting_cost`/`staged_drafts` (via `meeting_id→meetings`), leaving `operation_runs` with no structural path. The tables are provably mutually exclusive; only a spec/acceptance change (excluding `operation_runs` from `NON_SCOPED`, like `sessions`, since it is tenant-scoped at runtime through its polymorphic `scope_id`, not a schema FK) can fix it.


DEFERRED (genuinely spec-blocked, needs founder spec fix): route-around; nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
FER test_m10_reg.py::test_reg_002_assert_registry_closed_passes_when_set_equal — 00-FOUNDATION.md:303 writes the closure over `set(get_args(MessageType))`, but CANONICAL-DECISIONS.md:18 and 08-EXPERIENCE.md:188 lock `MessageType` as a `StrEnum`/`Enum`, for which `get_args()` is language-guaranteed to return `()`; the sealed test body (reg_002:75-77) recomputes `{str(m) for m in get_args(MessageType)}` and asserts it equals the non-empty `CHANNEL_REGISTRY`, which is unsatisfiable for any Enum and directly contradicts the GREEN reg_005:211/214 (which requires the Enum and concedes `get_args==()`) — a genuine spec self-contradiction that no product code can bridge and that only a spec fix (line 303 → `set(MessageType) == set(CHANNEL_REGISTRY)`, with reg_002:75 corrected in kind) can resolve.


DEFERRED (genuinely spec-blocked, needs founder spec fix): per the SPEC_BLOCKED protocol.
or m in MessageType}`, or reconciling §12 with CANONICAL) can advance it — the builder cannot touch sealed tests.

ADJUDICATION: DEFER tests/doc00/test_m10_reg.py::test_reg_002_assert_registry_closed_passes_when_set_equal — CANONICAL-DECISIONS §1 (line 18) locks `MessageType` as an `Enum`, enforced green by sealed reg_005 (`issubclass(MessageType, enum.Enum)`), yet 00-FOUNDATION §12 (line 303) and this test require `set(get_args(MessageType)) == set(CHANNEL_REGISTRY)`; `typing.get_args()` returns `()` for every Enum class as a language invariant, so the union is permanently empty while the registry is non-empty by contract — the two authoritative spec passages are mutually unsatisfiable for one object, an impossibility no product code can bridge and only a spec/sealed-test change can fix.


DEFERRED (genuinely spec-blocked, needs founder spec fix): touched; no route-around; nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
 the resolver; my adjudication touched no files.

The ruling stands:

**ADJUDICATION: DEFER tests/doc00/test_m10_reg.py::test_reg_002_assert_registry_closed_passes_when_set_equal** — `CANONICAL-DECISIONS.md:18` locks `MessageType` as an `Enum` (for which `get_args()` is always `()`), while `00-FOUNDATION.md:303`'s closure predicate `set(get_args(MessageType)) == set(CHANNEL_REGISTRY)` — encoded verbatim by this sealed test at line 77 — can only hold when `MessageType` is a non-empty `Literal`/`Union` alias. No single Python object is both an `Enum` subclass and a subscripted generic; both encoding tests are sealed; the live registry is already consistent and passes `assert_registry_closed()`. Genuine impossibility, resolvable only by a one-line spec edit to CANONICAL §1 or FOUNDATION §12.


DEFERRED (genuinely spec-blocked, needs founder spec fix): route-around; nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
aled test into a literal impossibility: `test_reg_005` (+CANONICAL-DECISIONS.md:18) locks `MessageType` as an `enum.Enum`, and `get_args()` of an Enum is unconditionally `()` (empirically confirmed, even after forcing `__args__`), while `test_reg_004` forces `CHANNEL_REGISTRY` non-empty — so reg_002's `{str(m) for m in get_args(MessageType)} == {str(k) for k in CHANNEL_REGISTRY}` reduces to `set() == {'connect-repo','approve-draft','invite-proxy'}`, which no permitted product implementation can satisfy; the sealed test transliterated the spec's illustrative `get_args()` pseudocode (00-FOUNDATION.md:303) instead of the Enum-correct member accessor its own sibling reg_006 uses, and only a founder edit to the sealed line (`{m.value for m in MessageType} == set(CHANNEL_REGISTRY)`) can fix it.


DEFERRED (genuinely spec-blocked, needs founder spec fix): touched; no route-around; nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
osed_passes_when_set_equal — CANONICAL-DECISIONS.md:18 locks `MessageType` as an `Enum` (enforced by reg_005:211 `issubclass(MessageType, enum.Enum)`), but the sealed reg_002 asserts `{str(m) for m in get_args(MessageType)} == set(CHANNEL_REGISTRY)` where reg_004:163 forces the registry non-empty; since `typing.get_args()` is invariantly `()` for every Enum class, the left side is unconditionally empty and the equality can never hold — the two sealed tests are language-level mutually unsatisfiable, reconcilable only by a founder edit to a sealed artifact (change reg_002 to `set(m.value for m in MessageType) == set(CHANNEL_REGISTRY)`, matching the spec's stated "type-union set-equal" intent at 00-FOUNDATION.md:295 and the product's own `_closure_values`), which product code cannot perform.


DEFERRED (genuinely spec-blocked, needs founder spec fix): speculatively. Session ends per the SPEC_BLOCKED protocol.
m.Enum` (sealed `test_m10_reg.py:211` enforces `issubclass(MessageType, enum.Enum)`, a passing test), while AC-REG-002's spec source-quote (`00-FOUNDATION.md:303`, `criteria.yaml:2493`) defines the closure oracle as `set(get_args(MessageType)) == set(CHANNEL_REGISTRY)`; since `get_args()` of any Enum is `()` (verified live: `∅` vs a non-empty registry `{connect-repo, approve-draft, invite-proxy}`, which REG-001/004 require to be non-empty), the two sealed criteria are simultaneously unsatisfiable by any product code — the spec itself is inconsistent (the predicate should read `{m.value for m in MessageType}`, which the live Enum's values already satisfy exactly), so only a founder edit to the sealed predicate (or to the Enum-vs-Literal decision) can resolve it, not a builder route-around.


DEFERRED (genuinely spec-blocked, needs founder spec fix): speculatively. Session ends per the SPEC_BLOCKED protocol.
es this impossible: 00-FOUNDATION.md:114-127 freezes `operation_runs` with no `tenant_id` column and a deliberately polymorphic `scope_id text` ("the one exception… which stays `text`", :289; "canonical shape… do not diverge"), CANONICAL §11.1 omits `operation_runs` from the enumerated `tenant_id uuid REFERENCES tenants` tables, and GREEN `test_sub_001` pins the table to exactly 12 columns — so neither adding a `tenant_id` column nor an FK on the polymorphic `text` `scope_id`/instance-id `created_by` is possible, while the test's cited oracle ("tenant_id in every schema") appears nowhere in the spec, whose isolation model is dispatch-funnel/`meeting_id`-presence based; only a founder edit adding `operation_runs` to the sealed `NON_SCOPED` set reconciles the test with the canonical schema.


DEFERRED (genuinely spec-blocked, needs founder spec fix): route-around; nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
The builder's SPEC_BLOCKED is correct here.

ADJUDICATION: DEFER test_m10_reg.py::test_reg_002_assert_registry_closed_passes_when_set_equal — CANONICAL-DECISIONS.md:18 *locks* `MessageType` to an `Enum`, which the override precedence in CLAUDE.md makes binding, while 00-FOUNDATION.md:303's `set(get_args(MessageType))` closure predicate — copied verbatim into the sealed assertion at test_m10_reg.py:75-77 — is only satisfiable if `get_args(MessageType)` is non-empty, which is impossible for any Enum (get_args of a non-generic class is always `()`); reg_005:211 independently forces the Enum, so no conforming product can make `union == registry` hold, and the only repair is editing the sealed test line to read the Enum's members instead of `get_args`, which is a founder-only spec/test change.


DEFERRED (genuinely spec-blocked, needs founder spec fix): Session ends per the SPEC_BLOCKED protocol.
num)` (line 213), because `typing.get_args()` of any class — every Enum included — is always `()`, so the left set is empty while the registry is non-empty (reg_001/004); the two criteria constrain the identical imported `libs.contracts.MessageType` to be an Enum class and a parameterized generic alias at once, which is impossible in Python. AC-REG-005 correctly encodes the locked CANONICAL-DECISIONS.md:18 mandate ("discriminator `MessageType` (an `Enum`)"), so the Enum side is authoritative and the `get_args` form in reg_002 (mirroring the stale illustrative snippet at 00-FOUNDATION.md:303) is the spec defect; only a founder edit to the sealed test — comparing `{m.value for m in MessageType}` to the registry — can resolve it, and no product code in libs/ or services/ can make both green.


DEFERRED (genuinely spec-blocked, needs founder spec fix): builder re-invocation; route these four sealed one-liners to a founder.** Session ends per the SPEC_BLOCKED protocol.
mplementation.

ADJUDICATION: DEFER tests/doc00/test_m10_reg.py::test_reg_002_assert_registry_closed_passes_when_set_equal — reg_002:77 hard-codes `{str(m) for m in get_args(MessageType)}`, but CANONICAL-DECISIONS.md:18 locks `MessageType` as an Enum (independently enforced by the passing, sealed reg_005), and `typing.get_args()` of any Enum is unconditionally `()`; so the left set is empty while the registry is non-empty, making `set() == {registry keys}` unsatisfiable for every possible product implementation. The §12 `get_args(MessageType)` snippet is stale pseudocode from the pre-Enum `Literal[...]` discriminator era that CANONICAL overrode; reconciling line 77 to the Enum value-set form is a sealed-test/spec edit the builder cannot make — a genuine contradiction, not mere difficulty.


DEFERRED (genuinely spec-blocked, needs founder spec fix): SPEC_BLOCKED protocol.
closure as `set(MessageType) == set(CHANNEL_REGISTRY)`, not `get_args`). `get_args()` of any Enum class is `()` at the language level, and `test_reg_005:211` independently seals `issubclass(MessageType, enum.Enum)`, so no object can be both an Enum and a subscripted generic — these two sealed tests are mutually exclusive and line 77 reduces to `set() == {non-empty registry}`, false for every conformant product. The criterion's own oracle (`closure_assert_pass`, threshold `false_closure_failure: 0`) is already met by line 71; the impossibility lives only in the test's extra re-derivation, which the builder cannot edit. Fix is a sealed-test change (`get_args(MessageType)` → `set(MessageType)`) aligning T-REG-002 to the canonical closure form — a spec/test-authority edit, route to a founder.


DEFERRED (genuinely spec-blocked, needs founder spec fix): SPEC_BLOCKED protocol.
GISTRY` — and `typing.get_args()` of any `enum.Enum` class is always `()` (a plain class has no `__args__`; reg_005:214's own comment concedes this), so the union is `∅ ≠ CHANNEL_REGISTRY`. No object is simultaneously an `enum.Enum` subclass and a subscripted generic, so reg_002 and reg_005 cannot both pass; since `tests/`, `product/`, and `acceptance/` are all guard-protected, the builder cannot reconcile them in product code. The spec is internally inconsistent (CANONICAL §1 mandates an Enum while FOUNDATION:303's `get_args`-based `assert_registry_closed()` presupposes a `Literal`/`Union` alias), so only a spec/test change fixes it: rewrite reg_002's closure predicate to `set(m.value for m in MessageType) == set(CHANNEL_REGISTRY)` per CANONICAL's override, keeping `MessageType` an Enum.


DEFERRED (genuinely spec-blocked, needs founder spec fix): nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
ageType` as an `Enum` (and CLAUDE.md ranks CANONICAL as an override over 00-FOUNDATION, with reg_005:211 sealing `issubclass(MessageType, enum.Enum)`), yet reg_002:77 asserts inline that `set(get_args(MessageType)) == set(CHANNEL_REGISTRY)`; since `get_args()` of any Enum is empirically always `()` while reg_004 forces the registry non-empty, this evaluates to `∅ == {non-empty}` for every conceivable product implementation, and the `get_args(MessageType)` call lives inside the builder-forbidden sealed test body where no `libs/`/`services/` code can reach it — a true contradiction between CANONICAL §1 and FOUNDATION §12's `get_args` formulation that only a spec/test amendment (e.g. `set(m.value for m in MessageType) == set(CHANNEL_REGISTRY)`) can resolve, exactly as the builder identified.


DEFERRED (genuinely spec-blocked, needs founder spec fix): per the SPEC_BLOCKED protocol.
M17, so nothing downstream can be built around it.

ADJUDICATION: DEFER test_m10_reg.py::test_reg_002_assert_registry_closed_passes_when_set_equal — CANONICAL-DECISIONS.md:18 locks `MessageType` as an `enum.Enum` (enforced by sealed reg_005's `issubclass(MessageType, enum.Enum)`), while 00-FOUNDATION.md:303 defines registry closure as `set(get_args(MessageType)) == set(CHANNEL_REGISTRY)` and reg_002:75-77 re-asserts that set-equality in the sealed body; `typing.get_args()` of any Enum class is unconditionally `()`, so the union is empty and can never equal the non-empty registry — a genuine spec self-contradiction (Enum vs. get_args-able type-union) that no product implementation can satisfy and only a spec/sealed-test amendment (closure over `{m.value for m in MessageType}`) can resolve.


DEFERRED (genuinely spec-blocked, needs founder spec fix): touched; no route-around; nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
adjudication.

ADJUDICATION: DEFER test_m10_reg.py::test_reg_002_assert_registry_closed_passes_when_set_equal — CANONICAL-DECISIONS.md:18 locks `MessageType` as an `Enum`, which reg_005:211 correctly enforces; but reg_002:75-77 asserts `{str(m) for m in get_args(MessageType)} == set(CHANNEL_REGISTRY)`, and `get_args()` of any `enum.Enum` subclass is unconditionally `()` (verified), while reg_001/003/004 require the registry non-empty — so `∅ == {3 keys}` is unsatisfiable by any product code, the offending `get_args` expression lives inside the sealed, builder-forbidden test body (a stale copy of the pre-CANONICAL §12 snippet at 00-FOUNDATION.md:303), and only a one-line test edit (`set(m.value for m in MessageType) == set(CHANNEL_REGISTRY)`) can reconcile it with the locked Enum decision.


DEFERRED (genuinely spec-blocked, needs founder spec fix): nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
.py::test_reg_002_assert_registry_closed_passes_when_set_equal — CANONICAL-DECISIONS.md:18 locks `MessageType` as an `Enum` (enforced/green by reg_005:211), which makes `get_args(MessageType)` unconditionally `()`; yet reg_002:75–77 requires `{str(m) for m in get_args(MessageType)}` to set-equal the non-empty 3-key `CHANNEL_REGISTRY` (mirroring 00-FOUNDATION.md:303's `get_args`-based `assert_registry_closed`, which only holds for a type-union). An Enum cannot have non-empty `get_args`, and that union is computed inline in the sealed test body, so no product definition can satisfy reg_002 and reg_005 simultaneously — a genuine spec/test contradiction fixable only by amending the sealed test (or the §12 snippet) to compare `set(m.value for m in MessageType)` against `set(CHANNEL_REGISTRY)`.


DEFERRED (genuinely spec-blocked, needs founder spec fix): **SPEC_BLOCKED — named precisely:** `tests/doc00/test_m10_reg.py:75,77` (AC-REG-002) is mutually exclusive with
5:211 (and CANONICAL §1) require `MessageType` to be an `enum.Enum` subclass, for which `typing.get_args()` is unconditionally `()`; thus `union` is always `set()` and can never equal the 3-key registry that reg_001/reg_003/`assert_registry_closed()` require to be populated. No `libs/`/`services/` object can be both a `type`-subclass-of-Enum and yield non-empty `get_args`, and the only corrective edit (reg_002:75 → `{str(m.value) for m in MessageType}`, matching the file's own line-251 fallback and the product's `_closure_values`) sits in a guard-protected sealed test the builder cannot touch. The root cause is a spec contradiction — `00-FOUNDATION.md:303` models `MessageType` as `get_args`-able while `AC-REG-005`/CANONICAL mandate an Enum — that only a founder's spec/test change can fix.


DEFERRED (builder + debugger both stuck) — plumb later: tests/doc00/test_m11_obs.py::test_obs_006_one_idempotent_hardening_script_full_control_set
anav/Desktop/proxy/tests/doc00/test_m11_obs.py:198: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED tests/doc00/test_m11_obs.py::test_obs_006_one_idempotent_hardening_script_full_control_set
!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!
1 failed, 125 passed, 1 deselected, 1 warning in 9.57s


DEFERRED (genuinely spec-blocked, needs founder spec fix): route-around; nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
ty `get_args` — is not a `type` and would fail reg_005, so the two sealed criteria are mutually exclusive and no legitimate `libs/`/`services/` edit can satisfy both. The FOUNDATION closure pseudocode was written for a `get_args`-subscriptable discriminator and the later locked CANONICAL decision changed `MessageType` to an `Enum` without updating that predicate (or AC-REG-002, which quotes it); the product already implements the correct Enum-member closure and `assert_registry_closed()` passes. The one-line fix — `union = {str(m.value) for m in MessageType}`, matching the test file's own `:251` fallback and the shipped implementation — lives in the sealed test tree the builder cannot edit, so this is a genuine spec contradiction requiring a founder-side spec/test change, not a build gap.


DEFERRED (genuinely spec-blocked, needs founder spec fix): touched; no route-around; nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
 "MessageType (an `Enum`)", and Python guarantees `get_args()` of any Enum class is `()` (verified by live probe and conceded by the same file's `:214` comment and `:251` fallback); therefore the sealed re-check at `:75` (`union = {str(m) for m in get_args(MessageType)}` asserted equal to the 3-key registry) is unconditionally `set() == {3 keys}` and unsatisfiable by any product object, while the criterion's actual normative oracle (`closure_assert_pass`, `assert_registry_closed()` passes) is already green — this is a genuine internal contradiction between the §12 `get_args` sketch transcribed into `:75` and the locked CANONICAL Enum decision, fixable only by editing the sealed test line (to `{str(m.value) for m in MessageType}`) or the spec sketch, neither of which the builder may touch.


DEFERRED (genuinely spec-blocked, needs founder spec fix): nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
ry_closed()` already does.

I modified no files.

ADJUDICATION: DEFER tests/doc00/test_m10_reg.py::test_reg_002_assert_registry_closed_passes_when_set_equal — 00-FOUNDATION.md §12 (line 303) mandates `set(get_args(MessageType)) == set(CHANNEL_REGISTRY)` while CANONICAL-DECISIONS.md:18 locks `MessageType` to be a Python `Enum` ("not an open alias"); `typing.get_args` on any Enum is unconditionally `()` (verified empirically), so the spec's own closure assertion — transcribed verbatim as reg_002's inline `get_args(MessageType)` check — is unsatisfiable against a non-empty registry, and no product-code edit can change a stdlib builtin's result on a CANONICAL-locked Enum; only a spec change to §12 (comparing enum members, not `get_args`) plus the corresponding sealed-test line can resolve it.


DEFERRED (genuinely spec-blocked, needs founder spec fix): CANONICAL file touched; no route-around; nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
`, and reg_005:211 correctly enforces that; but the sealed assertion at reg_002:75 (`union = {str(m) for m in get_args(MessageType)}`) requires `get_args(MessageType)` to be non-empty, which `typing.get_args` returns `()` for on any Enum class (empirically verified), while `CHANNEL_REGISTRY` is required non-empty by reg_001/reg_004 — so `set() == {3 keys}` at reg_002:77 is language-level unsatisfiable for the CANONICAL-mandated Enum, and no `libs/`/`services/` edit can satisfy both reg_002:77 and reg_005:211 at once. The only fix is a one-line change to the sealed, builder-forbidden test (`test_m10_reg.py:75` → `{str(m.value) for m in MessageType}`, mirroring the file's own reg_006:251 fallback), so this is a genuine sealed-criterion contradiction that only a spec/test change can resolve.


DEFERRED (genuinely spec-blocked, needs founder spec fix): Session ends per the SPEC_BLOCKED protocol.
_equal — `test_reg_002` requires `get_args(MessageType)` to set-equal the non-empty `CHANNEL_REGISTRY` (forcing `MessageType` to be a `get_args`-able `Literal`/`Union`), while the sibling sealed `test_reg_005:211` asserts `issubclass(MessageType, enum.Enum)` and `test_reg_004:158` forces the registry non-empty; since `typing.get_args()` of any class is unconditionally `()` (a fact `reg_005:213` itself states), `reg_002`'s equality is `set() == {non-empty}`, unsatisfiable by any product code — and `CANONICAL-DECISIONS.md:18` ("discriminator `MessageType` (an `Enum`)") sides with `reg_005`, making `reg_002`'s `get_args` predicate the defect. Only a sealed-file edit to `test_m10_reg.py:74` (→ `union = {str(m.value) for m in MessageType}`), which the builder is forbidden to touch, can fix it.


DEFERRED (genuinely spec-blocked, needs founder spec fix): nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
 any `enum.Enum` subclass is `()` — a fact the sealed suite itself asserts and comments at `test_m10_reg.py:214` — so the `union` set is unconditionally empty while `test_m10_reg.py:77` requires it to equal the non-empty CANONICAL registry. No definition of `MessageType`, `CHANNEL_REGISTRY`, or `assert_registry_closed()` under `libs/`/`services/` can satisfy both an Enum discriminator and a non-empty `get_args`, and the sealed test carrying the contradiction is in `harness/guard.py` `PROTECTED`, so the builder cannot touch it. The founder must reconcile the spec — either make `MessageType` a `Literal`-style alias in CANONICAL, or change FOUNDATION §12 / AC-REG-002 and `test_m10_reg.py:75` to iterate enum members (`{str(m.value) for m in MessageType}`) — before this criterion can go green.


DEFERRED (genuinely spec-blocked, needs founder spec fix): CANONICAL file touched; no route-around; nothing built speculatively. Session ends per the SPEC_BLOCKED protocol.
om the 42-session prose chain.

ADJUDICATION: DEFER test_m10_reg.py::test_reg_002_assert_registry_closed_passes_when_set_equal — `CANONICAL-DECISIONS.md:18` locks `MessageType` as an `Enum`, while `00-FOUNDATION.md:303` defines registry closure as `set(get_args(MessageType)) == set(CHANNEL_REGISTRY)`; since `get_args()` of any Enum is verifiably `()`, `reg_002`'s `union` is unconditionally empty while the CANONICAL registry is non-empty, so `union == registry` (and the spec's own `assert_registry_closed`) can never hold for a real registry. `reg_005` independently forces the Enum, so the two sealed blocking tests are mutually unsatisfiable by any `libs/`/`services/` code; only a spec change (make `MessageType` a `Literal`/Union alias, or redefine closure over Enum members) can resolve it.


DEFERRED (genuinely spec-blocked, needs founder spec fix): harness/CANONICAL file touched; no route-around; nothing built speculatively. Session ends per the SPEC_BLOCKED
rt_registry_closed_passes_when_set_equal — CANONICAL-DECISIONS.md:18 locks `MessageType` as an `Enum`, and reg_005:211 correctly mandates `issubclass(MessageType, enum.Enum)`; but Python's `get_args()` of any Enum class is invariably `()` (verified live in fresh context), so this test's own line-75 `union = {str(m) for m in get_args(MessageType)}` is permanently empty while line-76 `registry` holds the three locked non-empty keys, making line-77's set-equality unsatisfiable by any `libs/`/`services/` edit — the product's `assert_registry_closed()` already passes at line 71. The defect is a sealed-test bug in a builder-forbidden path (line 75 must read `{str(m.value) for m in MessageType}`, mirroring what the product closure already does); only a founder edit to the sealed test can fix it.

