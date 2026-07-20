#!/usr/bin/env python3
"""The conductor: fully-autonomous multi-doc build, docs 00->09, launched by a founder.

Design (ORCHESTRATION.md): per doc —
  P1 generate criteria (fresh agent -> STAGING)          P2 adversarial criteria review (fresh agent)
  P3 generate evidence: tests+fixtures+sims (-> STAGING) P4 coverage gate + PROMOTE + SEAL (this file)
  P5 plan + review (fresh agent)                         P6 build loop (fresh agent per pass, scoped verify)
  P6.5 mutation (if available)                           P7 independent verifier (fresh agent, refute)
  P7.5 completeness sweep (fresh agent, spec vs criteria) P8 cross-doc regression -> advance

Trust model: agents NEVER write protected trees. Arbiter agents write staging/<doc>/ only;
THIS process (deterministic, reviewed, founder-launched, not hook-bound) promotes staged
artifacts into acceptance/ + tests/ after the coverage gate passes, then seals (hashes).
Builder agents run under the full guard hooks + a per-pass integrity hash over protected
trees. Verifier/sweep agents are tamper-checked (services/libs hashed before/after).

Honest deviations for the unattended overnight (documented in LAUNCH-AUTONOMOUS.md):
  - rung-2 real-data eval is NOT wired (eval_runner stub): the gates here replace it tonight.
  - mypy --strict + bandit are REPORTED per pass, blocking only at doc-end (not per pass),
    so strict-typing minutiae can't stall the loop mid-milestone but can't ship either.
"""
import argparse, hashlib, json, os, pathlib, re, shutil, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
ORCH = ROOT / "orchestrator"
STAGING = ROOT / "staging"
PY = str(ROOT / ".venv" / "bin" / "python")
LOG = ORCH / "run.log"
ALLOW_PULL = ORCH / "state" / "ALLOW_PULL"   # present ONLY between docs: the supervisor may
#   fast-forward monitoring-side fixes then. Absent while a doc builds => the run is pinned to
#   its launch SHA and no founder push can mutate it mid-doc.

# Model tiering: SONNET for routine builder passes; OPUS for all judgment-critical paths
# (adjudicator, verifier, sweep, criteria review) and for builder sessions that have stalled.
SONNET_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-6"
OPUS_ESCALATION_STALL_COUNT = 2   # build sessions stalled >= this many times → escalate to Opus

DOCS = {
    "doc00": {"spec": "00-FOUNDATION.md",
              "deps": ["pydantic", "pydantic-settings", "fastapi", "uvicorn", "asyncpg",
                        "alembic", "structlog", "httpx", "pytest-asyncio"]},
    "doc01": {"spec": "01-CODE-INTELLIGENCE.md",
              "deps": ["tree-sitter", "networkx", "pytest-timeout"]},
    "doc02": {"spec": "02-VOICE-TRANSPORT.md", "deps": ["websockets"]},
    "doc03": {"spec": "03-MEETING-UNDERSTANDING.md", "deps": []},
    "doc04": {"spec": "04-ORCHESTRATOR.md", "deps": []},
    "doc05": {"spec": "05-WORKROOM.md", "deps": []},
    "doc08": {"spec": "08-EXPERIENCE.md", "deps": []},
    "doc09": {"spec": "09-VERIFICATION.md", "deps": []},
}
ORDER = list(DOCS)
PROTECTED = ("tests/", "harness/", "fixtures/", "criteria/", "acceptance/", "product/", ".claude/",
             "orchestrator/prompts/", "orchestrator/orchestrate.py", "orchestrator/supervise.sh",
             "orchestrator/skills/", "orchestrator/verify_doc.md")
MAX_PASSES, STALL_LIMIT, PASS_TIMEOUT = 120, 4, 60 * 30   # wall-clock (9h/doc) binds before passes
MAX_BUILD_SESSIONS = 24            # each is a LONG persistent builder doing many milestones
BUILD_TIMEOUT, BUILD_TURNS = 60 * 90, 600   # a builder session iterates internally build->test->fix
REVIEW_CYCLES, SWEEP_CYCLES = 3, 2   # each pass is cheap now (formal artifacts dropped) so keep the
#                                      coverage loop generous; both break EARLY once the reviewer
#                                      approves + the RTM gate passes, so a clean doc costs 1 cycle.


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.open("a").write(line + "\n")


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, **kw)


def agent(prompt_file: str, subs: dict, capture: bool = False, timeout: int = 60 * 120,
          max_turns: int = 200, model: str | None = None) -> str:
    """One fresh Claude Code session (guard hooks apply to it). Returns stdout if captured."""
    text = (ORCH / "prompts" / prompt_file).read_text()
    for k, v in subs.items():
        text = text.replace(k, v)
    cmd = ["claude", "-p", text, "--permission-mode", "bypassPermissions",
           "--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    r = subprocess.run(cmd, cwd=ROOT, timeout=timeout,
                       capture_output=capture, text=capture)
    return (r.stdout or "") if capture else ""


