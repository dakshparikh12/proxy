red-key manifest is environment-conditional:
> §6: "`DATABASE_URL`, `GCS_BUCKET`, `SESSION_SECRET` **(prod)**, GCP project **(prod)**, each AES credential key, `RECALL_API_KEY`, `ANTHROPIC_*`."

AC-BOOT-001 lists the keys (with "(prod)" annotations in prose) but its oracle only tests one unconditional key: `given: "A boot with one required key (e.g. DATABASE_URL) unset"`. It never asserts the boundary — that in **dev** a missing `SESSION_SECRET` must *not* crash, while in **prod** it *must*. A build that unconditionally requires `SESSION_SECRET` (crashes dev) or never requires it (boots prod without a session secret — a security hole) passes. **Missing: the prod-conditional off-state for the `(prod)`-qualified keys.**

### GAP-8 (LOW — Category 1, omitted config surface). Per-role `MAX_TURNS_*` env vars (§7) have no criterion.

> §7: "... per-role `MAX_TURNS_*` · `RECALL_API_KEY` ..."

AC-CFG-002 pins the 8 model seats and AC-CFG-003 the in-flight semaphore, but the per-role max-turns surface (`config [orchestrator] wake_max_turns`, `[workroom] task_wall_clock_budget_min`, and the `MAX_TURNS_*` env family) is uncovered. Grep confirms zero `MAX_TURNS`/`max_turns` references in criteria/requirements. Minor (turn caps are a runtime-safety floor), and arguably thin at the foundation, but it is a named §7 config-contract element with no requirement.

### GAP-9 (LOW — Category 4, schema-pinning asymmetry). `webhook_events` canonical column set / `delivery_guid UNIQUE` / status domain is not schema-pinned like its sibling durable tables.

`operation_runs` (AC-SUB-001), `meeting_cost` (AC-SUB-025), `staged_drafts` (AC-SUB-027), and the identity tables (AC-SUB-030) each get an exact-column-set contract criterion. `webhook_events` (CANONICAL §12.10: `{id, provider, delivery_guid UNIQUE, sha, payload, status pending|processed|failed, received_at}`) gets only **behavioral** coverage (AC-SUB-022 dedupe, AC-SUB-023 200-then-drain, AC-SUB-024 no-bus). The `UNIQUE(delivery_guid)` is behaviorally implied by the dedupe test, but the `status` value domain (`pending|processed|failed`) and column set are unpinned — inconsistent with the treatment of every other §5.6 durable table, and the linchpin-status-domain lesson of AC-SUB-037 (which pinned `operation_runs.status` for exactly this reason) was not applied to `webhook_events.status`.

---

**Bottom line:** GAP-1 (cost-breaker uncomputable on the pinned schema, a re-run of the A-009 defect) and GAP-2 (`/internal/notes` cross-tenant exposure frontier) are seal-blockers in my judgment; GAP-3 is a live P1 contradiction the "0 unresolved contradictions" claim missed. GAP-4/5 are concrete omitted substrate behaviors with real crash-safety consequences. GAP-6/7/8/9 are lower-confidence but each names a spec behavior with no criterion. Recommend routing GAP-1 and GAP-3 to `ambiguities.yaml` with real resolutions (derived-obligation column split / never-retry carve-out), and adding criteria for GAP-2/4/5 before promotion.
