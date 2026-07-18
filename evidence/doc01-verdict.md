0 | ✓ | | `tests/test_m4_substrate.py` green; impl `graph.py`, `graph_builder.py`, `graph_store.py`, `orm.py` | Real graph build/rebuild/GC. |
| AC-M5-001…015 | ✓ | | `tests/test_m5_tools.py` green; impl `mcp_server.py`, `results.py`; W07/W12 sims | Tools return computed file:line (validated real by INV-001). |
| AC-M6-001…004 | ✓ | | `tests/test_m6_readiness.py` green; impl `readiness.py`, `coverage.py`; W01 sim | Readiness→"ready", coverage completeness. |
| AC-M7-001…006 | ✓ | | `tests/test_m7_freshness.py` green; impl `webhook_handler.py`; W03/W10 sims | HMAC 401, dedup single-rebuild, uninstall hard-delete asserted. |
| AC-M8-001…004 | ✓ | | `tests/test_m8_lsp.py` green; impl `sandbox.py`, LSP path | Resolved→timeout fallback→restart. |
| AC-LAT-001,002 | ✓ | | covered by W11 (`test_w_workflows.py:335`) green | p50≤2s/p95≤4s + readiness≤900s. |
| AC-GV-001,002 | ✓ | | `tests/test_gv_graph_versions.py` green | Graph versioning. |
| AC-SANDBOX-001,002 | ✓ | | `tests/test_sandbox_boundary.py` green | LSP tools absent from sandbox manifest; no tool-name overlap. |
| AC-INV-001…007 (P0) | ✓ | | `tests/test_invariants.py` green; W08/W09 sims | Grounding (file:line exists+in-bounds), abstention not-found, lower-bound labeling, verifier rejects fabricated `resolved` (INV-004 real subprocess oracle), tenant isolation. |
| AC-CANON-001…005 | ✓ | | `tests/test_canonical_contracts.py` green | Contract/canonical decisions. |
| AC-E2E-001,002 | ✓ | | covered by W01/W08 green | Full happy-path + honest-abstention traces. |

**Cross-doc checks:** Seal integrity ✓ (digest byte-identical). Gates ruff/mypy-strict/bandit ✓. Every one of the 78 criteria maps to a real test (E2E/LAT covered by workflow sims). Honest-failure criteria emit correct shapes (INV-002 not-found, INV-003 lower-bound, INV-004 verifier non-zero). No law/invariant-violating path found in the passing set.

**Notes (not the cause of refutation, but flagged):** (1) The manifest itself declares `spec_blocked_items: 1` (D-INV-03 / AC-M2-001) and `fixtures_hash/goldens_hash: pending-golden-derivation` — goldens live under `fixtures/goldens` and `staging/`, **outside** the seal-hashed `acceptance/doc01` tree, so they are not integrity-anchored. (2) The AC-M2-001 tenant-`PermissionError` check is enforced by a test-double (`stubs.py:122 open_as_tenant`, path-string inspection), not product code.

---

The doc is knowingly SPEC_BLOCKED on a P0 blocking criterion. The builder's "done" rests on a claimed pass on a different privileged host ("code_intel estate"); that is the builder's word, which the gate does not accept as evidence. My own unmodified `verify.sh` run exits non-zero, and I cannot provision the required `/tenants` volume to reproduce a pass.

**VERDICT: NOT DONE** — refuted: **AC-M2-001** (P0; sealed harness `verify.sh` exits non-zero on independent run — 254 passed / 1 failed; required evidence `junit:T-M2-001` is RED; `/tenants` volume unprovisionable, no passing evidence obtainable).