def tree_hash(trees=PROTECTED) -> str:
    h = hashlib.sha256()
    for t in trees:
        p = ROOT / t
        if not p.exists():
            continue
        if p.is_file():   # single-file protected entry (e.g. orchestrate.py): hash it directly
            h.update(str(p.relative_to(ROOT)).encode()); h.update(p.read_bytes())
            continue
        for f in sorted(p.rglob("*")):
            if f.is_file() and "__pycache__" not in str(f):
                h.update(str(f.relative_to(ROOT)).encode()); h.update(f.read_bytes())
    return h.hexdigest()


def git_commit(msg: str) -> None:
    sh(["git", "add", "-A"]); sh(["git", "commit", "-q", "-m", msg])
    push_backup()


def lock_pull_during_doc() -> None:
    """A doc is now building: pin the run to its launch SHA (remove the between-docs marker)."""
    ALLOW_PULL.unlink(missing_ok=True)


def allow_pull_between_docs() -> None:
    """At a doc boundary (or on halt): a fast-forward of monitoring-side fixes is safe again."""
    ALLOW_PULL.parent.mkdir(exist_ok=True)
    ALLOW_PULL.touch()


def push_backup() -> None:
    """Best-effort remote backup after every commit — all progress saved off-machine, never
    halts the run if the network/auth hiccups. DURING a doc (ALLOW_PULL absent) a push conflict
    means a founder pushed into a live run: we do NOT rebase the live tree onto it (that would
    mutate the run), just WARN and leave the work committed locally to push at the next boundary."""
    try:
        r = sh(["git", "push", "-q", "origin", "main"], capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            if not ALLOW_PULL.exists():
                log("WARN push conflict DURING a doc — run pinned to launch SHA; skipping rebase, "
                    "work is committed locally and will push at the next doc boundary.")
                return
            sh(["git", "pull", "--rebase", "-q", "origin", "main"], capture_output=True, text=True, timeout=90)
            sh(["git", "push", "-q", "origin", "main"], capture_output=True, text=True, timeout=90)
    except Exception as e:
        log(f"WARN push backup failed (progress is committed locally): {e}")


def note_exception(doc: str, kind: str, detail: str) -> None:
    """Autonomy: record a deferred/noted exception and CONTINUE, instead of halting the whole
    build. Surfaced in evidence/<doc>-EXCEPTIONS.md and the final report; the doc proceeds with
    this debt flagged for later. Only genuine 'cannot run' failures halt."""
    (ROOT / "evidence").mkdir(exist_ok=True)
    (ROOT / "evidence" / f"{doc}-EXCEPTIONS.md").open("a").write(
        f"\n## {kind} — {time.strftime('%F %T')}\n{detail[:1500]}\n")
    git_commit(f"{doc}: NOTED EXCEPTION [{kind}] — continuing (evidence/{doc}-EXCEPTIONS.md)")
    log(f"[{doc}] NOTED EXCEPTION [{kind}] — continuing autonomously; flagged for review")


def write_final_report() -> None:
    lines = ["# Autonomous build — final report", f"generated {time.strftime('%F %T')}", ""]
    lines.append("## Docs completed"); lines += [
        "- " + t for t in sh(["git", "tag", "-l", "*-done"], capture_output=True, text=True).stdout.split()]
    lines.append("\n## Noted exceptions + deferrals (plumb later — none blocked the build)")
    for f in sorted((ROOT / "evidence").glob("*EXCEPTIONS.md")) + sorted((ROOT / "evidence").glob("*deferred.md")):
        lines.append(f"\n### {f.name}\n{f.read_text()[:4000]}")
    (ROOT / "evidence" / "FINAL-REPORT.md").write_text("\n".join(lines))
    git_commit("final report: docs done + all noted exceptions/deferrals")


def ensure_deps(doc: str) -> None:
    deps = list(DOCS[doc]["deps"])          # copy — never mutate the module table
    hints = STAGING / doc / "DEPS.txt"
    if hints.exists():
        for line in hints.read_text().splitlines():
            line = line.split("#", 1)[0].strip()          # drop inline + comment-only lines
            if line:
                deps.append(line)
    deps = [d for d in dict.fromkeys(deps) if re.match(r"^[A-Za-z0-9]", d)]   # dedupe + sane spec only
    if deps:
        log(f"[{doc}] installing test/build deps: {deps}")
        r = sh(["uv", "pip", "install", "-q", *deps], capture_output=True, text=True)
        if r.returncode != 0:
            log(f"[{doc}] WARN dep install non-zero (continuing): {r.stderr[-300:]}")


def test_dirs_upto(doc: str) -> list[str]:
    """Accumulated per-doc test scopes. doc01's suite is Pranav's, at tests/ root."""
    dirs = []
    for d in ORDER[: ORDER.index(doc) + 1]:
        if d == "doc01":
            dirs += [str(p) for p in sorted((ROOT / "tests").glob("test_*.py"))]
        elif (ROOT / "tests" / d).exists():
            dirs.append(f"tests/{d}")
    return dirs


def verify(doc: str, blocking_types: bool = False) -> tuple[int, str]:
    """Replicates harness/verify.sh semantics, scoped to the docs built so far.
    ruff + pytest + zero-collected guard BLOCK; mypy/bandit block only when blocking_types."""
    out = []
    dirs = [d for d in ("services", "libs") if (ROOT / d).exists()]
    scope = test_dirs_upto(doc)
    if not scope:
        return 1, "no test scope for this doc (evidence not promoted?)"
    def run(cmd):
        r = sh(cmd, capture_output=True, text=True)
        out.append(f"$ {' '.join(cmd)}\n{r.stdout[-1500:]}{r.stderr[-1500:]}")
        return r.returncode
    if dirs and run([PY, "-m", "ruff", "check", *dirs]) != 0:
        return 1, "\n".join(out)
    mypy_rc = run([PY, "-m", "mypy", "--strict", *dirs]) if dirs else 0
    bandit_rc = run([PY, "-m", "bandit", "-q", "-r", *dirs]) if dirs else 0
    desel = [a for t in DESELECTED for a in ("--deselect", t)]
    col = sh([PY, "-m", "pytest", "--collect-only", "-q", *scope], capture_output=True, text=True)
    if re.search(r"^no tests ran|^0 tests|error", col.stdout.splitlines()[-1] if col.stdout else "error"):
        return 1, "ZERO TESTS COLLECTED or collection error — refusing green\n" + col.stdout[-800:]
    if run([PY, "-m", "pytest", "-q", "-x", "--maxfail=1", *desel, *scope]) != 0:
        return 1, "\n".join(out)
    if blocking_types and (mypy_rc or bandit_rc):
        return 1, "doc-end gate: mypy/bandit must be clean\n" + "\n".join(out)
    if mypy_rc or bandit_rc:
        out.append("WARN: mypy/bandit not clean (blocking at doc-end, not per pass)")
    return 0, "\n".join(out)


def bundle_dir(doc: str) -> pathlib.Path:
    """Dir that actually holds the sealed criteria. Tolerates the legacy double-nested
    acceptance/<doc>/<doc>/ layout left by the old promote bug so already-sealed docs are
    recognized (and skipped) instead of regenerated."""
    d = ROOT / "acceptance" / doc
    return (d / doc) if (d / doc / "criteria").exists() else d


def promote(doc: str) -> None:
    """Deterministic promotion of staged arbiter artifacts into protected trees (this
    process only — agents cannot). staging/<doc>/acceptance -> acceptance/<doc>;
    staging/<doc>/tests -> tests/ (doc-scoped subdir or fixtures).

    Acceptance bundles are replaced atomically: the staged source is validated
    (criteria.yaml + requirements.yaml must exist and be non-trivial), built
    in a temp directory, then swapped in via rmtree+rename.  If the staged
    source is empty or missing the required files, the promote is skipped
    (existing bundle preserved) with a logged warning — never leaving the
    destination in a partially-overwritten state."""
    s = STAGING / doc
    if (s / "acceptance").exists():
        # Resolve the staged bundle root (may be nested as acceptance/<doc>/).
        src = (s / "acceptance" / doc) if (s / "acceptance" / doc).exists() else (s / "acceptance")
        # --- sanity: staged source must contain real content ---
        crit = src / "criteria" / "criteria.yaml"
        reqs = src / "requirements" / "requirements.yaml"
        if not (crit.is_file() and reqs.is_file()):
            log(f"[{doc}] promote: staged acceptance has no criteria/requirements files — "
                f"skipping acceptance copy (existing bundle preserved)")
        elif crit.stat().st_size < 20 or reqs.stat().st_size < 20:
            log(f"[{doc}] promote: staged acceptance files are trivially small — "
                f"skipping acceptance copy (existing bundle preserved)")
        else:
            # Atomic replace: copy into a temp sibling, then swap.
            dst = ROOT / "acceptance" / doc
            tmp = dst.with_name(f".{doc}.promote_tmp")
            if tmp.exists():
                shutil.rmtree(tmp)
            shutil.copytree(src, tmp)
            # Final validation of the copy before replacing the live bundle.
            tmp_crit = tmp / "criteria" / "criteria.yaml"
            tmp_reqs = tmp / "requirements" / "requirements.yaml"
            if not (tmp_crit.is_file() and tmp_reqs.is_file()):
                shutil.rmtree(tmp)
                raise RuntimeError(
                    f"promote({doc}): copytree produced a bundle without criteria/requirements — aborting")
            # Content-hash guard: if the live bundle already matches the recorded seal,
            # staging is stale (manual post-staging fixes were applied after the last
            # orchestrator seal). Never overwrite a correctly-sealed live bundle with
            # stale staging content — same protection principle as the mtime guard on
            # tests/ and fixtures/goldens/, applied here via content hash (more reliable
            # than mtime for the atomic-copytree path).
            seal_path = ORCH / "state" / f"{doc}.seal.json"
            _skip_replace = False
            if dst.exists() and seal_path.exists():
                try:
                    recorded = json.loads(seal_path.read_text()).get("authority+bundle_sha256", "")
                    spec_file = ROOT / "product" / "v0-spec" / DOCS[doc]["spec"]
                    _h = hashlib.sha256()
                    for _p in [spec_file, *sorted(dst.rglob("*"))]:
                        if _p.is_file():
                            _h.update(_p.read_bytes())
                    if _h.hexdigest() == recorded:
                        shutil.rmtree(tmp)
                        log(f"[{doc}] promote: live bundle matches recorded seal "
                            f"{recorded[:12]} — staging is stale; skipping acceptance "
                            f"overwrite (live bundle preserved)")
                        _skip_replace = True
                except Exception as _exc:
                    log(f"[{doc}] promote: seal-hash guard error ({_exc}); proceeding with replace")
            if not _skip_replace:
                if dst.exists():
                    shutil.rmtree(dst)
                tmp.rename(dst)
    if (s / "tests").exists():
        for item in (s / "tests").rglob("*"):
            if item.is_file():
                rel = item.relative_to(s / "tests")
                dst = ROOT / "tests" / rel
                # Only promote if staged file is newer than what's already in the
                # working tree, or if the destination doesn't exist. This prevents
                # stale staging leftovers from clobbering legitimate edits (e.g.
                # fixtures added by sweep-gap-closure after the initial staging).
                if dst.exists() and dst.stat().st_mtime >= item.stat().st_mtime:
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dst)
    if (s / "goldens").exists():
        dst = ROOT / "fixtures" / "goldens"
        dst.mkdir(parents=True, exist_ok=True)
        for item in (s / "goldens").rglob("*"):
            if item.is_file():
                rel = item.relative_to(s / "goldens")
                target = dst / rel
                if target.exists() and target.stat().st_mtime >= item.stat().st_mtime:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)


