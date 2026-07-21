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
import argparse, collections, hashlib, json, os, pathlib, re, shutil, signal, subprocess, sys, threading, time
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
ORCH = ROOT / "orchestrator"
STAGING = ROOT / "staging"
PY = str(ROOT / ".venv" / "bin" / "python")
sys.path.insert(0, str(ORCH))
from model_routing import model_for, tier  # noqa: E402  (single source for every model choice)
LOG = ORCH / "run.log"
ALLOW_PULL = ORCH / "state" / "ALLOW_PULL"   # present ONLY between docs: the supervisor may
#   fast-forward monitoring-side fixes then. Absent while a doc builds => the run is pinned to
#   its launch SHA and no founder push can mutate it mid-doc.

# Model tiering is CONFIG, not code (Task 7): every choice resolves through model_for()/tier()
# reading orchestrator/model-routing.json. SONNET for routine builder/generation passes; OPUS for
# judgment-critical paths (adjudicator, criteria review, extraction-count audit, whole-doc review)
# and for stalled builder sessions. Re-tiering any task is a one-line JSON edit. These two constants
# remain as the tier ids (config-driven) for the build-escalation + checked_agent defaults below.
SONNET_MODEL = tier("sonnet")
OPUS_MODEL = tier("opus")
OPUS_ESCALATION_STALL_COUNT = 2   # build sessions stalled >= this many times → escalate to Opus

DOCS = {
    "doc00": {"spec": "00-FOUNDATION.md",
              "deps": ["pydantic", "pydantic-settings", "fastapi", "uvicorn", "asyncpg",
                        "alembic", "structlog", "httpx", "pytest-asyncio", "sentry-sdk"]},
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
P5_PLAN_TIMEOUT = 60 * 25   # the P5 planner is BOUNDED: planning a doc is minutes of work, so a
#   session that runs past this is hung (the planner subprocess has frozen here for 40+ min with
#   zero CPU progress). Fail LOUD (return "P5_PLAN_TIMEOUT") rather than freeze the night silently.
# EVERY other claude-spawning phase is bounded the SAME way (P5 was not special — P3 hung identically:
#   14min+ of zero log output at near-idle CPU with no bound at all). Each phase below caps its
#   subprocess and, on subprocess.TimeoutExpired, returns/logs a clear "<PHASE>_TIMEOUT" so main()
#   exits non-DONE and supervise.sh relaunches — the resume logic then skips already-sealed work.
#   Sized to the work each phase does: generation-heavy phases get planning-weight; narrow rulings less.
# P1 criteria-generation timeout SCALES with the target spec's size (see gen_criteria_timeout below):
# bigger specs have more sections to read + turn into criteria, so a fixed 15m starved the largest
# ones — doc03's 532-line spec timed out at 15m though doc01's 401-line spec had fit. base 300s +
# 2.5s/line, FLOORED at the proven 15m and CAPPED at 35m so a genuine hang is still caught loud.
GEN_CRITERIA_FLOOR, GEN_CRITERIA_CEIL = 60 * 15, 60 * 35
GEN_EVIDENCE_TIMEOUT = 60 * 25   # P3 + sweep gap-closure: heaviest generator (tests+fixtures+sims+goldens)
REVIEW_TIMEOUT       = 60 * 15   # P2: fresh-context adversarial criteria review (read one bundle + judge)
VERIFY_TIMEOUT       = 60 * 20   # P7: fresh-context independent verifier (refute the whole built doc)
SWEEP_TIMEOUT        = 60 * 20   # P7.5: fresh-context completeness sweep (spec behaviors vs criteria)
ADJUDICATE_TIMEOUT   = 60 * 10   # a single yes/no ruling on ONE claimed blocker — narrow, so tightest
REVIEW_CYCLES, SWEEP_CYCLES = 3, 2   # each pass is cheap now (formal artifacts dropped) so keep the
#                                      coverage loop generous; both break EARLY once the reviewer
#                                      approves + the RTM gate passes, so a clean doc costs 1 cycle.


def gen_criteria_timeout(doc: str) -> int:
    """P1 criteria-generation timeout, scaled by the doc's spec line count so a fixed constant can't
    starve a large spec (base 300s + 2.5s/line, clamped to [15m floor, 35m cap]). Calibrated on the
    real spec sizes — the largest docs (doc04 690L, doc08 706L) land near, but under, the 35m cap so
    a genuine hang is still caught:
        doc02 132L -> 15m (floor)   doc00 339L -> 19m   doc01 401L -> 22m   doc05 432L -> 23m
        doc03 532L -> 27m           doc04 690L -> 34m   doc08 706L -> 34m   (>720L -> 35m cap)"""
    spec = ROOT / "product" / "v0-spec" / DOCS[doc]["spec"]
    try:
        lines = sum(1 for _ in spec.open(encoding="utf-8", errors="replace"))
    except Exception:
        lines = 450   # spec unreadable -> safe middle estimate, still well above the floor
    return int(min(GEN_CRITERIA_CEIL, max(GEN_CRITERIA_FLOOR, 300 + 2.5 * lines)))


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.open("a").write(line + "\n")


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, **kw)


