# Proxy v2 Build-Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the v2 spec→ship build-loop machinery (BUILD-LOOP.md §10 steps 1–5) and reach *readiness* to run the doc00–03 cross-doc verification journey (step 6), then `drive.sh 04/05/…` (step 7).

**Architecture:** Delete the v1 orchestrator/harness ceremony; salvage the two coverage/extraction gates; keep all built product code (`services/`, `libs/`), specs, acceptance bundles, tests, cassettes, goldens, evidence. Build a small, doc-agnostic engine — `drive.sh` (loop) + `done-check.sh` (computable DONE predicate) + `scripts/{decompose,coverage_gate,cost_log,journey}.py` — driven by lean `.claude/` hooks/agents/skills. Requirements and per-requirement acceptance checks are **already distilled** in `acceptance/doc<NN>/{requirements,criteria}.yaml`, so the engine reads those (not spec prose).

**Tech Stack:** Python 3.12 · uv-workspace monorepo (`services/*` + `libs/*`, one `uv.lock`) · pytest + pytest-asyncio + pytest-recording/vcrpy · ruff + mypy --strict + bandit · Postgres (auto-provisioned at `:55432` in tests) + GCS · DeepEval + pytest-testmon + pip-audit (added here) · Claude Code hooks (`.claude/settings.json`).

## Global Constraints

*(Copied verbatim from AGENTS.md / BUILD-LOOP.md / BUILD-PLAN-AMENDMENT-01. Every task implicitly includes these.)*

- **Python floor:** `>=3.12`. mypy `--strict`, `namespace_packages=true`. ruff line-length 120, rules E/F/I.
- **The 5 laws:** Grounded-or-silent (`file:line` or "not found by this method") · Never-overstate (`resolved` vs `lower-bound`; failures spoken plainly) · Human-control-absolute (barge-in <200ms; every world-touching action a staged draft behind a human click) · Dynamic-never-hard-coded (situation→action in model judgment; code owns physics/pipes/substrate) · Talk-and-glance.
- **Contract homes (never re-declare):** `libs/contracts` = all wire types + `assert_registry_closed()` + `AgentChunk` + `stream_deltas` + Readiness enum + ProxyMessage registry. `libs/http` = the sole `call_external(op, service, unit_cost_usd)` seam (retry + cost telemetry; no raw vendor clients elsewhere). `libs/llm` = the metered model gateway (every model call through it; `libs/llm/routing.py` = the canonical seat table). `libs/db` · `libs/ops` (with_operation_run, atomic-claim, TTL reconcile; `ops.lint.naming`; `ops.check_*`).
- **Product invariants (a violation is a build failure):** cited-or-abstain · lossless-or-honest · tenant-held-code-hard-deleted (per-tenant volume/process/index; ≤15 min delete) · truth-is-live · staged-drafts · freshness-gated caching (never cache verify/operate) · accelerate-never-gate (uncovered → labeled fallback, never hard fail) · tenant_id in every schema (cross-tenant read = P0) · self-host credentials (tokens per-operation, never cached/logged) · vertical/size-agnostic (zero industry code).
- **Naming:** user-visible strings never contain internal names (Orchestrator/Scribe/workroom). Product + agent are both "Proxy". Enforced by `ops.lint.naming`.
- **Builder-read-only (anti-gaming):** the build agent may NOT edit `tests/`, `tests/cassettes/`, `acceptance/`, `fixtures/`, `goldens/`, `slices/<id>/_baseline.json`, or flip `passes` bits in `slices/<id>/tasks.json` it hasn't earned. `_baseline.json` changes + `EXTRACTION_COUNT_HALT` are **human-gated** (founder review; never auto-approve).
- **`SPEC_BLOCKED` / `BLOCKED` is a first-class outcome:** a derived obligation the spec omits, or a task that fails N identical times, flags-and-continues (records the reason) — it never silently claims done and never deadlocks the pipeline.
- **Verification is execution-based:** every acceptance check RUNS the real path and shows output as evidence; assertion-only checks are banned as acceptance. Inner loop = vcrpy cassettes (offline, `record_mode="none"`); outer = ≥1 gated live e2e (the `DOC03_LIVE_E2E` pattern). LLM-judge calibration = weighted **κ≥0.6**, deterministic/DAG graders preferred. Estate matrix = 3–4 pinned real repos.
- **Model seats — TWO SEPARATE LAYERS, do not conflate:**
  - *Build-loop machinery* (this plan's own agents): Opus by default, Sonnet for cheap mechanical steps, **No Haiku** (founder directive, BUILD-LOOP §8).
  - *Product runtime* (`libs/llm/routing.py`, doc00–03 code — KEEP, do not touch here): Scribe/gates=`claude-haiku-4-5`, answer/orchestrator/workroom=`claude-sonnet-4-6`, big-build=`claude-opus-4-8`. **OPEN QUESTION for the verification journey:** whether the founder "No Haiku" directive now also retires Haiku from the product routing table. Flag; do NOT change routing in this plan.

---

## REVISION 01 — binding corrections (post plan-review; SUPERSEDE the task bodies below where they conflict)

A skeptical review (2026-07-22) verified the plan against the real tree and found several task bodies assume interfaces that don't exist here. These corrections are binding; each is woven into the relevant subagent dispatch.

- **[A1/A2] Invariant enforcement is NOT a per-file PreToolUse hook.** There is no `ops.lint.naming` CLI — the real module is `libs.lint.naming` (a function `check_user_visible_strings(mapping)->LintResult`, no `__main__`, importable only after `conftest.py::_wire_libs_lint()` extends the `libs` namespace `__path__` under pytest). `ops.check_secret_bindings.main()` ignores argv and is an inert no-op; `ops.check_sdk_isolation_triad.main()` ignores argv and AST-scans the whole tree. All the real checks are `pass_filenames:false` in `.pre-commit-config.yaml`. **Therefore:** delete Task 3.3's `invariants.py` per-file hook. Instead, run the invariant wall **repo-scoped at Stop-time** by having `done-check.sh --task` (and the `--spec` path) invoke `pre-commit run --all-files` (or the specific `ops.check_*` mains + `python -m pytest tests/doc00/test_m12_con.py` for naming). The PreToolUse layer keeps ONLY `pretool_guard.py` (builder-read-only). Update Task 3.8 settings.json to wire a single PreToolUse hook.
- **[A3] `decompose.py` acceptance cmd must select real tests.** `pytest -k AC_CMP_001` matches zero tests (real node ids are `test_cmp_001_…`). `parse_criteria` must ALSO extract each criterion's `test_ids:` list (e.g. `T-CMP-001`); the acceptance cmd is built from those: `T-CMP-001` → `-k cmp_001` (lowercase, strip the `T-`/`AC-` prefix, join with `or`). If a criterion has no `test_ids`, mark the task `passes:false` with `acceptance.note:"no test_ids in bundle — needs mapping"` (a legible BLOCKED, not a zero-match green).
- **[A4] Offline conjuncts exclude `integration`.** Every `done-check.sh` pytest invocation over the offline tier uses `-m "not reality and not e2e and not negative and not integration"` (integration needs live Postgres/GCS). State the DB precondition where the integration/journey conjunct runs.
- **[B3] `deepeval` goes in a separate opt-in dependency group** (like the existing `reality` group), NOT `dev` — it pulls a heavy transitive set that can perturb the shared lock. `pytest-testmon` + `pip-audit` may stay in `dev`. `uv sync --all-packages` remains the sync (never bare `uv sync`).
- **[B5] Already-built docs are VERIFIED, not rebuilt (BUILD-LOOP §9).** doc00 has 157 criteria; do NOT emit 157 build slices. For docs whose code already exists (00–03), `decompose.py` maps each criterion to its existing `test_ids` and the task's acceptance = "those tests are green" (verification tasks), not "write new code". Phase 6 runs the existing suite per doc as the readiness evidence. Only genuinely-unbuilt requirements become build tasks.
- **[C1] `done-check.sh --spec` must run the real-data eval + a mutation spot-check, or it's gameable.** Conjunct 3 (DeepEval ≥ `slices/<id>/_baseline.json`) and the "mutation-spot-check N random requirements" (flip a requirement's expected value, assert the check goes red) are REQUIRED for the predicate to be trustworthy — omitting them is exactly the anti-gaming hole the pivot exists to close. Until `_baseline.json` exists (Task 6.2), conjunct 3 prints `eval: NO BASELINE (blocked)` and `--spec` returns nonzero — it must NOT silently pass.
- **[C2] Add the `SubagentStop` hook** (BUILD-LOOP §6): a build subagent can't fold back until its task's tests pass + no secret + no out-of-scope write. New Task 3.5b `.claude/hooks/subagent_stop.sh`; wire in Task 3.8.
- **[C3] Wire `regressions/` into `done-check.sh`.** Every BLOCKED-then-fixed item appends a permanent case under `regressions/`; `done-check --spec` runs `pytest regressions/ -q` as part of the predicate (the ratchet, §7). Empty `regressions/` passes vacuously; that's fine.
- **[C5] `drive.sh` enforces the cost ceiling** it claims: read a per-run token/USD ceiling (env `V2_COST_CEILING_USD`, default e.g. 25), append each phase's spend via `scripts/cost_log.py`, and abort with `BLOCKED:cost-ceiling` when exceeded. If cost telemetry isn't available yet, log `cost: untracked` honestly rather than claiming enforcement.
- **[B1] Done in Phase 2:** stale `src/proxy` pyproject refs removed; the one v1-ladder test coupled to deleted machinery archived. Offline baseline is now **747 passed / 2 known-fail**.