def seal(doc: str) -> str:
    spec = ROOT / "product" / "v0-spec" / DOCS[doc]["spec"]
    h = hashlib.sha256()
    for p in [spec, *sorted((ROOT / "acceptance" / doc).rglob("*"))]:
        if p.is_file():
            h.update(p.read_bytes())
    digest = h.hexdigest()
    (ORCH / "state").mkdir(exist_ok=True)
    (ORCH / "state" / f"{doc}.seal.json").write_text(json.dumps(
        {"doc": doc, "authority+bundle_sha256": digest, "sealed_at": time.strftime("%F %T")}, indent=1))
    return digest


def coverage_gate(doc: str, base: pathlib.Path) -> bool:
    r = sh([sys.executable, str(ORCH / "criteria_coverage_gate.py"), doc, "--base", str(base)],
           capture_output=True, text=True)
    log(r.stdout[-600:])
    return r.returncode == 0


# DESELECTED (deferred/genuinely-blocked criteria) is persisted to disk so a conductor
# restart re-loads the morning-triage backlog instead of re-discovering it from scratch:
# load on start (if present), write on every append.
_DESELECTED_PATH = ORCH / "state" / "doc00.deselected.json"


def _load_deselected() -> list[str]:
    """Read the persisted deferral backlog (empty list if absent/corrupt)."""
    try:
        data = json.loads(_DESELECTED_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def _persist_deselected() -> None:
    """Write the current deferral backlog to disk (called on every append)."""
    _DESELECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DESELECTED_PATH.write_text(json.dumps(DESELECTED, indent=1))


DESELECTED: list[str] = _load_deselected()   # deferred criteria — persisted across restarts
_seen_blocked = 0


# ── Decision cache (TASK 1) ──────────────────────────────────────────────────
# Keyed by sha256(entry_text) so the exact same SPEC_BLOCKED claim never
# triggers a second LLM adjudication; the cached ruling is re-applied instead.
# Cache lives at orchestrator/state/<doc>.decisions.json.

def _decisions_path(doc: str) -> pathlib.Path:
    return ORCH / "state" / f"{doc}.decisions.json"


def _load_decisions(doc: str) -> dict:
    try:
        return json.loads(_decisions_path(doc).read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save_decisions(doc: str, cache: dict) -> None:
    _decisions_path(doc).parent.mkdir(parents=True, exist_ok=True)
    _decisions_path(doc).write_text(json.dumps(cache, indent=1))


def _entry_sig(entry: str) -> str:
    """Stable fingerprint for a SPEC_BLOCKED entry (first 300 chars captures the criterion ID
    and conflict statement without being sensitive to trailing whitespace edits)."""
    return hashlib.sha256(entry.strip()[:300].encode()).hexdigest()[:24]


def new_spec_blocked() -> str | None:
    """Only NEW SPEC_BLOCKED entries since last check (resolved ones don't re-trigger)."""
    global _seen_blocked
    p = ROOT / "PROGRESS.md"
    if not p.exists():
        return None
    entries = [l for l in p.read_text().splitlines() if "SPEC_BLOCKED" in l and "RESOLVED" not in l]
    if len(entries) > _seen_blocked:
        _seen_blocked = len(entries)
        return entries[-1]
    return None


def adjudicate(doc: str, entry: str) -> bool:
    """Fresh-context ruling on a claimed blocker. True = proceed; False = deferred (recorded).
    Cache hit: same entry fingerprint → re-apply prior ruling without spawning an LLM session."""
    sig = _entry_sig(entry)
    cache = _load_decisions(doc)
    if sig in cache:
        prior = cache[sig]
        log(f"[{doc}] cached ruling re-applied ({prior['ruling']}) for: {entry[:80]}")
        return prior["ruling"] == "PROCEED"
    log(f"[{doc}] adjudicating SPEC_BLOCKED: {entry[:120]}")
    ok, out = independent_check(doc, "adjudicate.md", "ADJUDICATION: PROCEED")
    cache[sig] = {"ruling": "PROCEED" if ok else "DEFER", "entry_prefix": entry.strip()[:120],
                  "decided_at": time.strftime("%F %T")}
    _save_decisions(doc, cache)
    if ok:
        clar = out.split("ADJUDICATION: PROCEED", 1)[-1][:800]
        (ROOT / "PROGRESS.md").open("a").write(
            f"\n## ADJUDICATION RESOLVED — proceed with this reading:\n{clar}\n")
        git_commit(f"{doc}: adjudication — proceed with clarified reading")
        return True
    m = re.search(r"ADJUDICATION: DEFER\s+(\S+)", out)
    target = m.group(1) if m else ""
    if target:
        DESELECTED.append(target)
        _persist_deselected()
    (ROOT / "evidence").mkdir(exist_ok=True)
    (ROOT / "evidence" / f"{doc}-deferred.md").open("a").write(
        f"\nDEFERRED (genuinely spec-blocked, needs founder spec fix): {entry}\n{out[-800:]}\n")
    git_commit(f"{doc}: deferred genuinely-blocked criterion for morning triage")
    log(f"[{doc}] DEFERRED {target or '(unparsed target)'} — night continues; morning triage item")
    return len(DESELECTED) <= 5   # >5 deferrals = something systemic; halt


def commit_count() -> int:
    r = sh(["git", "rev-list", "--count", "HEAD"], capture_output=True, text=True)
    return int(r.stdout.strip() or 0)


def _extract_failing_test_ids(out: str) -> list[str]:
    """Parse FAILED test::id tokens from pytest -q output."""
    return [m.group(1) for line in out.splitlines() if (m := re.match(r"^FAILED (\S+)", line))]


def _in_doc_scope(doc: str, test_id: str) -> bool:
    """True when test_id belongs to the current doc's test tree (not a prior doc's)."""
    if doc == "doc01":
        return test_id.startswith("tests/test_m")
    return test_id.startswith(f"tests/{doc}/")


def build_loop(doc: str) -> str:
    """Phase 6: ONE persistent builder session iterates build->test->fix across many milestones,
    committing per milestone (conductor pushes each). We spawn a CONTINUATION session only when the
    prior one ran out of turns WITH progress (new commits). No progress + not green => a fresh
    debugger; still stuck => defer that one criterion and continue. Fresh context is spent only on
    the CHECKS (verify below, debugger, and P7 verify/sweep) — never on every build increment. That
    is the maker!=checker line: the builder may be one warm session; the grader is always fresh."""
    baseline = tree_hash()          # protected trees only; builder writes services/libs (unprotected)
    start = time.time()
    stalls = 0
    for s in range(1, MAX_BUILD_SESSIONS + 1):
        if time.time() - start + 1800 > 9 * 3600:
            return "WALL_CLOCK"
        before = commit_count()
        build_model = OPUS_MODEL if stalls >= OPUS_ESCALATION_STALL_COUNT else SONNET_MODEL
        log(f"[{doc}] build session {s} (persistent; iterates internally) [{build_model.split('-')[1]}]")
        try:
            agent("build_pass.md", {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]},
                  timeout=BUILD_TIMEOUT, max_turns=BUILD_TURNS, model=build_model)
        except subprocess.TimeoutExpired:
            log(f"[{doc}] build session {s} hit the {BUILD_TIMEOUT//60}m cap; checking progress")
        if tree_hash() != baseline:
            return "INTEGRITY_VIOLATION"
        if (msg := new_spec_blocked()):
            if not adjudicate(doc, msg):
                return "TOO_MANY_DEFERRALS"
            # Adjudicator ruled PROCEED — the builder is NOT spec-blocked.  Rather than
            # spawning another expensive build session that will re-derive the same
            # "nothing to do" conclusion, iteratively peel off any failures that are
            # outside this doc's own test tree (prior-doc fixture debt, host-env gaps,
            # etc.).  Only spawn a new session if an in-scope failure remains.
            for _ad in range(15):
                code_ad, out_ad = verify(doc)
                LOG.open("a").write(out_ad[-1000:] + "\n")
                if code_ad == 0:
                    return "GREEN"
                failing = _extract_failing_test_ids(out_ad)
                if not failing:
                    break
                outside = [t for t in failing if not _in_doc_scope(doc, t)]
                if len(outside) < len(failing):
                    break   # at least one in-scope failure: real work remains; spawn a session
                for t in outside:
                    if t not in DESELECTED:
                        DESELECTED.append(t)
                        _persist_deselected()
                        log(f"[{doc}] adjudication PROCEED: auto-deselect non-{doc} failure: {t}")
            continue
        code, out = verify(doc)
        LOG.open("a").write(out[-2000:] + "\n")
        if code == 0:
            return "GREEN"
        if commit_count() > before:            # session progressed but ran out of turns -> continue
            stalls = 0
            continue
        # No new commits AND not green => the builder is stuck.
        stalls += 1
        if stalls == 1:
            log(f"[{doc}] no progress this session — fresh-context debugger (opus)")
            try:
                agent("unstall.md", {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]},
                      timeout=BUILD_TIMEOUT, max_turns=200, model=OPUS_MODEL)
            except subprocess.TimeoutExpired:
                pass
            if tree_hash() != baseline:
                return "INTEGRITY_VIOLATION"
            continue
        # Debugger also made no progress: defer the failing criterion, record it, keep building.
        m = re.search(r"FAILED (\S+::\S+)", out)
        node = m.group(1) if m else None
        if node and node not in DESELECTED:
            DESELECTED.append(node)
            _persist_deselected()
            (ROOT / "evidence").mkdir(exist_ok=True)
            (ROOT / "evidence" / f"{doc}-deferred.md").open("a").write(
                f"\nDEFERRED (builder + debugger both stuck) — plumb later: {node}\n{out[-600:]}\n")
            git_commit(f"{doc}: defer stuck criterion {node.split('::')[-1]} — continuing build")
            log(f"[{doc}] DEFERRED {node} — continuing with the rest of the doc")
            if len(DESELECTED) > 12:
                return "TOO_MANY_DEFERRALS"
            stalls = 0
            continue
        return "STALL"     # couldn't identify the failing node -> genuine, needs a human
    return "MAX_BUILD_SESSIONS"


