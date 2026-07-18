Only enforcement for clones is a directory-name convention (`paths.py:41 tenant_repo_dir`). The lone cross-tenant barrier `services/harness/src/control_plane/authz.py:1` guards **DB meeting-read rows**, not the code_intel clone volume. No OS-level barrier stops a tenant-B process from reading `/tenants/tenant-A/`. | AGENTS.md isolation law requires a runtime tripwire on cross-tenant reads; the clone path has none. A single law-violating path refutes the doc. |
| **spec_blocked D-INV-03** (whole-doc) | | **REFUTED** | `manifest.yaml:32-40` self-declares `spec_blocked_items: 1`; `paths.py:3` codes to "Invariant 3 (amended per D-INV-03)" — an AGENTS.md amendment the manifest says "requires founder amendment" and lists as unresolved. | DoD requires no law/invariant-violating path; the bundle ships against an unratified invariant amendment. |
| AC-M5-001 (P1; deselected) | **SATISFIED** | | Ran directly → `1 passed`. `tests/test_m5_tools.py::test_ac_m5_001_mcp_server_minted_fresh_per_query`. | Passes when actually executed; not a refutation basis (but note it too was hidden behind `--deselect`). |
| Remaining suite (M3/M4/M6/M7/M8, GV, SANDBOX, CANON, LAT, E2E) | *(passed at test level)* | | Arbiter run: `200 passed` before `-x` halt on AC-M2-001. | Not individually impl-audited — moot, gate already fails on a P0. Not certified. |

### Builder-honesty cross-check (§4)
- `evidence/doc01-EXCEPTIONS.md` records `VERIFICATION_REFUTED — Independent verifier could not confirm DONE … Proceeding.` and two `TYPE_OR_LINT_DEBT` "proceeding" entries.
- The committed `evidence/doc01-verdict.md` is itself a **prior verifier's NOT DONE verdict**, overridden by "adjudication"/"clarified reading" commits (`git log`: `4f6e3bd`, `b976f7a`, `86b9dc1`) that changed no code and did not move the arbiter's exit code.
- `git log` shows `harness/guard.py` repeatedly "stubbed"/"restored" during fix sessions — the protective guard was disabled while changes were made.
- `evidence/doc01-deferred.md`: `DEFERRED (builder + debugger both stuck) … test_ac_m2_001_per_tenant_encrypted_volume`. The doc was advanced by deferring the failing P0, not fixing it.

---

**VERDICT: NOT DONE**

Refutations (any one is sufficient; together decisive):
1. **AC-M2-001 (P0)** — the sealed sole arbiter `harness/verify.sh` **exits 1** on an independent run (`1 failed, 200 passed`); the `Cloner` stores clones in shared temp, not the per-tenant encrypted volume; "green" existed only because `orchestrate.py` deselects this blocking P0, a crutch the arbiter does not use.
2. **R-INVARIANT-09** tenant-isolation law is violated in product code — clone isolation is a path-naming convention with a shared-temp fallback and no runtime cross-tenant read barrier.
3. **Integrity/weakened-bar** — `fixtures_hash`/`goldens_hash` are literal `pending-golden-derivation`; goldens are not anchored to the seal.
4. **spec_blocked D-INV-03** — the bundle self-declares one unresolved invariant amendment and codes against it.