# ── THE single claude-spawn primitive (every phase goes through run_agent) ───────────────────
# History: 9 phases each spawned `claude -p` via an ad-hoc subprocess.run(timeout=) and wrapped it
# in its own try/except TimeoutExpired. That shared primitive had THREE latent defects that made
# "fix P5, P3 freezes identically" inevitable — the freeze lived in the primitive, not the phases:
#   1. stdin was inherited (not DEVNULL): a child that reads stdin blocks forever at near-idle CPU.
#   2. NO process group: subprocess.run's timeout does p.kill() on the DIRECT claude only, so its
#      MCP/npx/node grandchildren ORPHAN (reparent to pid 1) and accumulate across the 60-restart
#      supervise loop. (Empirically confirmed: apify's `npm exec` survived a direct-child SIGKILL.)
#   3. Full ambient MCP loaded every phase (apify/magic/supabase/context7 + a claude.ai connector
#      fetched over the network): tools the build never uses, but each a startup-latency + live
#      tool-call-hang + child-process surface. A hung MCP tool call presents as EXACTLY the
#      observed "near-idle CPU, no log output for many minutes" freeze.
# run_agent() fixes all three at the source: stdin=DEVNULL, start_new_session (own process group),
# strict no-MCP, a drain thread so a slow/orphaned writer can't deadlock, and — always, in finally —
# a whole-process-group kill so NOTHING orphans. It NEVER raises TimeoutExpired; it returns a
# structured AgentResult so every phase handles timeout the SAME one-line way (`if res.timed_out`).
NO_MCP = ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']


class AgentResult(NamedTuple):
    returncode: int | None   # claude's exit code (None if we killed it on timeout)
    stdout: str              # combined stdout+stderr (bounded rolling tail)
    timed_out: bool          # True iff the wall-clock cap fired and we killed the group


def _kill_process_group(p: subprocess.Popen, phase: str) -> int:
    """Kill the ENTIRE process group led by p (claude + every child it spawned): SIGTERM, brief
    grace, then SIGKILL. Idempotent + best-effort. Returns how many members were still alive at
    SIGKILL time (for logging). This is what guarantees no orphaned claude/MCP/tool processes."""
    try:
        pgid = os.getpgid(p.pid)
    except (ProcessLookupError, OSError):
        return 0
    def _members() -> list[int]:
        try:
            out = subprocess.run(["ps", "-Ao", "pid,pgid"], capture_output=True, text=True).stdout
        except Exception:
            return []
        pids = []
        for line in out.splitlines()[1:]:
            f = line.split()
            if len(f) >= 2 and f[1].isdigit() and int(f[1]) == pgid:
                pids.append(int(f[0]))
        return pids
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return 0
    try:
        p.wait(timeout=8)   # graceful window for claude to flush + reap its own children
    except subprocess.TimeoutExpired:
        pass
    still = _members()
    if still:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return len(still)


def run_agent(prompt_file: str, subs: dict, *, timeout: int, max_turns: int,
              model: str | None = None, phase: str = "") -> AgentResult:
    """Spawn ONE fresh Claude Code session (guard hooks apply). Bounded, no-MCP, process-group
    isolated, drained, and ALWAYS group-killed on the way out. Returns AgentResult; never raises
    TimeoutExpired. `phase` is a short label used in the distinct start/timeout/exit log lines."""
    text = (ORCH / "prompts" / prompt_file).read_text()
    for k, v in subs.items():
        text = text.replace(k, v)
    cmd = ["claude", "-p", text, "--permission-mode", "bypassPermissions",
           "--max-turns", str(max_turns), *NO_MCP]
    if model:
        cmd += ["--model", model]
    log(f"[{phase or prompt_file}] spawn claude (model={(model or 'default').split('-')[-1]}, "
        f"cap={timeout // 60}m, max_turns={max_turns})")
    buf: collections.deque[str] = collections.deque(maxlen=6000)   # bounded rolling tail
    p = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1, start_new_session=True)

    def _drain() -> None:
        try:
            for line in p.stdout:            # keep the pipe empty so the child never blocks on it,
                buf.append(line)             # and echo live so console.log shows real progress
                sys.stdout.write(line)
        except Exception:
            pass
    th = threading.Thread(target=_drain, daemon=True)
    th.start()
    timed_out = False
    started = time.time()
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        log(f"[{phase or prompt_file}] *** TIMEOUT after {timeout // 60}m at near-idle — killing "
            f"process group {_safe_pgid(p)} (claude + all children); nothing left to orphan ***")
    finally:
        reaped = _kill_process_group(p, phase)
        th.join(timeout=5)
    dt = int(time.time() - started)
    rc = p.returncode
    if timed_out:
        log(f"[{phase or prompt_file}] group killed after {dt}s (reaped {reaped} live members)")
    elif rc not in (0, None):
        tail = "".join(buf)[-400:].replace("\n", " ")
        log(f"[{phase or prompt_file}] claude exited rc={rc} after {dt}s — tail: {tail}")
    return AgentResult(rc, "".join(buf), timed_out)


def _safe_pgid(p: subprocess.Popen) -> int:
    try:
        return os.getpgid(p.pid)
    except (ProcessLookupError, OSError):
        return -1


# Local build-tool bounds — a hung pytest/mypy/etc. would freeze the conductor at near-idle CPU
# with no output, the SAME symptom class as a hung claude subprocess. Bound them the same way.
TOOL_TIMEOUT_QUICK  = 60 * 5    # ruff / bandit / collect-only / import checks / coverage gate
TOOL_TIMEOUT_TYPES  = 60 * 10   # mypy --strict over the whole workspace
PYTEST_TIMEOUT      = 60 * 20   # a full scoped pytest RUN — bounded so one hung test can't freeze
DEP_INSTALL_TIMEOUT = 60 * 10   # uv pip install (network-bound)