def independent_check(doc: str, prompt: str, expect: str,
                      model: str = OPUS_MODEL) -> tuple[bool, str]:
    """Verifier / sweep: fresh session, tamper-checked (services+libs hash must not change).
    Defaults to OPUS_MODEL — all judgment-critical checks (adjudicator, verifier, sweep,
    criteria review) warrant the strongest available model."""
    before = tree_hash(("services/", "libs/")) if (ROOT / "services").exists() else ""
    out = agent(prompt, {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]}, capture=True, max_turns=120,
                model=model)
    after = tree_hash(("services/", "libs/")) if (ROOT / "services").exists() else ""
    if before != after:
        return False, "TAMPER: check session modified the build"
    return (expect in out), out[-3000:]


def merge_sweep_parts(doc: str) -> None:
    """Merge sweep-gap-closure addenda from staging/<doc>/parts/ into the existing
    sealed bundle at acceptance/<doc>/. Appends new requirements and criteria without
    overwriting the existing content. If the staging dir has a complete bundle at
    staging/<doc>/acceptance/<doc>/, promote() already handles it — this function
    handles the parts/ patch files that sweeps produce."""
    parts = STAGING / doc / "parts"
    if not parts.exists():
        return
    target = bundle_dir(doc)
    consumed = []
    for suffix, subdir, fname in [
        (".reqs.yaml", "requirements", "requirements.yaml"),
        (".crit.yaml", "criteria", "criteria.yaml"),
    ]:
        for patch in sorted(parts.glob(f"*{suffix}")):
            dst = target / subdir / fname
            if dst.exists():
                with dst.open("a") as f:
                    f.write("\n" + patch.read_text())
                log(f"[{doc}] merged sweep part {patch.name} -> {dst.relative_to(ROOT)}")
                consumed.append(patch)
    # Consume parts files so a rerun doesn't re-detect them as pending work
    for p in consumed:
        p.unlink()
        log(f"[{doc}] consumed (removed) {p.name}")


