# BUILD-LOOP.md — Proxy v2 build system (the generalizable spec→ship loop)

*Self-contained execution spec. A fresh Claude Code session should be able to read
this file and execute the whole pivot deterministically. Replaces the v1 orchestrator
harness. **Dynamic by design:** every mechanism is spec-driven and doc-agnostic — point
it at ANY doc (00–09 today, or anything future) and the same loop runs. Nothing is
hard-coded per document.*

---

## 0 · Why this exists (the pivot in one paragraph)

The v1 build system (9-phase orchestrator, sealed 91–309 criteria/doc, fresh-session-per-*pass*,
immutable-to-the-fixer seals) was slow AND never converged — one doc burned 23 fresh sessions
reproducing a block only a founder could clear. v2 keeps everything v1 got *right* (specs as
source of truth, real-data verification, maker≠checker, coverage-closure, never-edit-tests) and
deletes the ceremony that caused the deadlock. It is assembled from Anthropic's own published
practice: **loop engineering** (goal→context→tools→action→verify→state→repeat), spec→coverage-verify
(`/opsx:verify`), and the long-running-agent harness (feature-list + progress file + one feature/session).

## 1 · Mental model

**Humans design intent; agents execute, verify, and remember.** Every doc/feature runs the SAME
pipeline; pointing it at a new spec is a config value, not a rebuild:

```
SPEC (source of truth)  →  DECOMPOSE → tasks.json  →  BUILD per task (TDD, real-data)
        →  VERIFY-IN-LOOP (self-repair until green)  →  INTEGRATE (assembled-tree gate)  →  DONE / BLOCKED
```