## File Structure

**Salvage (git mv, keep logic):**
- `scripts/coverage_gate.py` ← `orchestrator/criteria_coverage_gate.py` (requirement↔criterion closure; pure stdlib; self-contained).
- `scripts/extraction_count_gate.py` ← `orchestrator/extraction_count_gate.py` (RTM-denominator founder gate; rewire the one `from orchestrate import` in `_real_spawn`).
- `scripts/prompts/extraction_count.md` ← `orchestrator/prompts/extraction_count.md` (the recount prompt the gate spawns).

**Create — engine (unprotected):**
- `scripts/decompose.py` — acceptance bundle → `slices/<id>/tasks.json`.
- `scripts/task_coverage.py` — asserts every bundle criterion → ≥1 task (criterion↔task closure; complements coverage_gate's requirement↔criterion closure).
- `scripts/cost_log.py` — append JSONL {ts, phase, tokens, cost_usd, wall_s} to `evidence/cost/<id>.jsonl`.
- `scripts/journey.py` — Doc 09 §2 contract checks + a named run-through (S1…) on the assembled tree.
- `scripts/lib_spec.py` — shared id→paths resolver (`00` → spec `product/v0-spec/00-*.md`, bundle `acceptance/doc00/`, slice `slices/00/`).
- `done-check.sh` — the DONE predicate (6 conjuncts), `--spec <id>` and `--task <id> <task_id>`.
- `drive.sh` — the loop for one spec id.
- `slices/<id>/{tasks.json,progress.md,_baseline.json}` (skeleton, per id) · `regressions/` · `tests/eval/` + DeepEval config.

**Create — protected (`.claude/`, need guard down — it IS down):**
- `.claude/hooks/{pretool_guard.py,invariants.py,post_edit_testmon.py,stop_gate.sh}`.
- `.claude/agents/{coverage-auditor.md,reviewer.md}` (keep `eval-runner.md`; leave `verifier.md`/`planner-reviewer.md` as reference).
- `.claude/skills/{decompose,build-slice}/SKILL.md` (keep `eval-gate/`; leave `proxy-component-build/`, `spec-compliance-review/` as reference).
- `.claude/settings.json` — wire v2 hooks (LAST; needs a Claude Code restart to load).

**Modify:**
- `CLAUDE.md` — add v2 loop rules + commands (it's already ~90% the §5 constitution).
- `pyproject.toml` — add `deepeval`, `pytest-testmon`, `pip-audit`; register `integration` marker.

**Delete (git rm — recoverable on this branch):**
- `orchestrator/` (after salvage), `runner.py`, `eval_runner.py`, `src/`, the stray `—` file, `__pycache__/`.
- `harness/{verify.sh,stop_verify.py,post_edit_test.py,HARNESS.md,prompts/,scripts/}` (already de-wired). `harness/guard.py` → deleted (replaced by `.claude/hooks/pretool_guard.py`).

**Archive (git mv → `archive/v1/`):**
- `PROGRESS.md`, `REDESIGN-REPORT.md`, `HANDOFF.md`, `SETUP.md`, `docs/BUILD-PLAN-AMENDMENT-01.md`, any `docs/BUILD-SYSTEM.md`/`RUNBOOK.md`, orchestrator `*.md` reports + logs.

**Keep untouched (verify, don't rebuild):**
- `services/`, `libs/`, `tests/` (+ `tests/cassettes/`), `acceptance/`, `criteria/`, `product/`, `fixtures/`, `goldens/`, `evidence/`, `migrations/`, `infra/`, `deploy/`, `config/`, `apps/`, `staging/`, `conftest.py`, `pyproject.toml`, `uv.lock`, `alembic.ini`, `AGENTS.md`, **`spike/`** (live test fixture — `tests/doc00/test_m14_bld.py`, AC-BLD-001..003).

---

## Phase 0 — Baseline (commit the guard stand-down)

### Task 0.1: Commit the v1-guard-down baseline

**Files:**
- Modify (already changed in working tree): `.claude/settings.json` (= `{"hooks":{}}`), `harness/guard.py` (de-fanged PROTECTED).

- [ ] **Step 1: Confirm the workspace is healthy before touching anything**

Run: `uv sync && uv run pytest -q -m "not reality and not e2e and not negative" -p no:cacheprovider --co -q | tail -5`
Expected: uv sync succeeds; pytest COLLECTS the unit suite with no import errors.

- [ ] **Step 2: Commit the stand-down as the baseline**

```bash
git add .claude/settings.json harness/guard.py
git commit -m "v2: stand down v1 guard (settings.json hooks:{} ) — baseline for build-loop pivot

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

Expected: clean commit; `git status` shows only untracked plan doc.

---

## Phase 1 — Salvage & Archive (BUILD-LOOP §10.1)

### Task 1.1: Salvage the coverage gate

**Files:**
- Move: `orchestrator/criteria_coverage_gate.py` → `scripts/coverage_gate.py`
- Test: `tests/scripts/test_coverage_gate.py`

**Interfaces:**
- Produces: `python3 scripts/coverage_gate.py <doc> [--base <path>]` → exit 0 (all requirements covered, no dangling/authorityless criteria) / exit 1 (gaps), human-readable report on stdout. Public fns: `parse_requirements(path)->dict[str,str]`, `parse_criteria(path)->list[dict]`.

- [ ] **Step 1: Move the file (preserve history)**

```bash
git mv orchestrator/criteria_coverage_gate.py scripts/coverage_gate.py
```

- [ ] **Step 2: Confirm it has no orchestrate dependency**

Run: `grep -n "from orchestrate\|import orchestrate\|orchestrator" scripts/coverage_gate.py`
Expected: NO output (it is pure `re`/`sys`/`pathlib`/`collections`). If any hit appears, STOP and report.

- [ ] **Step 3: Write the failing test (real bundle, real closure)**

```python
# tests/scripts/test_coverage_gate.py
import subprocess, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]

def test_coverage_gate_runs_on_doc00_bundle():
    r = subprocess.run([sys.executable, str(ROOT/"scripts/coverage_gate.py"), "doc00"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode in (0, 1), r.stderr
    assert "requirement" in (r.stdout + r.stderr).lower()

def test_coverage_gate_parses_real_requirements():
    sys.path.insert(0, str(ROOT/"scripts"))
    import coverage_gate
    reqs = coverage_gate.parse_requirements(ROOT/"acceptance/doc00/requirements/requirements.yaml")
    assert len(reqs) > 0
    assert all(k.startswith("R-DOC00") for k in reqs)
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/scripts/test_coverage_gate.py -q`
Expected: PASS (the salvaged gate runs on the real doc00 bundle).

- [ ] **Step 5: Commit**

```bash
git add scripts/coverage_gate.py tests/scripts/test_coverage_gate.py
git commit -m "v2: salvage coverage_gate (requirement<->criterion closure) from orchestrator"
```

### Task 1.2: Salvage the extraction-count gate (rewire the orchestrate dependency)

**Files:**
- Move: `orchestrator/extraction_count_gate.py` → `scripts/extraction_count_gate.py`
- Move: `orchestrator/prompts/extraction_count.md` → `scripts/prompts/extraction_count.md`
- Test: `tests/scripts/test_extraction_count_gate.py`

**Interfaces:**
- Produces: `run_extraction_gate(doc, *, spawn=None, threshold=0.10, base=None) -> dict` (keys: `halt:bool`, `verdict:str`, `bundle_requirement_count`, `independent_count`, `rel_diff`). `spawn(doc)->(int|None, str)` is injectable. CLI: `python3 scripts/extraction_count_gate.py <doc> [--threshold F]` → exit 0 (AGREE) / exit 1 (HALT). Founder-gated: HALT is never auto-cleared.

- [ ] **Step 1: Move both files**

```bash
mkdir -p scripts/prompts
git mv orchestrator/extraction_count_gate.py scripts/extraction_count_gate.py
git mv orchestrator/prompts/extraction_count.md scripts/prompts/extraction_count.md
```

- [ ] **Step 2: Rewire `_real_spawn` (drop `from orchestrate import DOCS, run_agent`)**

Replace the body of `_real_spawn(doc)` so it spawns the recount via the headless `claude` CLI instead of the deleted orchestrator. New body:

```python
def _real_spawn(doc: str) -> tuple[int | None, str]:
    """Fresh-context independent recount from the RAW spec only (no bundle access).
    Uses the headless claude CLI with the salvaged extraction_count prompt.
    Returns (independent_count, full_output). Opus per the No-Haiku build directive."""
    import subprocess, pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = _spec_path_for(doc)          # from scripts/lib_spec.py (Task 4.1 dep) — see Step 3
    prompt_tmpl = (root / "scripts/prompts/extraction_count.md").read_text()
    prompt = prompt_tmpl.replace("<DOC>", doc).replace("<SPEC>", str(spec))
    res = subprocess.run(
        ["claude", "-p", prompt, "--model", "opus", "--allowedTools", "Read,Grep,Glob"],
        capture_output=True, text=True, timeout=60 * 20, cwd=str(root))
    out = res.stdout + res.stderr
    return _parse_count(out), out
```

- [ ] **Step 3: Add the minimal spec resolver inline (avoid a hard dep on Task 4.1)**

Add near the top of `scripts/extraction_count_gate.py`:

```python
def _spec_path_for(doc: str) -> "pathlib.Path":
    """doc00 -> product/v0-spec/00-*.md (first match)."""
    import pathlib, glob
    root = pathlib.Path(__file__).resolve().parent.parent
    n = doc.replace("doc", "")
    hits = sorted(glob.glob(str(root / "product/v0-spec" / f"{n}-*.md")))
    if not hits:
        raise FileNotFoundError(f"no spec for {doc} under product/v0-spec/{n}-*.md")
    return pathlib.Path(hits[0])
```

- [ ] **Step 4: Fix the `parse_requirements` import (it came from the sibling gate)**

The original imported `parse_requirements` from `criteria_coverage_gate`. Update it to the salvaged name:

Run: `grep -n "criteria_coverage_gate\|from coverage_gate\|import coverage_gate" scripts/extraction_count_gate.py`
Then change any `from criteria_coverage_gate import parse_requirements` to `from coverage_gate import parse_requirements` (both live in `scripts/`, importable when run from repo root or via the test's `sys.path.insert`).

- [ ] **Step 5: Write the failing test (pure logic, injected spawn — no real agent)**

```python
# tests/scripts/test_extraction_count_gate.py
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
import extraction_count_gate as g

def test_agree_within_threshold():
    real = g.bundle_requirement_count("doc00")            # real sealed count
    res = g.run_extraction_gate("doc00", spawn=lambda d: (real, "EXTRACTION_COUNT: %d" % real))
    assert res["halt"] is False and res["verdict"] == "AGREE"

def test_material_disagreement_halts():
    real = g.bundle_requirement_count("doc00")
    res = g.run_extraction_gate("doc00", spawn=lambda d: (max(1, real*2), "EXTRACTION_COUNT: x"))
    assert res["halt"] is True and res["verdict"] == "MATERIAL_DISAGREEMENT"
```

- [ ] **Step 6: Run the test**

Run: `uv run pytest tests/scripts/test_extraction_count_gate.py -q`
Expected: PASS (AGREE + HALT branches proven with injected spawn; no real CLI call).

- [ ] **Step 7: Commit**

```bash
git add scripts/extraction_count_gate.py scripts/prompts/extraction_count.md tests/scripts/test_extraction_count_gate.py
git commit -m "v2: salvage extraction_count gate (RTM-denominator founder halt); rewire spawn off orchestrator"
```

### Task 1.3: Archive v1 history docs

**Files:**
- Move to `archive/v1/`: `PROGRESS.md`, `REDESIGN-REPORT.md`, `HANDOFF.md`, `SETUP.md`, `docs/BUILD-PLAN-AMENDMENT-01.md`, and (if present) `docs/BUILD-SYSTEM.md`, `RUNBOOK.md`.

- [ ] **Step 1: Create the archive and move history docs**

```bash
mkdir -p archive/v1
for f in PROGRESS.md REDESIGN-REPORT.md HANDOFF.md SETUP.md docs/BUILD-PLAN-AMENDMENT-01.md docs/BUILD-SYSTEM.md RUNBOOK.md; do
  [ -e "$f" ] && git mv "$f" "archive/v1/$(basename "$f")"
done
```

- [ ] **Step 2: Move orchestrator markdown reports + logs (keep the record, not the machinery)**

```bash
mkdir -p archive/v1/orchestrator-reports
for f in orchestrator/*.md orchestrator/*.log; do
  [ -e "$f" ] && git mv "$f" "archive/v1/orchestrator-reports/$(basename "$f")"
done
```

- [ ] **Step 3: Verify + commit**

Run: `git status --porcelain | grep -c '^R' && ls archive/v1`
Expected: renames staged; `archive/v1/` holds the history docs.

```bash
git add -A && git commit -m "v2: archive v1 history docs + orchestrator reports -> archive/v1/"
```

---

## Phase 2 — Delete v1 machinery (BUILD-LOOP §10.2)

### Task 2.1: Remove the v1 process machinery

**Files:**
- Delete: `orchestrator/` (post-salvage), `runner.py`, `eval_runner.py`, `src/`, stray `—`, `__pycache__/`.
- Delete: `harness/{verify.sh,stop_verify.py,post_edit_test.py,HARNESS.md,guard.py}`, `harness/prompts/`, `harness/scripts/`.
- KEEP: `spike/` (live test fixture — verified).

- [ ] **Step 1: Re-confirm nothing live imports what we're about to delete**

Run: `grep -rn "from orchestrator\|import orchestrator\|import runner\b\|import eval_runner\|from harness\|import harness" services libs tests conftest.py scripts | grep -v "services.harness"`
Expected: NO output. (`services.harness` is the product harness and is intentionally excluded — it stays.) If anything else appears, STOP and report.

- [ ] **Step 2: Delete the machinery**

```bash
git rm -r orchestrator runner.py eval_runner.py src \
  harness/verify.sh harness/stop_verify.py harness/post_edit_test.py \
  harness/HARNESS.md harness/guard.py harness/prompts harness/scripts
[ -e "—" ] && git rm -- "—"
find . -path ./.venv -prune -o -name __pycache__ -type d -print -exec rm -rf {} + 2>/dev/null || true
```

- [ ] **Step 3: Prove the product tree is intact (unit suite still green)**

Run: `uv sync && uv run pytest -q -m "not reality and not e2e and not negative" -p no:cacheprovider`
Expected: the doc00–03 unit baseline passes (per the commit history, ~352 passed). If anything regresses, the deletion touched something live — STOP, `git restore --staged`, report.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "v2: delete v1 process machinery (orchestrator/runner/eval_runner/src + v1 harness hooks); keep spike/"
```

---

## Phase 3 — v2 protected files (`.claude/` + CLAUDE.md) (BUILD-LOOP §10.3)

### Task 3.1: Update CLAUDE.md with the v2 loop rules + commands

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a "The build loop (doc-agnostic)" section and extend Commands**

Insert after the existing `## Commands` block:

```markdown
- `./drive.sh <id>` — run the full v2 loop for spec `<id>` (e.g. `00`, `03`).
- `./done-check.sh --spec <id>` — compute the DONE predicate (exit 0 = DONE).

## The build loop (doc-agnostic — every doc runs the SAME pipeline)
- Work **ONE** task from `slices/<id>/tasks.json` at a time; `/clear` between tasks.
- TDD: write the failing acceptance test → code to green → **never edit tests, cassettes, acceptance bundles, goldens, or `_baseline.json`**.
- A task is done ONLY after its real path RAN on real/held-out data and the output was shown as evidence; flip `passes:true` only then.
- `_baseline.json` changes and the `EXTRACTION_COUNT_HALT` are human-gated (founder review; never auto-approve).
- A task that fails N identical times flags `BLOCKED:<reason>` and continues — it never deadlocks the pipeline and never silently claims done.
- Pointers (read dynamically, not restated here): specs = `product/v0-spec/`; `CANONICAL-DECISIONS.md` overrides; requirements/criteria = `acceptance/doc<NN>/`; integration journeys = Doc 09 §2/§3.
```

- [ ] **Step 2: Verify the naming lint still passes on CLAUDE.md-referenced strings**

Run: `uv run python -m ops.lint.naming CLAUDE.md 2>/dev/null; echo "exit=$?"`
Expected: exit 0 (no internal names leaked). If the module signature differs, run the pre-commit hook instead: `pre-commit run --files CLAUDE.md`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md && git commit -m "v2: CLAUDE.md — add doc-agnostic build-loop rules + drive/done-check commands"
```

### Task 3.2: Lean PreToolUse guard

**Files:**
- Create: `.claude/hooks/pretool_guard.py`
- Test: `tests/hooks/test_pretool_guard.py`

**Interfaces:**
- Consumes: a Claude Code PreToolUse hook JSON payload on stdin (`{tool_name, tool_input:{file_path, ...}}`).
- Produces: exit 0 (allow) or a JSON `{"decision":"block","reason":...}` on stdout with exit 0 (Claude Code blocks on the decision field). Blocks writes to read-only trees and `passes`-bit tampering.

- [ ] **Step 1: Write the failing test**

```python
# tests/hooks/test_pretool_guard.py
import json, subprocess, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
HOOK = ROOT/".claude/hooks/pretool_guard.py"

def _run(payload):
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True)
    return r.stdout, r.returncode

def test_blocks_edit_to_tests():
    out, _ = _run({"tool_name":"Edit","tool_input":{"file_path":"tests/doc03/test_x.py"}})
    assert '"block"' in out

def test_blocks_cassette_edit():
    out, _ = _run({"tool_name":"Write","tool_input":{"file_path":"tests/cassettes/foo.yaml"}})
    assert '"block"' in out

def test_blocks_baseline_edit():
    out, _ = _run({"tool_name":"Edit","tool_input":{"file_path":"slices/03/_baseline.json"}})
    assert '"block"' in out

def test_allows_service_edit():
    out, rc = _run({"tool_name":"Edit","tool_input":{"file_path":"services/scribe/src/scribe/close.py"}})
    assert out.strip() == "" and rc == 0
```

- [ ] **Step 2: Run it (fails — hook missing)**

Run: `uv run pytest tests/hooks/test_pretool_guard.py -q`
Expected: FAIL (file not found).

- [ ] **Step 3: Implement the hook**

```python
#!/usr/bin/env python3
"""Lean v2 PreToolUse guard. Protects the builder-read-only surface; no integrity-hash ceremony."""
import sys, json, re

READONLY = ("tests/", "acceptance/", "fixtures/", "goldens/", "criteria/", "product/")
READONLY_FILE_RE = re.compile(r"(^|/)_baseline\.json$")

def block(reason):
    print(json.dumps({"decision": "block", "reason": reason})); sys.exit(0)

def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        sys.exit(0)                              # never throw; fail open on unparseable payloads
    if ev.get("tool_name") not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        sys.exit(0)
    path = (ev.get("tool_input") or {}).get("file_path", "") or ""
    norm = path.split("/Users/")[-1]             # tolerate absolute paths
    if any(seg in norm for seg in READONLY) or READONLY_FILE_RE.search(norm):
        block(f"{path} is builder-read-only (tests/acceptance/goldens/baseline). "
              f"Change the spec/bundle via founder review, not the build agent.")
    sys.exit(0)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test (passes)**

Run: `uv run pytest tests/hooks/test_pretool_guard.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add .claude/hooks/pretool_guard.py tests/hooks/test_pretool_guard.py
git commit -m "v2: lean PreToolUse guard (builder-read-only surface)"
```

### Task 3.3: Invariant hook (shell out to existing `ops.check_*`)

**Files:**
- Create: `.claude/hooks/invariants.py`
- Test: `tests/hooks/test_invariants.py`

**Interfaces:**
- Consumes: PreToolUse payload (same shape). Produces: block if the edited file introduces a raw vendor client (outside `libs/http`), a secret literal, or an internal name in a user-visible string — by invoking `ops.lint.naming` + the relevant `ops.check_*` on the target. Never throws.

- [ ] **Step 1: Confirm the real check entrypoints exist**

Run: `uv run python -m ops.lint.naming --help 2>&1 | head -3; uv run python -c "import ops.check_secret_bindings, ops.check_sdk_isolation_triad; print('ok')"`
Expected: modules import (`ok`). Note the exact CLI each expects (file arg vs. repo scan). If a module scans the whole repo (no per-file mode), the hook runs it repo-wide post-edit and blocks on nonzero exit.

- [ ] **Step 2: Write the failing test**

```python
# tests/hooks/test_invariants.py
import json, subprocess, sys, pathlib, tempfile, os
ROOT = pathlib.Path(__file__).resolve().parents[2]
HOOK = ROOT/".claude/hooks/invariants.py"

def _run(path):
    r = subprocess.run([sys.executable, str(HOOK)],
                       input=json.dumps({"tool_name":"Edit","tool_input":{"file_path":path}}),
                       capture_output=True, text=True, cwd=str(ROOT))
    return r.stdout, r.returncode

def test_clean_service_file_allowed():
    out, rc = _run("services/scribe/src/scribe/close.py")
    assert rc == 0                      # a clean, existing file passes
```

- [ ] **Step 3: Implement the hook**

```python
#!/usr/bin/env python3
"""v2 invariant hook: reuse the existing ops.check_* / ops.lint.naming enforcement.
Runs the relevant checks against the edited file; blocks on violation. Never throws."""
import sys, json, subprocess, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

def block(reason):
    print(json.dumps({"decision": "block", "reason": reason})); sys.exit(0)

def _check(mod, *args):
    r = subprocess.run([sys.executable, "-m", mod, *args],
                       capture_output=True, text=True, cwd=str(ROOT))
    return r.returncode, (r.stdout + r.stderr)

def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if ev.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        sys.exit(0)
    path = (ev.get("tool_input") or {}).get("file_path", "") or ""
    if not path.endswith(".py"):
        sys.exit(0)
    # Naming (user-visible strings carry no internal names) — per-file.
    rc, out = _check("ops.lint.naming", path)
    if rc != 0:
        block(f"naming invariant: {out.strip()[:400]}")
    # Secret bindings + SDK isolation triad — repo-scoped checks (run post-edit).
    for mod in ("ops.check_secret_bindings", "ops.check_sdk_isolation_triad"):
        rc, out = _check(mod)
        if rc != 0:
            block(f"{mod}: {out.strip()[:400]}")
    sys.exit(0)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run + adjust to the real CLIs**

Run: `uv run pytest tests/hooks/test_invariants.py -q`
Expected: PASS. If `ops.lint.naming <path>` doesn't accept a path arg (Step 1 revealed its real interface), adapt the `_check("ops.lint.naming", path)` call to the actual signature discovered in Step 1 — do not invent flags.

- [ ] **Step 5: Commit**

```bash
git add .claude/hooks/invariants.py tests/hooks/test_invariants.py
git commit -m "v2: invariant hook reuses ops.check_* (naming/secrets/sdk-isolation)"
```

### Task 3.4: PostToolUse testmon + lint hook

**Files:**
- Create: `.claude/hooks/post_edit_testmon.py`

**Interfaces:**
- Consumes: PostToolUse payload after `Edit|Write`. Produces: runs `ruff` + `mypy` on the edited file and `pytest --testmon` (affected-only); prints failures to stdout (non-blocking signal, injected back per-turn). Never throws.

- [ ] **Step 1: Implement (no test — it's a best-effort signal; guarded to never throw)**

```python
#!/usr/bin/env python3
"""PostToolUse: affected-only test + lint signal on the edited file. Non-blocking."""
import sys, json, subprocess, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]

def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    path = (ev.get("tool_input") or {}).get("file_path", "") or ""
    if not path.endswith(".py"):
        sys.exit(0)
    msgs = []
    for cmd in (["ruff", "check", path],
                ["mypy", "--strict", path]):
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        if r.returncode != 0:
            msgs.append((r.stdout + r.stderr).strip()[:800])
    # affected-only tests (testmon must be installed — Task 4.0)
    r = subprocess.run(["uv", "run", "pytest", "-q", "--testmon", "-p", "no:cacheprovider",
                        "-m", "not reality and not e2e and not negative"],
                       capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode not in (0, 5):        # 5 = no tests selected by testmon
        msgs.append((r.stdout + r.stderr).strip()[-1200:])
    if msgs:
        print("post-edit signal:\n" + "\n---\n".join(msgs))
    sys.exit(0)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke it**

Run: `echo '{"tool_input":{"file_path":"services/scribe/src/scribe/close.py"}}' | uv run python .claude/hooks/post_edit_testmon.py; echo "exit=$?"`
Expected: exit 0 (may print a signal; must not throw). testmon may warn until Task 4.0 installs it — acceptable pre-4.0.

- [ ] **Step 3: Commit**

```bash
git add .claude/hooks/post_edit_testmon.py
git commit -m "v2: PostToolUse testmon+lint signal (affected-only, non-blocking)"
```

### Task 3.5: Stop-gate hook

**Files:**
- Create: `.claude/hooks/stop_gate.sh`

**Interfaces:**
- Consumes: the Stop hook invocation. Produces: exit 0 (allow stop) only if there is no in-progress task, or the current task's `done-check.sh --task` passes. Escalates to `BLOCKED` after N identical failures rather than blind-retrying. Reads the "current task" from `slices/<id>/.current` (written by `drive.sh`).

- [ ] **Step 1: Implement**

```bash
#!/usr/bin/env bash
# v2 Stop gate: don't end the turn until the current task's real check passes.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}" || exit 0
CUR="$(cat slices/*/.current 2>/dev/null | head -1)"
[ -z "${CUR:-}" ] && exit 0                     # no active task -> allow stop
ID="${CUR%%:*}"; TASK="${CUR##*:}"
if ./done-check.sh --task "$ID" "$TASK" >/tmp/v2_taskcheck.out 2>&1; then
  exit 0
fi
# count identical failures to escalate instead of looping forever
SIG="$(tail -c 1500 /tmp/v2_taskcheck.out | shasum | cut -d' ' -f1)"
CNT_FILE="slices/$ID/.stall.$TASK"
PREV="$(cat "$CNT_FILE" 2>/dev/null)"
if [ "${PREV%%:*}" = "$SIG" ]; then N=$(( ${PREV##*:} + 1 )); else N=1; fi
echo "$SIG:$N" > "$CNT_FILE"
if [ "$N" -ge 3 ]; then
  echo "BLOCKED:$ID:$TASK stalled ${N}x (identical failure) — flagging, not retrying." >> slices/"$ID"/progress.md
  exit 0                                          # flag-and-continue; never deadlock
fi
# block the stop with the failing check as the reason
echo "{\"decision\":\"block\",\"reason\":\"task $TASK not green: $(tail -c 500 /tmp/v2_taskcheck.out)\"}"
exit 0
```

- [ ] **Step 2: Make executable + smoke**

```bash
chmod +x .claude/hooks/stop_gate.sh
CLAUDE_PROJECT_DIR="$(pwd)" .claude/hooks/stop_gate.sh; echo "exit=$?"
```
Expected: exit 0 (no active task → allow stop).

- [ ] **Step 3: Commit**

```bash
git add .claude/hooks/stop_gate.sh
git commit -m "v2: Stop gate (task-green-or-block, N-identical-failure escalation to BLOCKED)"
```

### Task 3.6: v2 agents (coverage-auditor + reviewer)

**Files:**
- Create: `.claude/agents/coverage-auditor.md`, `.claude/agents/reviewer.md`
- Keep: `.claude/agents/eval-runner.md` (unchanged).

- [ ] **Step 1: coverage-auditor.md** — a fresh-context agent that runs `scripts/coverage_gate.py <doc>` + `scripts/task_coverage.py <id>` and asserts no orphans either direction; returns a structured `{covered:bool, gaps:[...]}`. Frontmatter `name: coverage-auditor`, `tools: Read, Grep, Glob, Bash`.

- [ ] **Step 2: reviewer.md** — the anchor-first fresh-context reviewer (absorbs `verifier` + `spec-compliance-review`): given a per-task diff + the task's `criterion_ids`, checks each criterion is met for the RIGHT reason and no invariant is violated (the 11 product invariants + naming); returns `{unmet:[...], invariant_violations:[...]}`. Frontmatter `name: reviewer`, `tools: Read, Grep, Glob, Bash`.

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/coverage-auditor.md .claude/agents/reviewer.md
git commit -m "v2: coverage-auditor + anchor-first reviewer agents"
```

### Task 3.7: v2 skills (decompose + build-slice)

**Files:**
- Create: `.claude/skills/decompose/SKILL.md`, `.claude/skills/build-slice/SKILL.md`
- Keep: `.claude/skills/eval-gate/` (unchanged).

- [ ] **Step 1: decompose/SKILL.md** — reads `acceptance/doc<NN>/{requirements,criteria}.yaml`, runs the extraction-count gate (founder-halt on disagreement), emits `slices/<id>/tasks.json` with atomic, dependency-ordered tasks each carrying `criterion_ids` + an execution-based acceptance check. Frontmatter `name: decompose`, `description: Use to turn a sealed acceptance bundle into slices/<id>/tasks.json`.

- [ ] **Step 2: build-slice/SKILL.md** — the per-task TDD loop: pick ONE `tasks.json` entry, write the failing acceptance test that RUNS the real path, code to green, show evidence, flip `passes:true`; never edit tests/bundle/baseline; `BLOCKED:<reason>` on stall. Frontmatter `name: build-slice`.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/decompose/SKILL.md .claude/skills/build-slice/SKILL.md
git commit -m "v2: decompose + build-slice skills"
```

### Task 3.8: Wire v2 hooks in settings.json (LAST — needs restart)

**Files:**
- Modify: `.claude/settings.json`

- [ ] **Step 1: Write the v2 hook wiring**

```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "Edit|Write|MultiEdit|NotebookEdit",
       "hooks": [
         {"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/pretool_guard.py\""},
         {"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/invariants.py\""}
       ]}
    ],
    "PostToolUse": [
      {"matcher": "Edit|Write",
       "hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/post_edit_testmon.py\""}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/stop_gate.sh\""}]}
    ]
  }
}
```

- [ ] **Step 2: Validate JSON + commit (do NOT restart mid-plan; note it)**

Run: `python3 -c "import json;json.load(open('.claude/settings.json'));print('valid')"`
Expected: `valid`. 

```bash
git add .claude/settings.json
git commit -m "v2: wire hooks (pretool_guard+invariants / testmon / stop_gate). NOTE: hooks load on next Claude Code restart."
```

> **Checkpoint:** the v2 hooks are wired but not yet loaded (they load at session start). Keep building the engine (Phase 4) in this still-hookless session; restart is a deliberate step before the smoke test (Phase 5).

---

## Phase 4 — v2 engine (scripts + done-check + drive) (BUILD-LOOP §10.4)

### Task 4.0: Add v2 dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add deps to the `dev` group + register the `integration` marker**

Add `"deepeval>=1.0"`, `"pytest-testmon>=2.1"`, `"pip-audit>=2.7"` to `[dependency-groups].dev`; add `"integration: assembled-tree / db+gcs integration tests"` to `[tool.pytest.ini_options].markers`.

- [ ] **Step 2: Sync + verify**

Run: `uv sync && uv run python -c "import deepeval, testmon; print('ok')" && uv run pip-audit --version`
Expected: `ok` + a pip-audit version. If `deepeval` pulls a heavy transitive that breaks `uv sync`, pin a lighter compatible version and note it.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "v2: add deepeval + pytest-testmon + pip-audit; register integration marker"
```