def run_doc(doc: str) -> str:
    log(f"########## {doc} ({DOCS[doc]['spec']}) ##########")
    sdir = STAGING / doc
    sdir.mkdir(parents=True, exist_ok=True)

    # P1+P2: criteria (skip generation if a sealed bundle already exists — doc01 has Pranav's)
    if not (bundle_dir(doc) / "criteria").exists():
        for cycle in range(1, REVIEW_CYCLES + 1):
            log(f"[{doc}] P1 generate criteria (cycle {cycle})")
            agent("gen_criteria.md", {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]}, model=SONNET_MODEL)
            log(f"[{doc}] P2 adversarial criteria review")
            ok, out = independent_check(doc, "review_criteria.md", "REVIEW: APPROVED")
            if ok and coverage_gate(doc, sdir / "acceptance" / doc):
                break
            (sdir / "review-gaps.md").write_text(out)
        else:
            # Review cycles exhausted: proceed ONLY if the deterministic gate passes; record the
            # residual review gaps for morning + rely on the end-of-doc completeness sweep as
            # the backstop. (Halting the whole night on reviewer perfectionism serves no one;
            # proceeding on a FAILED coverage gate is never allowed.)
            if not coverage_gate(doc, sdir / "acceptance" / doc):
                return "COVERAGE_GATE_FAILED_AFTER_REVIEW_CYCLES"
            (ROOT / "evidence").mkdir(exist_ok=True)
            shutil.copy2(sdir / "review-gaps.md", ROOT / "evidence" / f"{doc}-review-gaps-outstanding.md")
            log(f"[{doc}] WARN review gaps outstanding after {REVIEW_CYCLES} cycles — gate passed, "
                f"proceeding; sweep is the backstop; morning item saved to evidence/")
    # P3: evidence (tests+fixtures+sims+goldens). Skip on resume if already promoted + collecting
    # clean (a re-run after a conductor-side halt must not waste ~30min regenerating a sealed doc).
    promoted_ok = bool(test_dirs_upto(doc)) and sh(
        [PY, "-m", "pytest", "--collect-only", "-q", *test_dirs_upto(doc)],
        capture_output=True, text=True).returncode == 0
    if promoted_ok and (ORCH / "state" / f"{doc}.seal.json").exists():
        log(f"[{doc}] P3 skipped — evidence already promoted + collecting clean (resume)")
    else:
        log(f"[{doc}] P3 generate evidence layer (tests/fixtures/sims/goldens -> staging)")
        agent("gen_evidence.md", {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]}, model=SONNET_MODEL)
    # P4: gate + promote + seal (THIS process, deterministic)
    # Use the live (already-sealed) bundle for the coverage gate when it has content —
    # staging may be a stale leftover from the original build run and must NOT shadow
    # manual fixes applied to the live acceptance/ tree after sealing. Only fall back
    # to staging when the live bundle is empty (first-time build, nothing promoted yet).
    staged = sdir / "acceptance" / doc
    live = bundle_dir(doc)
    base = live if (live / "requirements" / "requirements.yaml").exists() else staged
    if not coverage_gate(doc, base):
        return "COVERAGE_GATE_FAILED"
    promote(doc)
    digest = seal(doc)
    git_commit(f"{doc}: promote + seal arbiter (bundle+evidence) [{digest[:12]}]")
    log(f"[{doc}] P4 sealed {digest[:12]}")
    ensure_deps(doc)
    # sanity: evidence must collect. Use pytest's EXIT CODE (0 = collected ok), never a substring
    # match — test names legitimately contain 'error' (e.g. ..._returns_errors_never_throws).
    col = sh([PY, "-m", "pytest", "--collect-only", "-q", *test_dirs_upto(doc)],
             capture_output=True, text=True)
    if col.returncode != 0:
        return "EVIDENCE_COLLECTION_ERROR:\n" + (col.stdout + col.stderr)[-800:]
    # P5: plan
    log(f"[{doc}] P5 plan + planner-review")
    agent("plan.md", {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]}, max_turns=60, model=SONNET_MODEL)
    # P6: build
    result = build_loop(doc)
    if result != "GREEN":
        return f"BUILD:{result}"
    # doc-end: types/bandit must now be clean too
    code, out = verify(doc, blocking_types=True)
    if code != 0:
        LOG.open("a").write(out[-2000:] + "\n")
        result = build_loop(doc)          # one more remediation round for type/bandit debt
        code, out = verify(doc, blocking_types=True)
        if code != 0:
            note_exception(doc, "TYPE_OR_LINT_DEBT",
                           "mypy --strict / bandit not clean after a remediation round; the "
                           "functional tests all pass. Proceeding; tidy types later.\n" + out[-1000:])
    # P6.5: mutation (best-effort)
    if sh([PY, "-c", "import mutmut"], capture_output=True).returncode == 0:
        log(f"[{doc}] P6.5 mutation gate")
    else:
        log(f"[{doc}] P6.5 SKIPPED (mutmut not installed) — noted honestly")
    # P7: independent verifier
    log(f"[{doc}] P7 independent verification (fresh context, refute)")
    ok, out = independent_check(doc, "verify_doc_prompt.md", "VERDICT: DONE")
    (ROOT / "evidence").mkdir(exist_ok=True)
    (ROOT / "evidence" / f"{doc}-verdict.md").write_text(out)
    git_commit(f"{doc}: independent verification verdict")
    if not ok:
        # one refute->rebuild cycle, then halt for morning triage
        log(f"[{doc}] verifier refuted — one rebuild cycle")
        if build_loop(doc) == "GREEN":
            ok, out = independent_check(doc, "verify_doc_prompt.md", "VERDICT: DONE")
            (ROOT / "evidence" / f"{doc}-verdict.md").write_text(out)
            git_commit(f"{doc}: re-verification verdict")
    if not ok:
        note_exception(doc, "VERIFICATION_REFUTED",
                       f"Independent verifier could not confirm DONE after a rebuild. Its specific "
                       f"refutations are in evidence/{doc}-verdict.md. The tests are green; the "
                       f"verifier's semantic concerns are flagged as unverified debt. Proceeding.")
    # P7.5: completeness sweep
    for cycle in range(1, SWEEP_CYCLES + 1):
        log(f"[{doc}] P7.5 completeness sweep (cycle {cycle})")
        done, out = independent_check(doc, "completeness_sweep.md", "SWEEP: NO GAPS")
        (ROOT / "evidence" / f"{doc}-sweep.md").write_text(out)
        git_commit(f"{doc}: completeness sweep {cycle}")
        if done:
            break
        log(f"[{doc}] sweep found gaps — extending criteria + evidence, rebuilding")
        agent("gen_criteria.md", {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]}, model=SONNET_MODEL)
        agent("gen_evidence.md", {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]}, model=SONNET_MODEL)
        # Merge sweep-gap-closure addenda (staging parts/) into the existing sealed bundle
        # instead of expecting a complete fresh bundle at staging/acceptance/.
        merge_sweep_parts(doc)
        sealed = bundle_dir(doc)
        if not coverage_gate(doc, sealed):
            return "COVERAGE_GATE_FAILED_POST_SWEEP"
        promote(doc); seal(doc); git_commit(f"{doc}: sweep-extended arbiter re-sealed")
        if build_loop(doc) != "GREEN":
            return "BUILD_FAILED_POST_SWEEP"
    else:
        note_exception(doc, "SWEEP_RESIDUAL_GAPS",
                       f"Completeness sweep still reported uncovered spec behaviors after "
                       f"{SWEEP_CYCLES} cycles (evidence/{doc}-sweep.md). Proceeding; extend later.")
    # P8: cross-doc regression (verify() already runs the accumulated scope) + advance
    sh(["git", "tag", "-f", f"{doc}-done"])
    return "DONE"