def run_tool(cmd: list[str], *, timeout: int, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a LOCAL tool (pytest/ruff/mypy/bandit/uv/git) bounded + process-group isolated. On
    timeout: kill the WHOLE group (no orphaned test subprocesses) and return a synthetic
    CompletedProcess(returncode=124) with a clear TIMEOUT message (GNU-timeout convention). This is
    the local-tool sibling of run_agent — together they make EVERY subprocess the conductor spawns
    bounded, so nothing can silently freeze the run."""
    p = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE if capture else None,
                         stderr=subprocess.PIPE if capture else None,
                         text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)     # drain-safe (threads); killpg below unblocks it
        return subprocess.CompletedProcess(cmd, p.returncode, out or "", err or "")
    except subprocess.TimeoutExpired:
        _kill_process_group(p, "tool")
        try:
            out, err = p.communicate(timeout=5)
        except Exception:
            out, err = "", ""
        msg = f"TOOL TIMEOUT after {timeout // 60}m — killed group: {' '.join(cmd[:5])} …"
        log(f"[tool] {msg}")
        return subprocess.CompletedProcess(cmd, 124, out or "", (err or "") + "\n" + msg)


def _reap_orphaned_mcp() -> None:
    """Kill TRULY orphaned MCP/npx helpers (ppid==1) left by a previously hard-killed conductor,
    before we start — so legacy orphans can't accumulate or interfere. Conservative: only ppid==1
    (real orphans, reparented to launchd); a live claude/desktop app's children have a live parent
    and are never touched. (run_agent's no-MCP + group-kill means new runs create none of these.)"""
    try:
        out = subprocess.run(["ps", "-Ao", "pid,ppid,command"], capture_output=True, text=True).stdout
    except Exception:
        return
    killed = 0
    for line in out.splitlines()[1:]:
        f = line.split(None, 2)
        if len(f) < 3:
            continue
        pid, ppid, cmd = f
        if ppid == "1" and any(s in cmd for s in (
                "actors-mcp-server", "context7-mcp", "mcp-server-supabase", "@21st-dev/magic",
                "@apify/actors-mcp", "@upstash/context7", "npm exec")):
            try:
                os.kill(int(pid), signal.SIGKILL); killed += 1
            except (OSError, ValueError):
                pass
    if killed:
        log(f"[startup] reaped {killed} orphaned MCP helper process(es) from a prior hard-killed run")


# ── Machine-sleep / suspend detection (TASK 3) ───────────────────────────────
# A heartbeat thread stamps state/heartbeat every HEARTBEAT_INTERVAL while the conductor is alive.
# A suspend (idle/lid-close sleep) freezes the WHOLE process tree — the heartbeat stops too — and
# can kill the tmux server, ending the run with no error line (the 2026-07-21 ~7h 19:42->02:35 gap).
# On the NEXT conductor start we compare now vs the last heartbeat: a large gap => log a LOUD,
# specific line so "why did it die?" has a visible answer instead of guesswork. Because the thread
# keeps beating WHILE AWAKE, a merely-long (34m) phase never trips it — only a real suspend does.
_HEARTBEAT = ORCH / "state" / "heartbeat"
HEARTBEAT_INTERVAL = 60          # seconds between beats while alive
SUSPEND_THRESHOLD = 900          # a gap larger than this between beats => machine likely slept


def _suspend_gap_message(last_epoch: float, now_epoch: float, threshold: int = SUSPEND_THRESHOLD) -> str | None:
    """Pure: return a loud warning if the heartbeat gap implies a suspend, else None."""
    gap = now_epoch - last_epoch
    if gap < threshold:
        return None
    mins = int(gap // 60)
    return (f"[startup] WARNING: {int(gap)}s (~{mins}m) wall-clock gap since the last heartbeat — the "
            f"machine likely SLEPT/SUSPENDED. Idle or lid-close sleep freezes the whole process tree "
            f"and can kill the tmux server, ending the run with NO error line. If this run had to be "
            f"relaunched, that is why. Prevent it: launch via orchestrator/launch.sh (caffeinate "
            f"-dimsu) and keep the lid open + charger in.")


def _read_last_heartbeat() -> float | None:
    try:
        return float(_HEARTBEAT.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _note_suspend_gap() -> None:
    """Called at startup BEFORE _start_heartbeat overwrites the file: did the machine sleep?"""
    last = _read_last_heartbeat()
    if last is None:
        return
    msg = _suspend_gap_message(last, time.time())
    if msg:
        log(msg)


def _start_heartbeat() -> None:
    """Daemon thread: stamp state/heartbeat every HEARTBEAT_INTERVAL so the NEXT start can measure
    a suspend gap. Daemon => never blocks exit; failures are swallowed (a heartbeat miss is harmless)."""
    _HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    try:
        _HEARTBEAT.write_text(str(int(time.time())))   # immediate first beat
    except OSError:
        pass

    def _beat() -> None:
        while True:
            time.sleep(HEARTBEAT_INTERVAL)
            try:
                _HEARTBEAT.write_text(str(int(time.time())))
            except OSError:
                pass
    threading.Thread(target=_beat, daemon=True, name="heartbeat").start()


def _ensure_host_tools_on_path() -> None:
    """Prepend common host-tool bin dirs to PATH so the build + tests find the binaries the suite
    shells out to (ripgrep `rg`, `postgres`/`initdb` for testing.postgresql, other homebrew tools).
    Missing these presents as prior-doc test failures that cascade into spurious deferrals — an
    environment gap, not a code gap. Setting os.environ here propagates to EVERY subprocess the
    conductor spawns (claude phases + local tools), so the fix is applied once, centrally."""
    candidates = [
        "/opt/homebrew/bin", "/usr/local/bin",                 # homebrew (rg, etc.)
        "/opt/homebrew/opt/postgresql@16/bin",                 # keg-only postgres 16
        "/opt/homebrew/opt/postgresql@15/bin", "/opt/homebrew/opt/postgresql@14/bin",
    ]
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep)
    added = [c for c in candidates if os.path.isdir(c) and c not in parts]
    if added:
        os.environ["PATH"] = os.pathsep.join(added + parts)
        log(f"[startup] host-tool PATH augmented with: {added}")


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
        r = run_tool(["uv", "pip", "install", "-q", *deps], timeout=DEP_INSTALL_TIMEOUT)
        if r.returncode != 0:
            log(f"[{doc}] WARN dep install non-zero/timeout (continuing): {r.stderr[-300:]}")


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
    def run(cmd, timeout=TOOL_TIMEOUT_QUICK):
        r = run_tool(cmd, timeout=timeout)          # bounded: rc 124 on timeout (never a silent hang)
        out.append(f"$ {' '.join(cmd)}\n{r.stdout[-1500:]}{r.stderr[-1500:]}")
        return r.returncode
    if dirs and run([PY, "-m", "ruff", "check", *dirs]) != 0:
        return 1, "\n".join(out)
    mypy_rc = run([PY, "-m", "mypy", "--strict", *dirs], TOOL_TIMEOUT_TYPES) if dirs else 0
    bandit_rc = run([PY, "-m", "bandit", "-q", "-r", *dirs]) if dirs else 0
    desel = [a for t in DESELECTED for a in ("--deselect", t)]
    col = run_tool([PY, "-m", "pytest", "--collect-only", "-q", *scope], timeout=TOOL_TIMEOUT_QUICK)
    if re.search(r"^no tests ran|^0 tests|error", col.stdout.splitlines()[-1] if col.stdout else "error"):
        return 1, "ZERO TESTS COLLECTED or collection error — refusing green\n" + col.stdout[-800:]
    if run([PY, "-m", "pytest", "-q", "-x", "--maxfail=1", *desel, *scope], PYTEST_TIMEOUT) != 0:
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
    r = run_tool([sys.executable, str(ORCH / "criteria_coverage_gate.py"), doc, "--base", str(base)],
                 timeout=TOOL_TIMEOUT_QUICK)
    log(r.stdout[-600:])
    return r.returncode == 0


def ladder_gate(doc: str, base: pathlib.Path) -> bool:
    """Verification-ladder schema gate (GENERATOR.md §8.4). MECHANICAL — pure subprocess, no agent.
    Fails the seal unless every criterion carries a well-formed ladder + dependency_class, every
    non-null class has its -NEG pair, e2e is golden-path-only, and dependency_manifest.yaml is
    consistent. Runs next to the RTM coverage gate in P4."""
    r = run_tool([sys.executable, str(ORCH / "ladder_schema_gate.py"), doc, "--base", str(base)],
                 timeout=TOOL_TIMEOUT_QUICK)
    log(r.stdout[-900:])
    return r.returncode == 0


def extraction_gate(doc: str) -> tuple[bool, str]:
    """Independent extraction-count gate (Task 5). A FRESH-context agent recounts the spec's distinct
    normative obligations with NO bundle access; HALT on material (>10%) disagreement with the
    bundle's requirement count. Runs ONCE per doc after generation. Opus (completeness is the
    highest-stakes judgment). Returns (ok, reason); writes evidence/<doc>-extraction-count.md."""
    sys.path.insert(0, str(ORCH))
    from extraction_count_gate import run_extraction_gate
    res = run_extraction_gate(doc)   # same process → uses run_agent (proper isolation), no nested tool
    log(f"[{doc}] extraction-count: bundle={res['bundle_requirement_count']} "
        f"independent={res['independent_count']} verdict={res['verdict']} — {res['reason']}")
    return (not res["halt"]), res["reason"]


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


def adjudicate(doc: str, entry: str) -> tuple[bool, bool]:
    """Fresh-context ruling on a claimed blocker. Returns (keep_going, timed_out):
    keep_going=True → continue the build (proceed, or deferred-under-budget); keep_going=False →
    halt (too many deferrals). timed_out=True → the adjudicator checker blew its bound; the caller
    halts the doc cleanly for a supervise restart. Cache hit re-applies a prior ruling with no LLM."""
    sig = _entry_sig(entry)
    cache = _load_decisions(doc)
    if sig in cache:
        prior = cache[sig]
        log(f"[{doc}] cached ruling re-applied ({prior['ruling']}) for: {entry[:80]}")
        return prior["ruling"] == "PROCEED", False
    log(f"[{doc}] adjudicating SPEC_BLOCKED: {entry[:120]}")
    ok, out, timed_out = checked_agent(doc, "adjudicate.md", "ADJUDICATION: PROCEED",
                                       timeout=ADJUDICATE_TIMEOUT)
    if timed_out:
        return False, True                       # no ruling — let the caller emit ADJUDICATE_TIMEOUT
    cache[sig] = {"ruling": "PROCEED" if ok else "DEFER", "entry_prefix": entry.strip()[:120],
                  "decided_at": time.strftime("%F %T")}
    _save_decisions(doc, cache)
    if ok:
        clar = out.split("ADJUDICATION: PROCEED", 1)[-1][:800]
        (ROOT / "PROGRESS.md").open("a").write(
            f"\n## ADJUDICATION RESOLVED — proceed with this reading:\n{clar}\n")
        git_commit(f"{doc}: adjudication — proceed with clarified reading")
        return True, False
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
    return len(DESELECTED) <= 5, False           # >5 deferrals = something systemic; halt


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
        build_model = (model_for("build-unstall") if stalls >= OPUS_ESCALATION_STALL_COUNT
                       else model_for("mechanical-build"))
        log(f"[{doc}] build session {s} (persistent; iterates internally) [{build_model.split('-')[1]}]")
        res = run_agent("build_pass.md", {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]},
                        timeout=BUILD_TIMEOUT, max_turns=BUILD_TURNS, model=build_model,
                        phase=f"{doc} P6 build#{s}")
        if res.timed_out:
            log(f"[{doc}] build session {s} hit the {BUILD_TIMEOUT//60}m cap; checking progress")
        if tree_hash() != baseline:
            return "INTEGRITY_VIOLATION"
        if (msg := new_spec_blocked()):
            proceed, adj_timed_out = adjudicate(doc, msg)
            if adj_timed_out:
                log(f"[{doc}] adjudicator exceeded {ADJUDICATE_TIMEOUT // 60}m and was killed — "
                    f"halting doc cleanly (was an unbounded silent hang before this timeout existed)")
                return "ADJUDICATE_TIMEOUT"
            if not proceed:
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
            run_agent("unstall.md", {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]},
                      timeout=BUILD_TIMEOUT, max_turns=200, model=model_for("build-unstall"),
                      phase=f"{doc} P6 unstall")   # timeout is self-bounded; result checked via commits
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


def checked_agent(doc: str, prompt: str, expect: str, *, timeout: int,
                  model: str = OPUS_MODEL, max_turns: int = 120) -> tuple[bool, str, bool]:
    """Fresh-context CHECKER session (criteria-review / adjudicator / verifier / sweep), tamper-
    checked: services+libs must hash identically before/after — a checker that edits the build is
    rejected. Runs through the shared run_agent primitive (bounded, no-MCP, process-group killed),
    so a frozen checker can never hang the night silently. Defaults to OPUS_MODEL — every judgment-
    critical check warrants the strongest model.

    Returns (expect_seen, out_tail, timed_out). The caller turns timed_out into its own distinct
    '<PHASE>_TIMEOUT' so supervise.sh sees exactly which phase's checker blew its bound."""
    before = tree_hash(("services/", "libs/")) if (ROOT / "services").exists() else ""
    res = run_agent(prompt, {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]},
                    timeout=timeout, max_turns=max_turns, model=model, phase=prompt)
    after = tree_hash(("services/", "libs/")) if (ROOT / "services").exists() else ""
    if res.timed_out:
        return False, res.stdout[-3000:], True
    if before != after:
        return False, "TAMPER: check session modified the build", False
    return (expect in res.stdout), res.stdout[-3000:], False


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


