ic heads` which exits 0 regardless of head count — the "fail-loud in both" contract is violated in the pre-commit path and untested. |
| AC-INV-007 (P1) | PLAUSIBLE | `services/…/workroom/drafts.py:82` | No-push is genuinely enforced, but `approval_recorded=True` is a hardcoded constant — the "records approval" half is stub-grade. |
| AC-INV-011 (P1) | PLAUSIBLE | `services/…/harness/accept_route.py:20,61` | Authz/CSRF/idempotency logic real, but draft→tenant map hardcoded to the fixture (`{"d-1":"tenant-A"}`) and the check reads the client-supplied `request.tenant`. |
| AC-DOCK-004 (P2) | PLAUSIBLE | `deploy/Dockerfile:18` | Only the `LABEL … sandbox-image-hash` presence is checked; the fail-closed mismatch behavior (`mismatched_pair_runs: 0`) is neither implemented nor exercised (`deploy/promote.sh` does no hash verification). |
| **All remaining ~145 blocking criteria** | **SATISFIED** (section-level) | see below | Independently verified as genuine by three fresh-context sub-reviewers running the real tests against real impls. |

**Sections confirmed SOLID (real impl + teeth + real trace):**
- **AC-SUB-002** (P0 concurrency) — real partial unique index `migrations/versions/0001_substrate.py`; test inserts a second concurrent running row and observes a real `UniqueViolation`.
- **AC-HOST-013/014** (P0 crypto isolation) — real AES-GCM per-tenant distinct keys (`services/code_intel/.../crypto.py`); test encrypts A+B, shreds A's key, proves A undecryptable while B survives.
- **AC-INV-010** (offboarding sweep) — `libs/ops/.../reconcile.py:93` genuinely `DELETE`s tenant rows + `gcs.delete_prefix`; test seeds real Postgres and asserts deletion + neighbor untouched.
- CMP (Pydantic/Literal introspection, real `stream_deltas` delta-izer incl. double-application corruption), REPO/HOST/IAC/DOCK/DB static oracles over real artifacts, BOOT (real settings crash / EPIPE handler / graceful shutdown / 3 auth modes), CFG (real routing + semaphore), REG-001/002/004/005/006, OBS, CON (naming lint, never-throw handlers, `call_external` seam), and workflows W01–W12 (drive real product code against a real Postgres, not mocks).

## Invariant/law note
The tenant-isolation law (invariant 9) has a **real** enforcing barrier (`read_meeting`), but its sealed P0 criterion **AC-TEN-002 does not exercise it** — so isolation is not proven by the bundle. Capability revocation (AC-INV-012) is entirely absent. These are the highest-risk gaps.

---

**`VERDICT: NOT DONE`**

Refutation list (all reproduced at source): **AC-TEN-002 (P0)**, **AC-BLD-003 (P1)**, **AC-CI-003 (P1)**, **AC-CFG-007 (P1)**, **AC-REG-003 (P1)**, **AC-INV-012 (P1)**, **AC-HOST-007 (P1)** — seven blocking criteria pass green for the wrong reason (toothless tests over stubs, hardcoded constants, absent modules, or gamed-to-skip assertions). Additional PLAUSIBLE weak-teeth criteria: AC-CMP-013, AC-CI-002, AC-INV-007, AC-INV-011, AC-DOCK-004. A doc that is otherwise ~93% genuinely satisfied is still NOT DONE.