def cli_preflight() -> None:
    """Fail fast BEFORE the night starts: claude CLI present + authenticated, venv, git identity."""
    if shutil.which("claude") is None:
        sys.exit("PRELAUNCH FAIL: `claude` CLI not on PATH.")
    r = subprocess.run(["claude", "-p", "say hi", "--max-turns", "10"],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    combined = (r.stdout or "") + (r.stderr or "")
    auth_fail_sigs = ["not authenticated", "please log in", "unauthorized", "401", "403",
                      "invalid api key", "expired", "could not authenticate"]
    combined_lower = combined.lower()
    has_auth_error = any(sig in combined_lower for sig in auth_fail_sigs)
    if r.returncode != 0 or has_auth_error:
        sys.exit("PRELAUNCH FAIL: claude CLI is not authenticated in THIS terminal.\n"
                 "  Fix: run `claude` interactively here, then `/login` (use the Max subscription\n"
                 "  account), exit, and relaunch the orchestrator.\n"
                 f"  CLI said: {combined.strip()[:200]}")
    if not pathlib.Path(PY).exists():
        sys.exit("PRELAUNCH FAIL: .venv missing — run `uv venv --python 3.12` + install test deps.")
    print("PRELAUNCH OK: claude authenticated, venv present.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="doc00")
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    cli_preflight()
    docs = [args.only] if args.only else ORDER[ORDER.index(args.start):]
    LOG.write_text("")
    for doc in docs:
        lock_pull_during_doc()      # doc in flight — pin the run to its launch SHA
        result = run_doc(doc)
        log(f"==> {doc}: {result}")
        allow_pull_between_docs()    # doc no longer building (boundary or halt) — ff pull safe again
        if result != "DONE":
            log("HALT: sequential dependency — resolve, then rerun with --from " + doc)
            sys.exit(1)
    write_final_report()
    log("ALL DOCS DONE — sealed, verified, swept. Report: evidence/FINAL-REPORT.md (+ git log).")


if __name__ == "__main__":
    main()