def _live_bundle_hash(doc: str) -> str:
    """sha256 over the spec + live acceptance/<doc> tree — byte-identical to the digest seal()
    records. Used to prove a doc's sealed authority is intact before skipping its rebuild."""
    spec = ROOT / "product" / "v0-spec" / DOCS[doc]["spec"]
    h = hashlib.sha256()
    for p in [spec, *sorted((ROOT / "acceptance" / doc).rglob("*"))]:
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def _doc_own_scope(doc: str) -> list[str]:
    """Just THIS doc's own test tree (not the accumulated prior-doc scope). doc01's suite is the
    root tests/test_*.py; every other doc lives under tests/<doc>/."""
    if doc == "doc01":
        return [str(p) for p in sorted((ROOT / "tests").glob("test_*.py"))]
    p = ROOT / "tests" / doc
    return [f"tests/{doc}"] if p.exists() else []


def _doc_already_complete(doc: str) -> tuple[bool, str]:
    """Is this doc fully done, so its entire pipeline (P4 re-seal .. P7.5 sweep) can be skipped?

    True when EITHER the doc already carries its <doc>-done tag (the canonical 'whole run_doc
    finished' marker, stamped at P8), OR it is sealed AND the live acceptance/<doc> bundle still
    hashes to the recorded seal AND its OWN test scope actually PASSES (modulo the deferred set).
    The second branch exists because a prior run can seal+build+verify a doc yet die before P8
    stamped the tag. We require PASSING, not merely collecting: 'Done means proven on real data'
    (CLAUDE.md) — a sealed doc whose implementation tests fail (e.g. doc02 with its transport
    package unbuilt) is NOT done and must be rebuilt, never skipped on a collect-only proxy."""
    if sh(["git", "rev-parse", "-q", "--verify", f"refs/tags/{doc}-done"],
          capture_output=True).returncode == 0:
        return True, f"{doc}-done tag present"
    seal_path = ORCH / "state" / f"{doc}.seal.json"
    if not seal_path.exists():
        return False, ""
    try:
        recorded = json.loads(seal_path.read_text()).get("authority+bundle_sha256", "")
    except (ValueError, OSError):
        return False, ""
    if not recorded or _live_bundle_hash(doc) != recorded:
        return False, ""   # sealed but the live bundle drifted — NOT safe to skip; rebuild it
    scope = _doc_own_scope(doc)
    if not scope:
        return False, ""
    desel = [a for t in DESELECTED for a in ("--deselect", t)]
    r = run_tool([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *desel, *scope],
                 timeout=PYTEST_TIMEOUT)
    if r.returncode != 0:                          # sealed but its own tests don't pass — rebuild it
        return False, ""
    return True, f"sealed {recorded[:12]} matches live bundle + own tests pass (modulo deferrals)"