### Task 4.1: `scripts/lib_spec.py` — id→paths resolver

**Files:**
- Create: `scripts/lib_spec.py`
- Test: `tests/scripts/test_lib_spec.py`

**Interfaces:**
- Produces: `spec_path(id)->Path`, `bundle_dir(id)->Path` (`acceptance/doc<NN>`), `slice_dir(id)->Path` (`slices/<id>`), `doc_name(id)->str` (`doc00`). `id` is the bare number string (`"00"`,`"03"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/scripts/test_lib_spec.py
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"scripts"))
import lib_spec as L

def test_resolves_doc03():
    assert L.doc_name("03") == "doc03"
    assert L.bundle_dir("03").name == "doc03"
    assert L.spec_path("03").name.startswith("03-")
    assert L.slice_dir("03") == ROOT/"slices/03"
```

- [ ] **Step 2: Implement**

```python
# scripts/lib_spec.py
import pathlib, glob
ROOT = pathlib.Path(__file__).resolve().parent.parent
def doc_name(id: str) -> str: return f"doc{id}"
def bundle_dir(id: str) -> pathlib.Path: return ROOT / "acceptance" / doc_name(id)
def slice_dir(id: str) -> pathlib.Path: return ROOT / "slices" / id
def spec_path(id: str) -> pathlib.Path:
    hits = sorted(glob.glob(str(ROOT / "product/v0-spec" / f"{id}-*.md")))
    if not hits: raise FileNotFoundError(f"no spec for id {id}")
    return pathlib.Path(hits[0])
```

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/scripts/test_lib_spec.py -q` → PASS.
```bash
git add scripts/lib_spec.py tests/scripts/test_lib_spec.py
git commit -m "v2: lib_spec id->paths resolver"
```

### Task 4.2: `scripts/decompose.py` — bundle → tasks.json

**Files:**
- Create: `scripts/decompose.py`
- Test: `tests/scripts/test_decompose.py`

**Interfaces:**
- Consumes: `lib_spec`, `coverage_gate.parse_criteria`. Produces: `python3 scripts/decompose.py <id>` → writes `slices/<id>/tasks.json` = `{"spec":<id>,"tasks":[{"task_id","title","criterion_ids":[...],"acceptance":{"cmd":...},"depends_on":[...],"passes":false}]}`. One task per criterion by default (mechanical, deterministic seed the build-slice loop refines); ordered by criticality (P0 first) then criterion_id.

- [ ] **Step 1: Write the failing test**

```python
# tests/scripts/test_decompose.py
import sys, pathlib, json, subprocess
ROOT = pathlib.Path(__file__).resolve().parents[2]

def test_decompose_doc00_emits_tasks(tmp_path, monkeypatch):
    r = subprocess.run([sys.executable, str(ROOT/"scripts/decompose.py"), "00"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    data = json.loads((ROOT/"slices/00/tasks.json").read_text())
    assert data["spec"] == "00" and len(data["tasks"]) > 0
    t = data["tasks"][0]
    assert t["criterion_ids"] and t["passes"] is False and "acceptance" in t
```

- [ ] **Step 2: Implement**

```python
# scripts/decompose.py
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import lib_spec as L
from coverage_gate import parse_criteria

CRIT_ORDER = {"P0": 0, "P1": 1, "P2": 2}

def build_tasks(id: str) -> dict:
    crit_path = L.bundle_dir(id) / "criteria" / "criteria.yaml"
    criteria = parse_criteria(crit_path)              # [{id, refs, criticality, blocking}]
    criteria.sort(key=lambda c: (CRIT_ORDER.get(c.get("criticality","P2"),3), c["id"]))
    tasks = []
    for c in criteria:
        tasks.append({
            "task_id": f"T-{c['id']}",
            "title": c["id"],
            "criterion_ids": [c["id"]],
            "requirement_ids": c.get("refs", []),
            "acceptance": {"cmd": f"pytest -q -k {c['id'].replace('-', '_')}"},
            "depends_on": [],
            "passes": False,
        })
    return {"spec": id, "tasks": tasks}

def main():
    id = sys.argv[1]
    out = L.slice_dir(id); out.mkdir(parents=True, exist_ok=True)
    (out / "tasks.json").write_text(json.dumps(build_tasks(id), indent=2))
    print(f"wrote {out/'tasks.json'} ({len(build_tasks(id)['tasks'])} tasks)")

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/scripts/test_decompose.py -q` → PASS.
```bash
git add scripts/decompose.py tests/scripts/test_decompose.py
git commit -m "v2: decompose acceptance bundle -> slices/<id>/tasks.json (criticality-ordered)"
```

### Task 4.3: `scripts/task_coverage.py` — criterion↔task closure

**Files:**
- Create: `scripts/task_coverage.py`
- Test: `tests/scripts/test_task_coverage.py`

**Interfaces:**
- Produces: `python3 scripts/task_coverage.py <id>` → exit 0 iff every criterion in the bundle appears in ≥1 task's `criterion_ids` and every task references a real criterion. Complements `coverage_gate.py` (requirement↔criterion) to give requirement→task transitively.

- [ ] **Step 1: Write the failing test**

```python
# tests/scripts/test_task_coverage.py
import sys, subprocess, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]

def test_task_coverage_closes_after_decompose():
    subprocess.run([sys.executable, str(ROOT/"scripts/decompose.py"), "00"], check=True, cwd=ROOT)
    r = subprocess.run([sys.executable, str(ROOT/"scripts/task_coverage.py"), "00"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 2: Implement** — load `slices/<id>/tasks.json` + bundle criteria; compute `set(criteria) - covered` and `dangling tasks`; print gaps; exit 1 if any.

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/scripts/test_task_coverage.py -q` → PASS.
```bash
git add scripts/task_coverage.py tests/scripts/test_task_coverage.py
git commit -m "v2: task_coverage (criterion<->task closure)"
```

### Task 4.4: `scripts/cost_log.py` — telemetry sink

**Files:**
- Create: `scripts/cost_log.py`
- Test: `tests/scripts/test_cost_log.py`

**Interfaces:**
- Produces: `append(id, phase, tokens, cost_usd, wall_s)` → one JSONL line to `evidence/cost/<id>.jsonl`. CLI: `python3 scripts/cost_log.py <id> <phase> <tokens> <cost_usd> <wall_s>`.

- [ ] **Step 1: Write failing test** (append twice → 2 lines, valid JSON, keys present, `tmp` redirected via env `COST_LOG_DIR`).
- [ ] **Step 2: Implement** (honor `COST_LOG_DIR` override for tests).
- [ ] **Step 3: Run + commit** — `git commit -m "v2: cost_log JSONL telemetry sink"`.

### Task 4.5: `scripts/journey.py` — integration gate (Doc 09 §2 + a scenario)

**Files:**
- Create: `scripts/journey.py`
- Test: `tests/scripts/test_journey.py`

**Interfaces:**
- Produces: `python3 scripts/journey.py contracts` → runs the Doc 09 §2 contract checks (registry closed; contracts resolve to `libs/contracts`; one `operation_runs`; `AgentChunk` consumers use `stream_deltas`; `meeting_cost`+`staged_drafts` persist) and exits 0/1. `python3 scripts/journey.py scenario S1` → runs the S1 happy-arc run-through (delegates to the existing e2e test id when present). Each check RUNS real code, not asserts.

- [ ] **Step 1: Write the failing test** — assert `journey.py contracts` runs and returns 0/1 with a per-check report; assert it invokes `assert_registry_closed()` (import it and prove it's called by checking output line `registry: closed`).

```python
# tests/scripts/test_journey.py
import sys, subprocess, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]

def test_contracts_gate_runs():
    r = subprocess.run([sys.executable, str(ROOT/"scripts/journey.py"), "contracts"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode in (0, 1)
    assert "registry" in (r.stdout + r.stderr).lower()
```

- [ ] **Step 2: Implement** — `contracts` subcommand:
  - import `libs.contracts` → call `assert_registry_closed()`; print `registry: closed` or the failure.
  - grep that no doc re-declares a shared wire type outside `libs/contracts` (reuse the existing `ops.check_field_contract` if it covers this; else a targeted grep).
  - one `operation_runs`: grep the repo for `CREATE TABLE operation_runs` and assert exactly one; assert zero `meeting_harness`.
  - `AgentChunk`/`stream_deltas`: grep consumers for raw `.type == "TEXT"` accumulation anti-pattern; assert `stream_deltas` used.
  - persistence: delegate to the existing kill-survival test id if one exists (search `tests/` for "process kill" / "recycle"); otherwise report `deferred: <reason>` (honest, not a silent pass).
  - `scenario S1`: look up the S1 e2e test (search `tests/` for the happy-arc / `DOC03_LIVE_E2E` marker) and run it under its gating env; report pass/skip.

- [ ] **Step 3: Run + commit**

Run: `uv run pytest tests/scripts/test_journey.py -q` → PASS.
```bash
git add scripts/journey.py tests/scripts/test_journey.py
git commit -m "v2: journey.py — Doc 09 §2 contract gate + scenario runner"
```

### Task 4.6: `done-check.sh` — the DONE predicate

**Files:**
- Create: `done-check.sh`
- Test: `tests/scripts/test_done_check.py`

**Interfaces:**
- Produces: `./done-check.sh --spec <id>` exits 0 iff all 6 conjuncts hold; prints a per-conjunct table + a `BLOCKED:` list on failure. `./done-check.sh --task <id> <task_id>` exits 0 iff that task's `acceptance.cmd` passes AND `passes:true` in tasks.json.

- [ ] **Step 1: Implement**

```bash
#!/usr/bin/env bash
# The v2 DONE predicate. Each conjunct is machine-checkable.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 2
MODE="${1:-}"; ID="${2:-}"

task_check() {                       # --task <id> <task_id>
  local id="$2" task="$3"
  local cmd; cmd="$(uv run python -c "import json,sys;d=json.load(open('slices/$id/tasks.json'));\
t=[x for x in d['tasks'] if x['task_id']=='$task'][0];print(t['acceptance']['cmd'])")" || return 2
  uv run $cmd -p no:cacheprovider >/dev/null 2>&1 || return 1
  uv run python -c "import json;d=json.load(open('slices/$id/tasks.json'));\
t=[x for x in d['tasks'] if x['task_id']=='$task'][0];exit(0 if t.get('passes') else 1)"
}

spec_check() {                       # --spec <id>
  local id="$1"; local fail=0
  echo "== DONE($id) =="
  python3 scripts/coverage_gate.py "doc$id" >/dev/null 2>&1 && echo "1 coverage(req<->crit): PASS" || { echo "1 coverage: FAIL"; fail=1; }
  python3 scripts/task_coverage.py "$id"    >/dev/null 2>&1 && echo "2 tasks(crit<->task): PASS" || { echo "2 tasks: FAIL"; fail=1; }
  uv run python -c "import json;d=json.load(open('slices/$id/tasks.json'));exit(0 if d['tasks'] and all(t.get('passes') for t in d['tasks']) else 1)" \
      && echo "3 all-tasks-pass: PASS" || { echo "3 all-tasks-pass: FAIL"; fail=1; }
  uv run pytest -q -m "not reality and not e2e and not negative" -p no:cacheprovider >/dev/null 2>&1 \
      && echo "4 unit+ruff clean: PASS" || { echo "4 unit: FAIL"; fail=1; }
  python3 scripts/journey.py contracts >/dev/null 2>&1 && echo "5 integration(Doc09§2): PASS" || { echo "5 integration: FAIL"; fail=1; }
  uv run ruff check >/dev/null 2>&1 && uv run pip-audit >/dev/null 2>&1 \
      && echo "6 mechanical(ruff/pip-audit): PASS" || { echo "6 mechanical: FAIL"; fail=1; }
  return $fail
}

case "$MODE" in
  --task) task_check "$@" ;;
  --spec) spec_check "$ID" ;;
  *) echo "usage: done-check.sh --spec <id> | --task <id> <task_id>"; exit 2 ;;
esac
```

> Note: conjunct 5 (reviewer zero-gaps) and the DeepEval baseline (conjunct for real-data eval) are wired in Task 6.x when baselines exist; the skeleton above gates coverage/tasks/unit/contracts/mechanical now and is extended (not replaced) once `_baseline.json` + reviewer output exist.

- [ ] **Step 2: chmod + smoke on doc00**

```bash
chmod +x done-check.sh
python3 scripts/decompose.py 00 >/dev/null
./done-check.sh --spec 00; echo "exit=$?"
```
Expected: prints the 6-conjunct table; exits nonzero (tasks not yet built) — proving the predicate computes and does not falsely pass.

- [ ] **Step 3: Write the guard test + commit**

```python
# tests/scripts/test_done_check.py
import subprocess, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
def test_done_check_computes_and_does_not_falsely_pass():
    subprocess.run(["python3","scripts/decompose.py","00"], cwd=ROOT, check=True)
    r = subprocess.run(["./done-check.sh","--spec","00"], cwd=ROOT, capture_output=True, text=True)
    assert "DONE(00)" in r.stdout
    assert r.returncode != 0            # nothing built yet -> must NOT report done
```

Run: `uv run pytest tests/scripts/test_done_check.py -q` → PASS.
```bash
git add done-check.sh tests/scripts/test_done_check.py
git commit -m "v2: done-check.sh — computable DONE predicate (6 conjuncts)"
```

### Task 4.7: `drive.sh` — the loop

**Files:**
- Create: `drive.sh`

**Interfaces:**
- Produces: `./drive.sh <id>` → decompose (if no tasks.json) → coverage gate → per-task build via the `build-slice` skill (writes `slices/<id>/.current` for the Stop gate) → `done-check --spec` → prints DONE or the BLOCKED list. Enforces a per-run cost ceiling via `scripts/cost_log.py`.

- [ ] **Step 1: Implement** (orchestrates existing pieces; the actual per-task coding is done by the agent under the `build-slice` skill — `drive.sh` sequences + gates, it does not write product code itself).

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 2
ID="${1:?usage: drive.sh <id>}"
mkdir -p "slices/$ID"
[ -f "slices/$ID/tasks.json" ] || python3 scripts/decompose.py "$ID" || { echo "decompose failed (EXTRACTION_COUNT_HALT?)"; exit 1; }
python3 scripts/coverage_gate.py "doc$ID" || { echo "coverage gap — fix the bundle (founder)"; exit 1; }
python3 scripts/task_coverage.py "$ID"   || { echo "task coverage gap"; exit 1; }
echo "== drive $ID: $(uv run python -c "import json;print(len(json.load(open('slices/'+'$ID'+'/tasks.json'))['tasks']))") tasks =="
echo "Next: run the build-slice skill per pending task (one task/session, /clear between)."
./done-check.sh --spec "$ID"
```

- [ ] **Step 2: chmod + smoke**

```bash
chmod +x drive.sh
./drive.sh 00; echo "exit=$?"
```
Expected: decompose + coverage + task-coverage run; prints the DONE table; nonzero (unbuilt) — the loop scaffolding works end-to-end.

- [ ] **Step 3: Commit**

```bash
git add drive.sh
git commit -m "v2: drive.sh — decompose->coverage->build-slice->done-check loop"
```

### Task 4.8: slices/ + regressions/ + tests/eval/ skeleton + DeepEval config

**Files:**
- Create: `slices/.gitkeep`, `regressions/.gitkeep`, `tests/eval/__init__.py`, `tests/eval/conftest.py`, `tests/eval/deepeval_config.py`.

- [ ] **Step 1: Scaffold** — `tests/eval/deepeval_config.py` pins the judge model (Opus/Sonnet per build directive) + weighted κ≥0.6 gate; `tests/eval/conftest.py` exposes a `held_out` fixture that reads from `fixtures/goldens/` and refuses to expose held-out inputs to a build agent (env `PROXY_HELD_OUT=1` required). Add a trivial `tests/eval/test_smoke_eval.py` that asserts DeepEval imports + a deterministic grader scores a fixed golden.

- [ ] **Step 2: Run + commit**

Run: `uv run pytest tests/eval/test_smoke_eval.py -q` → PASS.
```bash
git add slices/.gitkeep regressions/.gitkeep tests/eval/
git commit -m "v2: tests/eval DeepEval scaffold + slices/regressions skeletons"
```

---

## Phase 5 — Smoke the loop (BUILD-LOOP §10.5)

### Task 5.1: Prove the Stop hook self-repairs + done-check computes on a trivial task

**Files:**
- Create (temporary): `slices/smoke/tasks.json`, `tests/scripts/test_smoke_task.py`

- [ ] **Step 1: Restart Claude Code to load the v2 hooks**

STOP and ask the human partner to restart Claude Code (hooks load at session start; settings.json was wired in Task 3.8). After restart, confirm: `cat .claude/settings.json` shows the v2 hooks.

- [ ] **Step 2: Hand-author a trivial slice with a failing acceptance check**

`slices/smoke/tasks.json` = one task `{"task_id":"T-SMOKE","acceptance":{"cmd":"pytest -q tests/scripts/test_smoke_task.py"},"passes":false}`; `tests/scripts/test_smoke_task.py` asserts `1+1==2` (green) — but leave `passes:false`.

- [ ] **Step 3: Prove the Stop gate blocks then clears**

Write `smoke:T-SMOKE` to `slices/smoke/.current`. Run `./done-check.sh --task smoke T-SMOKE` → exit 1 (passes:false). Flip `passes:true`, re-run → exit 0. Delete `.current`.

- [ ] **Step 4: Prove pretool_guard blocks a test edit live**

Attempt (in-session) to edit `tests/scripts/test_smoke_task.py` → the PreToolUse guard must block it. Confirm the block message. (This proves hooks are loaded and enforcing.)

- [ ] **Step 5: Clean up the smoke slice + commit the evidence note**

```bash
git rm -r slices/smoke tests/scripts/test_smoke_task.py
echo "smoke: Stop gate blocks on passes:false and clears on true; pretool_guard blocks tests/ edits. $(git rev-parse --short HEAD)" >> evidence/v2-loop-smoke.md
git add evidence/v2-loop-smoke.md
git commit -m "v2: smoke — Stop gate self-repairs + pretool_guard enforces (evidence recorded)"
```

---

## Phase 6 — Readiness for the doc00–03 verification journey (prep for §10.6)

### Task 6.1: Decompose doc00–03 + green the coverage gates + snapshot baselines

**Files:**
- Create: `slices/{00,01,02,03}/tasks.json`, `slices/{00,01,02,03}/_baseline.json`, `docs/v2-readiness.md`

- [ ] **Step 1: Decompose + close coverage for each doc**

```bash
for id in 00 01 02 03; do
  python3 scripts/decompose.py "$id"
  python3 scripts/coverage_gate.py "doc$id" || echo "COVERAGE GAP doc$id (bundle needs founder fix)"
  python3 scripts/task_coverage.py "$id"   || echo "TASK GAP doc$id"
done
```
Expected: four `tasks.json` written; coverage gates PASS (the bundles are sealed/complete) or print a specific gap list (a founder bundle fix, not an agent edit).

- [ ] **Step 2: Snapshot real-data baselines from the existing evidence**

For each doc, seed `slices/<id>/_baseline.json` from the current green eval numbers in `evidence/doc0X-*.json` (relative-regression floor, not absolute). This file is human-gated thereafter.

- [ ] **Step 3: Run `done-check --spec` for each doc → capture the verdict**

```bash
for id in 00 01 02 03; do echo "### doc$id"; ./done-check.sh --spec "$id" || true; done | tee docs/v2-readiness.md
```
Expected: a per-doc, per-conjunct table. Because doc00–03 are already built, many conjuncts PASS; any FAIL is a specific, legible `BLOCKED:<reason>` — the exact list the journey must clear.

- [ ] **Step 4: Write the readiness verdict**

Append to `docs/v2-readiness.md`: for each of the four verification tracks (00 Foundation / 01 Code-Intel / 02 Voice / 03 Notes), state **READY** (all preconditions green) or **BLOCKED:<reason>**. This is the go/no-go for the 4-track parallel journey (§9) + the integration gate (Doc 09 §2 + S1 happy arc).

- [ ] **Step 5: Commit**

```bash
git add slices docs/v2-readiness.md
git commit -m "v2: doc00-03 decomposed, coverage closed, baselines snapshotted — readiness verdict for the verification journey"
```

---

## What comes AFTER this plan (not built here — needs the readiness verdict first)

- **§10.6 — the first journey:** four parallel doc00–03 verification tracks in git worktrees (Foundation / Code-Intel / Voice / Notes) → merge → `scripts/journey.py contracts` + `scenario S1` on the assembled tree + one gated live e2e → DONE or a specific `BLOCKED:` list. Run via `superpowers:subagent-driven-development` with worktree isolation.
- **§10.7 — `drive.sh 04`, `drive.sh 05`, `08`, `09`:** the same loop, dynamically, for every remaining doc.
- **Open question to resolve at the journey:** does the founder "No Haiku" directive retire `claude-haiku-4-5` from the *product* routing table (`libs/llm/routing.py`), or only from the build loop? doc00–03 currently ship Haiku for scribe/gates.

---

## Self-Review (run against BUILD-LOOP.md §10 + AGENTS.md)

**Spec coverage — §10 steps → tasks:**
- §10.1 salvage+archive → Tasks 1.1–1.3 ✓
- §10.2 delete v1 (post src/spike import check) → Task 2.1 (import re-check in Step 1) ✓; spike/ KEPT (verified live) ✓
- §10.3 write v2 protected files → Tasks 3.1–3.8 (CLAUDE.md, 4 hooks, 2 agents, 2 skills, settings.json) ✓
- §10.4 write v2 engine → Tasks 4.0–4.8 (deps, lib_spec, decompose, task_coverage, cost_log, journey, done-check, drive, eval scaffold) ✓
- §10.5 smoke the loop → Task 5.1 (Stop self-repair + guard enforcement, live) ✓
- §10.6 first journey → prepped by Task 6.1 (readiness verdict); execution deferred (needs go/no-go) ✓
- §10.7 drive 04+ → documented as post-plan ✓

**DONE predicate (§3) → done-check.sh:** coverage-closed (1) ✓, all-tasks-pass (2) ✓, real-data-eval (baseline; wired Task 6.2 note) ~ , integration (5) ✓, reviewer-zero-gaps (agent exists; wired at journey) ~ , mechanical (6) ✓. The two `~` are honestly deferred to when baselines/reviewer output exist — done-check EXTENDS, never silently passes.

**Anti-gaming (§3 / BUILD-PLAN-AMENDMENT-01):** pretool_guard makes tests/acceptance/goldens/baseline read-only ✓; `passes` flips gated by real `acceptance.cmd` in done-check ✓; extraction-count HALT is founder-gated, never auto-approved ✓; held-out eval inputs never exposed to the build agent (Task 4.8 fixture) ✓.

**Invariants:** hooks reuse `ops.check_*`/`ops.lint.naming` (no reimplementation) ✓; contract homes respected (journey imports `libs.contracts.assert_registry_closed`) ✓; No-Haiku (build loop) vs product-routing kept separate + flagged ✓.

**Placeholder scan:** the two `~` conjuncts above are explicitly-scoped deferrals with a named trigger, not "TODO later." Task 4.3/4.4/4.8 give interface + test + trigger; their bodies are derived to pass the stated test (TDD). No banned placeholders.

**Type consistency:** `id` (bare `"00"`) vs `doc_name(id)` (`"doc00"`) used consistently — `coverage_gate`/`extraction_count_gate` take `doc<NN>`; `decompose`/`task_coverage`/`drive`/`slice_dir` take bare `<id>`; `lib_spec` bridges. `tasks.json` schema (`task_id`,`criterion_ids`,`acceptance.cmd`,`passes`) is identical across decompose/task_coverage/done-check/drive/stop_gate. ✓
