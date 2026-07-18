
| AC-M2-002…006 (Clone) | ✓ | | `tests/test_m2_clone.py` (all except -001) green | Passed. |
| AC-M3/M4/M5/M6/M7/M8, GV, SANDBOX, CANON, INV, LAT, E2E | ✓ | | `tests/test_*.py` + `tests/doc01/test_w_workflows.py` → `80 passed` + `12 passed` | Passed at test level in my run; not exhaustively impl-audited (moot — gate already fails). |
| **Integrity anchor (whole-doc)** | | **✗** | `acceptance/doc01/manifest.yaml:18-19` → `fixtures_hash: pending-golden-derivation`, `goldens_hash: pending-golden-derivation` | Goldens/fixtures are **not** anchored to the seal; "criteria/thresholds/goldens byte-identical to manifest hash" is **not** provable. Weakened-bar guarantee unmet. |
| **Isolation-triad hard rule / R-INVARIANT-09 (whole-doc)** | | **✗** | Only `open_as_tenant` fixture + `control_plane/authz.py` (a *DB meeting-read* scope, unrelated to the clone volume). No "runtime tripwire on cross-tenant read" for code_intel clones. | A cross-tenant file read on the clone volume is **not** actually prevented in product code — a law-violating path. A single violating path refutes the doc. |
| **spec_blocked D-INV-03 (whole-doc)** | | **✗** | `manifest.yaml:34,37-40` self-declares `spec_blocked_items: 1` (AGENTS.md invariant 3 superseded, "requires founder amendment") | DoD requires no law/invariant-violating path; the bundle ships with an unresolved invariant amendment. |

### Cross-checks
- **Sole arbiter:** `harness/verify.sh` **exits 1** (reproduced twice, deterministic) — AC-M2-001 fails without the orchestrator's deselect crutch.
- **Weakened bar:** two P0 blocking criteria (AC-M2-001, AC-M5-001) sit in `orchestrator/state/doc00.deselected.json`; green was achieved by removing blocking criteria from the run.
- **Builder honesty:** `evidence/doc01-EXCEPTIONS.md` records `VERIFICATION_REFUTED` (prior gate said NOT DONE on AC-M2-001) and the builder "proceeded" via a "clarified reading" adjudication + re-verify commits — none of which changed the arbiter's exit code on an independent run. `harness/guard.py` was repeatedly "stubbed"/"restored" (git log) — the protective guard was disabled during fix sessions.
- **Integrity:** goldens/fixtures hashes are literal `pending-golden-derivation`; not sealed.

| criterion_id | SATISFIED | REFUTED |
|---|:---:|:---:|
| AC-M2-001 (P0) | | REFUTED |
| Integrity anchor | | REFUTED |
| R-INVARIANT-09 (isolation) | | REFUTED |
| spec_blocked D-INV-03 | | REFUTED |

**VERDICT: NOT DONE** — refuted: **AC-M2-001** (P0; sealed `harness/verify.sh` exits 1 on independent run — `1 failed, 200 passed`; the clone volume isolation is enforced by a test-double path-string check, not product code; the P0 test is deselected to fake green). Additionally the doc violates the **tenant-isolation invariant (R-INVARIANT-09)** in product code, ships **unanchored goldens** (`fixtures_hash/goldens_hash: pending-golden-derivation`), and self-declares **1 spec_blocked invariant** (D-INV-03). Any one of these refutes; together they are decisive.