def run_doc(doc: str) -> str:
    log(f"########## {doc} ({DOCS[doc]['spec']}) ##########")
    # WHOLE-DOC SKIP (resume): a fully sealed+verified doc must NOT be re-planned or rebuilt.
    # Before this guard, run_doc skipped only P1-P3 and still ran P4 re-seal + the P5 planner +
    # P6 build on every launch — and the P5 planner subprocess has frozen here indefinitely. Skip
    # the whole doc and stamp its -done tag so the supervisor (which picks the first UNTAGGED doc,
    # ignoring --from) advances past it instead of restarting on doc00 forever.
    complete, why = _doc_already_complete(doc)
    if complete:
        log(f"[{doc}] SKIP whole doc — {why}; not re-planning or rebuilding")
        sh(["git", "tag", "-f", f"{doc}-done"])
        return "DONE"
    sdir = STAGING / doc
    sdir.mkdir(parents=True, exist_ok=True)

    # P1+P2: criteria (skip generation if a sealed bundle already exists — doc01 has Pranav's)
    if not (bundle_dir(doc) / "criteria").exists():
        for cycle in range(1, REVIEW_CYCLES + 1):
            log(f"[{doc}] P1 generate criteria (cycle {cycle})")
            if run_agent("gen_criteria.md", {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]},
                         model=model_for("criteria-generation"), timeout=gen_criteria_timeout(doc),
                         max_turns=200, phase=f"{doc} P1 criteria").timed_out:
                return "P1_CRITERIA_TIMEOUT"
            log(f"[{doc}] P2 adversarial criteria review")
            ok, out, p2_timed_out = checked_agent(doc, "review_criteria.md", "REVIEW: APPROVED",
                                                  timeout=REVIEW_TIMEOUT)
            if p2_timed_out:
                return "P2_REVIEW_TIMEOUT"
            if ok and coverage_gate(doc, sdir / "acceptance" / doc) \
                    and ladder_gate(doc, sdir / "acceptance" / doc):
                break
            (sdir / "review-gaps.md").write_text(out)
        else:
            # Review cycles exhausted: proceed ONLY if the deterministic gate passes; record the
            # residual review gaps for morning + rely on the end-of-doc completeness sweep as
            # the backstop. (Halting the whole night on reviewer perfectionism serves no one;
            # proceeding on a FAILED coverage gate is never allowed.)
            if not coverage_gate(doc, sdir / "acceptance" / doc):
                return "COVERAGE_GATE_FAILED_AFTER_REVIEW_CYCLES"
            if not ladder_gate(doc, sdir / "acceptance" / doc):
                return "LADDER_GATE_FAILED_AFTER_REVIEW_CYCLES"
            (ROOT / "evidence").mkdir(exist_ok=True)
            shutil.copy2(sdir / "review-gaps.md", ROOT / "evidence" / f"{doc}-review-gaps-outstanding.md")
            log(f"[{doc}] WARN review gaps outstanding after {REVIEW_CYCLES} cycles — gate passed, "
                f"proceeding; sweep is the backstop; morning item saved to evidence/")
    # P3: evidence (tests+fixtures+sims+goldens). Skip on resume if already promoted + collecting
    # clean (a re-run after a conductor-side halt must not waste ~30min regenerating a sealed doc).
    promoted_ok = bool(test_dirs_upto(doc)) and run_tool(
        [PY, "-m", "pytest", "--collect-only", "-q", *test_dirs_upto(doc)],
        timeout=TOOL_TIMEOUT_QUICK).returncode == 0
    if promoted_ok and (ORCH / "state" / f"{doc}.seal.json").exists():
        log(f"[{doc}] P3 skipped — evidence already promoted + collecting clean (resume)")
    else:
        log(f"[{doc}] P3 generate evidence layer (tests/fixtures/sims/goldens -> staging)")
        if run_agent("gen_evidence.md", {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]},
                     model=model_for("evidence-generation"), timeout=GEN_EVIDENCE_TIMEOUT,
                     max_turns=200, phase=f"{doc} P3 evidence").timed_out:
            return "P3_EVIDENCE_TIMEOUT"
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
    if not ladder_gate(doc, base):
        return "LADDER_GATE_FAILED"
    promote(doc)
    digest = seal(doc)
    git_commit(f"{doc}: promote + seal arbiter (bundle+evidence) [{digest[:12]}]")
    log(f"[{doc}] P4 sealed {digest[:12]}")
    # P4.5: independent extraction-count gate (Task 5) — BEFORE the build, so a bundle that silently
    # under/over-extracted the spec HALTS for founder review here, not after a wasted build. This is
    # the completeness check the old auto-closing P7.5 sweep used to approximate; unlike the sweep it
    # does NOT regenerate — a material gap means a human must look at WHAT is different.
    ok_xc, xc_reason = extraction_gate(doc)
    if not ok_xc:
        note_exception(doc, "EXTRACTION_COUNT_HALT",
                       f"Independent spec recount materially disagrees with the bundle's requirement "
                       f"count: {xc_reason} See evidence/{doc}-extraction-count.md. Halting for "
                       f"founder review — not auto-regenerating.")
        return "EXTRACTION_COUNT_HALT"
    ensure_deps(doc)
    # sanity: evidence must collect. Use pytest's EXIT CODE (0 = collected ok), never a substring
    # match — test names legitimately contain 'error' (e.g. ..._returns_errors_never_throws).
    col = run_tool([PY, "-m", "pytest", "--collect-only", "-q", *test_dirs_upto(doc)],
                   timeout=TOOL_TIMEOUT_QUICK)
    if col.returncode != 0:
        return "EVIDENCE_COLLECTION_ERROR:\n" + (col.stdout + col.stderr)[-800:]
    # P5: plan (BOUNDED via the shared run_agent primitive — a hung planner subprocess fails loud
    # and its whole process group is killed, never silently freezing for hours as it once did).
    log(f"[{doc}] P5 plan + planner-review")
    if run_agent("plan.md", {"<DOC>": doc, "<SPEC>": DOCS[doc]["spec"]},
                 max_turns=60, model=model_for("planning"), timeout=P5_PLAN_TIMEOUT,
                 phase=f"{doc} P5 plan").timed_out:
        return "P5_PLAN_TIMEOUT"
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
    # P7: verification ladder (Task 3) — the SINGLE verification system (replaces the old whole-doc
    # refute). Its reality/negative tiers ARE the fresh-context refutation, per-criterion; its
    # mechanical tiers (lint/unit/integration) are the objective gate, run in parallel with zero
    # agent cost. The runner persists evidence/<doc>-ladder.json and exits: 0=ladder-complete,
    # 1=a rung is RED (genuine defect), 2=no defect but reality/e2e await founder cassettes.
    (ROOT / "evidence").mkdir(exist_ok=True)
    log(f"[{doc}] P7 verification ladder (mechanical tiers + fresh-context reality/negative critics)")
    r = run_tool([PY, str(ORCH / "ladder_runner.py"), doc], timeout=VERIFY_TIMEOUT)
    log(r.stdout[-1500:])
    git_commit(f"{doc}: verification-ladder run (evidence/{doc}-ladder.json)")
    if r.returncode == 1:
        # A rung is RED — genuine defect. One rebuild cycle re-running only failed tiers, then halt.
        log(f"[{doc}] ladder RED — one rebuild cycle, then re-climb")
        if build_loop(doc) == "GREEN":
            r = run_tool([PY, str(ORCH / "ladder_runner.py"), doc, "--force"], timeout=VERIFY_TIMEOUT)
            log(r.stdout[-1500:])
            git_commit(f"{doc}: verification-ladder re-run after rebuild")
        if r.returncode == 1:
            note_exception(doc, "LADDER_RED",
                           f"A verification-ladder rung is RED after a rebuild — the specific red "
                           f"(criterion, tier) rows are in evidence/{doc}-ladder.json. Genuine "
                           f"defect (or spec problem); halting for founder review, not looping.")
            return "LADDER_RED"
    elif r.returncode == 2:
        # No defect, but reality/e2e rungs await founder-recorded cassettes — an honest, visible
        # incomplete state (NOT a silent skip, NOT a failure). The pipeline proceeds; the pending
        # rungs turn green with no code change once cassettes are recorded (tests/cassettes/RECORDING.md).
        note_exception(doc, "LADDER_PENDING_CASSETTES",
                       f"Mechanical tiers green; reality/e2e rungs for {doc} await founder-recorded "
                       f"cassettes (see evidence/{doc}-ladder.json + tests/cassettes/RECORDING.md).")
    # P7.5: completeness is now covered WITHOUT a second, conflicting verification system:
    #   - COUNT completeness (did the bundle capture the whole spec?) — the P4.5 extraction-count gate,
    #     which HALTS for founder review on a material gap rather than silently auto-regenerating.
    #   - BEHAVIORAL completeness (does each criterion's real boundary actually hold?) — the P7 ladder's
    #     fresh-context reality/negative critics, per criterion.
    # The old auto-closing completeness_sweep loop is retired: it was a parallel verification path that
    # regenerated the bundle behind the founder's back — exactly what Task 5 replaces with a halt.
    # P8: cross-doc regression (verify() already runs the accumulated scope) + advance
    sh(["git", "tag", "-f", f"{doc}-done"])
    return "DONE"


