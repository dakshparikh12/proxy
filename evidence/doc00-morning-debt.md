# doc00 — morning debt (non-seal-blocking)

Adjudication of the 9 review gaps in `evidence/doc00-review-gaps-outstanding.md`, ruled by a
fresh-context adjudicator against the spec/criteria/requirements (no build-session framing).

Outcome: **1 FIX-NOW (GAP-2, applied), 1 INVALID (GAP-1), 7 MORNING-DEBT.** Only GAP-2 blocked
the seal. The bundle was re-sealed after applying GAP-2. Everything below is recorded, not blocking.

## Ruling summary

| GAP | Ruling | One-line reason |
|-----|--------|-----------------|
| GAP-1 | INVALID | `meeting_cost` already pins `transport_usd`+`e2b_usd` (AC-SUB-025) and AC-INV-003 sums the three meters; the breaker is computable and A-006 already reconciled its basis — not an A-009 re-run. |
| GAP-2 | **FIX-NOW (applied)** | `GET /internal/notes/{meeting_id}` is auth-wall-external + token-gated and had no criterion for its token gate or meeting→tenant scope; AC-TEN-002 only exercises the *authenticated* `/m/` surface. P0 cross-tenant exposure (invariant 9). |
| GAP-3 | MORNING-DEBT | No real contradiction: `status='failed'` and "drain `pending`" coexist. The pending→failed (never-retry) transition is merely *unspecified* — a minor ambiguity, not a falsified "0 contradictions" claim. |
| GAP-4 | MORNING-DEBT | Unnamed "substrate behavior"; every `R-DOC00-5-*` requirement already carries ≥1 criterion — sub-behavior oracle thinness, not a grep-confirmable uncovered P0 hole. |
| GAP-5 | MORNING-DEBT | Same as GAP-4 — §5 crash-safety (heartbeat, stale sweep, boot reaper, fencing) is densely covered; unnamed. |
| GAP-6 | MORNING-DEBT | Reviewer's own "lower-confidence"; unnamed behavior over densely-covered ground — a thinness note. |
| GAP-7 | MORNING-DEBT | AC-BOOT-001 lists `SESSION_SECRET(prod)`/`GCP(prod)` under `boots_with_missing_required_key:0`; only the dev-vs-prod conditional boundary is unexercised — oracle-completeness thinness on a covered P1. |
| GAP-8 | MORNING-DEBT | Zero `MAX_TURNS` refs confirmed, but per-role turn caps are numeric tunables (`R-DOC00-7-05` → `config/defaults.toml`); enforcement is downstream — thin foundation config-surface note. |
| GAP-9 | MORNING-DEBT | Real schema-pinning asymmetry (`webhook_events` gets no column-set/status-domain pin unlike AC-SUB-001/025/027/030/037), but its load-bearing behaviors (dedupe→UNIQUE, drain-on-`pending`) are behaviorally covered — consistency note, not a correctness hole. |

## Suggested follow-ups (do in the morning, not seal-blocking)

- **GAP-3:** record the `pending→failed` (never-retry / poison-webhook) transition as a minor ambiguity in `ambiguities.yaml` with a concrete carve-out (e.g. N-attempt cap → terminal `failed`).
- **GAP-7:** extend AC-BOOT-001's oracle with the dev-must-not-crash / prod-must-require conditional sub-assertions for the `(prod)`-qualified keys.
- **GAP-8:** add a `config/defaults.toml` presence check (or a `covered_downstream` disposition) for the per-role `MAX_TURNS_*` / `wake_max_turns` / `task_wall_clock_budget_min` surface.
- **GAP-9:** add a `webhook_events` column-set + `status ∈ {pending|processed|failed}` domain-pin criterion for parity with its §5.6 siblings.
- **GAP-4/5/6:** if the reviewer can name the specific uncovered behaviors, re-triage; as stated they are thinness over covered requirements.

## GAP-2 — what was applied (seal-blocking fix)

- `acceptance/doc00/doc00/criteria/criteria.yaml`: added **AC-TEN-004** (P0, `[security-adversarial]`,
  `authority_refs: [R-DOC00-4-01, R-INV-09]`) asserting (1) untokened `/internal/notes` is refused with
  no notes returned (`internal_notes_untokened_accept == 0`) and (2) the handler resolves
  `meeting_id → owning tenant` server-side and returns only that meeting's notes
  (`cross_tenant_notes_returned == 0`).
- `acceptance/doc00/doc00/faults/fault-model.yaml`: added `F-INTERNAL-NOTES-NO-TOKEN` and
  `F-INTERNAL-NOTES-CROSS-TENANT`, each `must_be_detected_by: [AC-TEN-004]`.
- `tests/doc00/test_m15_ten.py`: added `test_ten_004_internal_notes_token_gated_and_tenant_scoped`
  (collects clean, red-before-green via in-body product import).