- **Unit of work** = a thin vertical slice / task, never a "finished component."
- **Definition of done** = a *computable boolean* (`done-check.sh`), never a human judgment call.
- **The loop runs until DONE is true or it flags specific BLOCKED items** — it never silently claims done,
  and it never deadlocks (blocked items flag-and-continue; they don't halt the pipeline).

## 2 · The generalizable pipeline (doc-agnostic)

`drive.sh <id>` runs the full loop for any spec `product/v0-spec/<id>*.md`:

| Stage | What runs | Artifact | Doc-agnostic mechanism |
|---|---|---|---|
| **Decompose** | plan-mode read of the spec → atomic, dependency-ordered tasks, each linked to a tagged spec requirement `R-<ID>-###` and carrying its own execution-based acceptance check | `slices/<id>/tasks.json` | `scripts/decompose.py` + a `decompose` skill; reads ANY spec |
| **Coverage gate** | fresh-context auditor asserts every `R-<ID>-###` maps to ≥1 task and every task → a requirement (no orphans) | pass/fail | `scripts/coverage_gate.py` (salvaged from v1 `criteria_coverage_gate.py`) |
| **Build** | one task/session, TDD: write the failing acceptance test → code to green → NEVER edit tests | commits + `passes:true` | `build-slice` skill; the `Stop` hook is the gate |
| **Verify-in-loop** | per-turn lint/type; per-task real-green; per-slice real-data eval + anchor-first review | evidence | hooks + DeepEval + `reviewer` subagent |
| **Integrate** | merge worktrees → assembled-tree run: Doc 09 §2 contract checks + the slice's run-through + 1 live e2e | pass/fail | `scripts/journey.py` runs Doc 09 run-throughs |
| **Done** | `done-check.sh --spec <id>` computes the boolean | DONE / BLOCKED list | uniform for every doc |

**Dynamic principle:** the loop reads requirements, acceptance checks, and run-throughs FROM the spec
(and from Doc 09 for integration). No doc names are hard-coded in the machinery — a doc10 dropped in
tomorrow runs identically.

## 3 · The DONE predicate (`done-check.sh`)

`DONE(<id>)` exits 0 **iff** all hold (each conjunct is machine-checkable):

1. **Coverage closed** — every `R-<id>-###` requirement → ≥1 task/test (no orphans).
2. **All tasks pass** — every `slices/<id>/tasks.json` entry `passes:true`, each backed by real-data evidence.
3. **Real-data eval green** — DeepEval suite ≥ `slices/<id>/_baseline.json` (relative regression, not absolute floors).
4. **Integration green** — Doc 09 §2 contract checks pass on the assembled tree + the slice's run-through + 1 live e2e.
5. **Reviewer zero-gaps** — anchor-first fresh-context reviewer finds 0 unmet requirements.
6. **Mechanical clean** — `ruff` + `mypy --strict` + `pytest` exit 0; `pip-audit` clean.

Anti-gaming (v2's hard rule): the agent may **not** write `_baseline.json` or flip `passes` bits it hasn't
earned — both are guard-protected; baseline changes are a human-gated action; a slice of held-out,
randomized eval inputs is never shown to the build agent; `done-check` mutation-spot-checks N random requirements.

## 4 · KEEP / DELETE / CREATE manifest (grounded in the real tree)

### KEEP — progress, never delete
- `product/` — the specs (source of truth) + `CANONICAL-DECISIONS.md` (overrides) + `SPINE-REGISTER.md`.
- `services/`, `libs/` — built doc00–03 code (frozen; verify, don't rebuild).
- `tests/` + `tests/cassettes/` — existing tests + recorded vendor traffic.
- `acceptance/` — the distilled requirements/criteria bundles → **reference input for `decompose`** + coverage cross-check (NOT the gate). Real work; do not throw away.
- `evidence/`, `fixtures/`, `goldens/`, `migrations/`, `infra/`, `deploy/`, `config/`, `apps/`, `staging/` (doc04/05/08/09 partial), `pyproject.toml`, `uv.lock`, `alembic.ini`, `conftest.py`, `docs/`, `tools/`, `scripts/`.
- `AGENTS.md` — the 5 laws (keep; `CLAUDE.md` `@import`s it).

### ARCHIVE (move to `archive/v1/`, don't hard-delete) — history/record
- `PROGRESS.md`, `REDESIGN-REPORT.md`, `HANDOFF.md`, `SETUP.md`, orchestrator `*.md` reports.
- Salvage first: `orchestrator/criteria_coverage_gate.py` + `orchestrator/extraction_count_gate.py` → `scripts/` (reuse the coverage-closure logic).

### DELETE — v1 process machinery (we don't need it)
- `orchestrator/` (orchestrate/supervise/launch/watchdog, `ladder_*`, `model_routing*`, `prompts/`, `skills/`, `state/`, logs, `test_*_hardening.py`) — *after salvaging the two gate files above*.
- `runner.py` (40-pass driver), `eval_runner.py` (stub).
- `harness/verify.sh`, `harness/stop_verify.py`, `harness/HARNESS.md`, `harness/prompts/` — replaced by v2 hooks + `done-check.sh`.
- Stray `__pycache__/`, the odd `—` file.
- **FLAG before deleting** (verify no live imports first): `src/` (`src/proxy/*` marked legacy in AGENTS.md — but the v1 guard whitelists `src/proxy/llm.py` as the gateway; confirm the real gateway is `libs/llm` before removing), `spike/`.

### REPLACE — the v1→v2 swaps (protected paths; need the guard stood down)
- `harness/guard.py` → a **lean guard** (protect `tests/`, `_baseline.json`, `tasks.json`-status only) + invariant hooks.
- `harness/post_edit_test.py` → **`pytest-testmon`** affected-only per-edit signal.
- `CLAUDE.md` → lean, dynamic constitution (see §5).
- `.claude/settings.json` → v2 hooks (see §6).

### CREATE — v2 (protected + unprotected)
- **Unprotected (buildable now):** this file, `done-check.sh`, `drive.sh`, `scripts/{decompose.py,coverage_gate.py,cost_log.py,journey.py}`, `slices/<id>/{tasks.json,progress.md,_baseline.json}`, `regressions/`, DeepEval config + `tests/eval/`.
- **Protected (need guard down):** `CLAUDE.md`, `.claude/settings.json`, `.claude/agents/{coverage-auditor,reviewer,eval-runner}.md`, `.claude/skills/{decompose,build-slice,eval-gate}/SKILL.md`, `.claude/hooks/{pretool_guard.py,stop_gate.sh,post_edit_testmon.py,invariants.py}`.

## 5 · CLAUDE.md design (lean, dynamic, carries the company vision)

Short (bloat = ignored rules), but it captures the vision + the generalizable loop so any doc runs correctly:
- **What Proxy is** (vision, 3–4 lines): an AI participant that joins a company's meetings already knowing their codebase; product + agent are both "Proxy"; user-visible strings never carry internal names.
- **The 5 laws** (`@import AGENTS.md`) + the invariants that are hook-enforced.
- **The loop rules** (doc-agnostic): work ONE task from `slices/<id>/tasks.json` at a time; TDD; a task is done only after it RAN on real/held-out data and the output was shown as evidence; flip `passes:true` only then; never edit tests or the baseline.
- **Commands:** `uv sync` · `uv run --package <x> pytest` · `./done-check.sh --spec <id>` · `./drive.sh <id>` · `alembic upgrade head`.
- **Pointers, not contents:** specs live in `product/v0-spec/` (source of truth); `CANONICAL-DECISIONS.md` overrides; integration journeys live in `Doc 09`. The loop reads these dynamically — CLAUDE.md does not restate them.
- **Context hygiene:** one task/session; `/clear` between tasks; subagents for investigation + review; stable prefix (CLAUDE.md/specs) for prompt-cache, volatile state (tasks.json/diffs) last.

## 6 · Hooks & enforcement (v2 — deterministic, the anti-gaming wall)

`.claude/settings.json`:
- **`PreToolUse` (lean guard, `.claude/hooks/pretool_guard.py`):** block edits to `tests/`, `_baseline.json`, `tasks.json`-`passes` fields; enforce invariants (no raw vendor client outside `libs/http` `call_external`; no secret literals; user-visible strings carry no internal names). Much smaller than v1's self-sealing guard — no integrity-hash ceremony.
- **`PostToolUse` (`Edit|Write`, testmon):** run only affected tests + `ruff`/`mypy` on the edited file; inject failures back per-turn.
- **`Stop` (`.claude/hooks/stop_gate.sh`):** block turn-end until the current task's real check passes (`done-check.sh --task`); auto-overrides after N *identical* failures → escalate to `BLOCKED`, not blind retry.
- **`SubagentStop`:** a build subagent can't fold its result back until tests pass + no secret + no out-of-scope write.

## 7 · Verification (execution-based, real-data, drift-guarded)

- **Every acceptance check RUNS the real path** (real transcript → real scribe → real notes → real persistence) and the agent **shows the output as evidence** — assertion-only checks are banned as acceptance.
- **Inner loop:** `vcrpy` cassettes (deterministic, offline). **Outer:** ≥1 gated live e2e that exercises real tool execution (the `DOC03_LIVE_E2E` pattern), run nightly.
- **DeepEval** (pytest-native) for behavioral/LLM outputs; **held-out** golden keys; LLM-judge pinned (model+version), calibrated κ≥0.6 cross-family, judged ≥3× gated on median; `done` fails if judge-vs-golden κ on a frozen slice drops.
- **Cassette hygiene:** stamp with model-id + record-date; CI warns past TTL.
- **Metamorphic checks** (the accuracy ceiling-raiser for Proxy): reorder/paraphrase a transcript window → decisions/action-items stay logically equivalent; inject a contradiction → the contradiction-resolution invariant fires.
- **Regression ratchet:** every BLOCKED-then-fixed item appends a permanent case to `regressions/`; `done-check` runs it. This is what asymptotically reaches 100%.
- **Integration gate = Doc 09 §2** (already specified): `assert_registry_closed()`, contracts resolve to `libs/contracts`, one `operation_runs`, `AgentChunk` consumers use `stream_deltas`, cost+drafts persist across a simulated process kill.

## 8 · Optimizations folded in (validated, low-complexity)

- **No Haiku.** Single strong model (Opus) by default; Sonnet only for cheap mechanical steps. (Founder directive.)
- **Prompt-cache discipline:** stable prefix (CLAUDE.md/AGENTS.md/spec), volatile state (tasks.json/diffs) at the end.
- **`pytest-testmon`** affected-only inner loop (replaces the blunt `-k fast or smoke`).
- **Cost/telemetry:** per-pass tokens/cost/wall-time → JSONL (`scripts/cost_log.py`); hard per-run + per-DONE cost ceiling in `drive.sh`.
- **Parallelism:** git worktrees, 2–4 agents, split by file/dir (never vague feature); integration gate on the merged tree. (Multi-agent helps parallelizable work, hurts sequential — keep it to genuinely independent slices.)
- **SKIP (overkill solo):** ensemble/debate verify, synthetic-user sim, canary, paid observability, big specialist swarms.

## 9 · FIRST JOURNEY — verify doc00–03 (production-ready, from Doc 09 S1)

**The happy arc (walking skeleton):** a real engineering team connects their GitHub repo → Proxy clones +
indexes to `ready` with an honest coverage % → the team invites Proxy to a live meeting → a participant asks a
codebase question out loud ("what breaks if we change the orders table?") → Proxy answers within the latency
target with a spoken headline + chat detail, **citing `file:line` from the live clone** → the meeting ends →
Proxy posts a clean notes file (decisions, action items w/ owners, open questions) with receipts.

**Exercises** doc 00·01·02·03 (+ light 04/05/08). **This is the first real integration test of doc00–03 — the
cross-doc proof the v1 system never ran.**

### Doc00–03 verification — run in PARALLEL (git worktrees, one track each)
- **Track 00 (Foundation):** boot + `operation_runs` heartbeat/atomic-claim/reconcile; Postgres + GCS (object-versioned) round-trip; `assert_registry_closed()`; secret-binding check. (mechanical + integration)
- **Track 01 (Code-Intel):** on a REAL public repo — clone → tree-sitter map → `get_dependents`/`who_writes`/grep/read → `ready` + coverage% + honest lower-bound "what breaks." (real-data eval)
- **Track 02 (Voice):** cassette replay of Recall+AssemblyAI+Cartesia over the `call_external` seam + 1 live smoke; barge-in <200ms; meeting-end signal. (reality tier)
- **Track 03 (Notes):** REAL transcript → real Haiku scribe (per-turn tagging) → coalesced deltas → real Sonnet close → notes file with decisions/actions/questions + receipts; contradiction resolved; referents→code. (real-data eval, held-out goldens)
- **Then INTEGRATE:** merge tracks → run **Doc 09 §2 contract checks** + **S1 happy arc** end-to-end on the assembled tree + the live e2e.

**Outcome (one of two legible states):** *doc00–03 verified as a working system → advance to doc04/05* — or a
specific `BLOCKED:<reason>` list → fix only those, on the v2 loop. No rebuild of working code.

## 10 · Execution order (for the post-guard-stand-down session)

1. **Salvage + archive:** move `criteria_coverage_gate.py`/`extraction_count_gate.py` → `scripts/`; move history docs → `archive/v1/`.
2. **Delete v1** process machinery (§4 DELETE list), after confirming `src/`/`spike/` have no live imports.
3. **Write v2 protected files:** `CLAUDE.md`, `.claude/settings.json`, `.claude/hooks/*`, `.claude/agents/*`, `.claude/skills/*`.
4. **Write v2 unprotected engine:** `done-check.sh`, `drive.sh`, `scripts/*`, `slices/` skeleton, `regressions/`, DeepEval config + `tests/eval/`; add `deepeval` + `pytest-testmon` + `pip-audit` to `pyproject.toml`; `uv sync`.
5. **Smoke the loop** on a trivial task to prove the Stop hook self-repairs and `done-check` computes.
6. **Run the first journey (§9):** four parallel doc00–03 verification tracks → integrate (Doc 09 §2 + S1) → produce the DONE/BLOCKED verdict.
7. Then `drive.sh 04`, `drive.sh 05`, … — same loop, dynamically, for every remaining and future doc.

## 11 · Guard stand-down (the one human step)

The v1 guard is a `PreToolUse` hook loaded into the running session; it self-protects, so it must be
disabled by the owner. Replace `.claude/settings.json` with:

```json
{ "hooks": {} }
```

then **restart Claude Code** (hooks load at session start). A fresh session then executes §10 with full access.
(The v2 hooks in §6 are re-enabled by writing the new `.claude/settings.json` in step 3 + one more restart.)