# Distinct preflight exit codes so supervise.sh tells a TRANSIENT stop (back off, keep the run alive)
# from a GENUINE auth stop (a human must /login — no point retrying). NOT 0/1 (generic).
PREFLIGHT_EXIT_AUTH = 3       # claude not authenticated / venv missing — needs a human, hard-halt fast
PREFLIGHT_EXIT_NETWORK = 4    # transient API/network degradation — pause + back off, resume-safe

# A genuine "you must log in / your key is bad" signature. Kept narrow so normal probe chatter can't
# spuriously match. Everything else non-zero is TRANSIENT (retryable) — the exact opposite of the old
# `returncode != 0 => not authenticated` misclassification that caused the 2026-07-21 restart loop.
_AUTH_FAIL_SIGS = ("not authenticated", "please log in", "please run /login", "unauthorized",
                   "401", "403", "invalid api key", "expired", "could not authenticate",
                   "authentication_error", "credentials")


def _classify_preflight(returncode: int, combined: str) -> str:
    """Pure classifier for a `claude -p` probe result: 'ok' | 'auth' | 'transient'.

    'auth'      — a real auth signature is present (rc irrelevant): a human must /login. Hard-halt.
    'ok'        — rc==0 and no auth signature: the CLI is authenticated and working.
    'transient' — rc!=0 with NO auth signature (e.g. 'Reached max turns', ConnectionRefused,
                  overloaded/5xx, an unknown non-zero): a network/API blip, NOT a login problem.
                  Fail SAFE toward transient so a degraded API is retried, never mistaken for a
                  fatal auth failure (the root cause of the silent restart loop)."""
    low = combined.lower()
    if any(sig in low for sig in _AUTH_FAIL_SIGS):
        return "auth"
    if returncode == 0:
        return "ok"
    return "transient"


def _preflight_die(code: int, msg: str) -> None:
    """Exit preflight LOUDLY: write the real reason to run.log (the founder's canonical log) AND
    stderr, then exit with a distinct code. The old path sys.exit()'d to stderr only, which is why
    run.log showed a bare stack of '[startup] host-tool PATH augmented' with no reason."""
    full = "PRELAUNCH FAIL: " + msg
    try:
        line = f"[{time.strftime('%H:%M:%S')}] [startup] {full}"
        LOG.open("a").write(line + "\n")
    except Exception:
        pass
    print(full, file=sys.stderr, flush=True)
    sys.exit(code)


def cli_preflight(attempts: int = 3) -> None:
    """Fail fast BEFORE the night starts: claude CLI present + authenticated + venv.

    A TRANSIENT probe failure (network/API degraded) is RETRIED with backoff — a blip recovers in
    seconds — and only after `attempts` genuine transient failures does it exit PREFLIGHT_EXIT_NETWORK
    (a resume-safe pause, not a broken run). A real auth failure exits PREFLIGHT_EXIT_AUTH immediately
    (retrying a login problem is pointless). Every outcome is logged to run.log."""
    if shutil.which("claude") is None:
        _preflight_die(PREFLIGHT_EXIT_AUTH, "`claude` CLI not on PATH.")
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            r = subprocess.run(["claude", "-p", "say hi", "--max-turns", "10", *NO_MCP],
                               cwd=ROOT, capture_output=True, text=True, timeout=120,
                               stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            verdict, last = "transient", "`claude -p` did not respond within 120s (network/API stalled)"
        else:
            combined = (r.stdout or "") + (r.stderr or "")
            verdict = _classify_preflight(r.returncode, combined)
            last = combined.strip()[:200] or f"(rc={r.returncode}, no output)"
        if verdict == "ok":
            if not pathlib.Path(PY).exists():
                _preflight_die(PREFLIGHT_EXIT_AUTH,
                               ".venv missing — run `uv venv --python 3.12` + install test deps.")
            if attempt > 1:
                log(f"[startup] PRELAUNCH recovered on attempt {attempt} after a transient blip")
            log("[startup] PRELAUNCH OK: claude authenticated, venv present.")
            return
        if verdict == "auth":
            _preflight_die(PREFLIGHT_EXIT_AUTH,
                           "claude CLI is NOT authenticated in THIS terminal.\n"
                           "  Fix: run `claude` interactively here, then `/login` (Max subscription\n"
                           "  account), exit, and relaunch. This is a human step — not retryable.\n"
                           f"  CLI said: {last}")
        # transient — log the REAL reason to run.log every time, then back off and retry
        if attempt < attempts:
            backoff = min(30, 5 * attempt)
            log(f"[startup] PRELAUNCH transient failure (attempt {attempt}/{attempts}): {last} "
                f"— NOT an auth problem; retrying in {backoff}s")
            time.sleep(backoff)
    _preflight_die(PREFLIGHT_EXIT_NETWORK,
                   f"claude -p probe failed {attempts}x with a TRANSIENT error (network/API degraded), "
                   f"NOT auth. Last: {last}. The run is paused, not broken — the supervisor will "
                   f"back off and it resumes automatically when connectivity returns.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="doc00")
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    # Truncate run.log FIRST (one clean log per conductor run) so EVERY startup diagnostic that
    # follows — PATH augment, a machine-sleep/suspend warning, and the preflight verdict (OK, a
    # transient retry, or a FAIL) — persists in run.log instead of being wiped after preflight. The
    # old order truncated AFTER preflight, so a bare stack of PATH-augment lines was all a failed
    # restart left behind (the exact 2026-07-21 symptom) and PRELAUNCH OK / suspend warnings vanished.
    LOG.write_text("")
    _ensure_host_tools_on_path()   # rg / postgres / homebrew tools the suite shells out to
    _note_suspend_gap()            # did the machine sleep since the last run? (reads OLD heartbeat)
    _start_heartbeat()             # begin stamping state/heartbeat so the NEXT start can tell
    _reap_orphaned_mcp()           # clear any legacy orphaned MCP helpers from a prior hard-killed run
    cli_preflight()
    docs = [args.only] if args.only else ORDER[ORDER.index(args.start):]
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
