"""Doc 05 §3.6.2 — the resumed-session SUBTASK EXECUTOR: sequential build, checkpoint →
git read-back → publish-or-fail → crash-resume-skips-finished.

The node ``workroom.sequential-build`` (``services/workroom/src/workroom/big_build.py`` —
:class:`BigBuildExecutor`). After the plan turn (``workroom.plan-step``) produces the
persisted AC-tagged multi-file plan, THIS executor runs the units:

  * **SEQUENTIALLY in ONE resumed session** (§3.6.2) — each unit is a fresh ``query()`` on
    the persisted SDK ``session_id`` with a tight ``max_turns`` + an explicit "do THIS
    subtask, then STOP. Do NOT start the next." (V0 core is sequential — no fan-out/worktree
    in the core path, §12.4; that dissolves the concurrent-shared-session race.)
  * per unit: **produce the artifact + checkpoint it** (a ``git commit`` in the sandbox),
    then **READ THE CHECKPOINT BACK from git** — capture HEAD before the turn, read
    ``head_before..HEAD`` after for the commits it ACTUALLY created (never mark done off the
    model's narration), then **publish-or-fail** — publish the committed tree to the staging
    destination; if publish THROWS, the subtask FAILS, it never reports success.
  * **a checkpoint per unit** persisted into the SAME ``operation_runs`` row's ``progress``
    so a mid-crash resume SKIPS the finished units (never redoes them).

These tests drive the REAL host path with in-process fakes:
  * a fake worker provider that, per resumed turn, invokes an injected commit-effect against
    a **REAL temp git repo** (so the read-back is proven against real ``git rev-parse`` /
    ``git rev-list`` — never a mock of git);
  * a publish seam that can be made to THROW on a chosen unit (publish-failure-fails);
  * the ``operation_runs`` row store from the sibling plan tests (checkpoints persist there);
  * a crash injected mid-sequence, then a fresh executor resumes from the durable checkpoints.

e2b is NOT installed; the E2B template bake (the Node sidecar) is the flagged Phase-3
residual, never faked. NOT done if a subtask silent-greens on a publish failure, if a unit
is marked done off model narration (no read-back commit), or if a resume redoes finished units.
"""
from __future__ import annotations

import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from contracts import AgentChunk, Bundle

from .test_big_build import FakeChat, FakeStore, _PLAN_UNITS


# ── a REAL temp git repo the read-back path runs against (never a git mock) ───


def _git(repo: Path, *args: str) -> str:
    """Run a git command in ``repo`` and return stripped stdout (raises on non-zero)."""
    out = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _init_repo(path: Path) -> Path:
    """Init a real git repo with one seed commit (so HEAD exists and is not unborn)."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@proxy.local")
    _git(path, "config", "user.name", "Proxy Test")
    (path / "README.md").write_text("seed\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")
    return path


class RealGitSandbox:
    """The git-backed sandbox seam the executor reads back through — a REAL git repo.

    This is the in-process stand-in for the E2B sandbox's git surface (the executor drives
    ``read_head`` / ``commit`` / ``list_commits`` / ``publish`` against it). Backing it with a
    REAL git repo means the read-back is proven against real ``git rev-parse`` / ``git
    rev-list`` — the exact source-of-truth path the DoD names ("read back from git").

    ``publish`` records the published commit ranges; ``fail_publish_on`` makes ``publish``
    THROW when a given unit id is published, to prove publish-failure-fails-the-subtask.
    """

    def __init__(self, repo: Path, *, fail_publish_on: str | None = None) -> None:
        self._repo = repo
        self.published: list[dict[str, Any]] = []
        self.fail_publish_on = fail_publish_on

    async def read_head(self) -> str | None:
        """Capture HEAD before a subtask turn (``None`` on an unborn repo)."""
        try:
            return _git(self._repo, "rev-parse", "HEAD")
        except subprocess.CalledProcessError:
            return None  # unborn repo (no commits yet)

    async def commit(self, *, unit_id: str, message: str, files: dict[str, str]) -> None:
        """The write-effect a worker turn performs in the sandbox: write files + git commit.

        (In production the model calls write_file/run_command('git commit') via the sandbox
        transport; here the fake worker provider invokes this so the effect lands in the REAL
        repo and the read-back below sees a real commit.)"""
        for rel, content in files.items():
            fp = self._repo / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)
        _git(self._repo, "add", "-A")
        _git(self._repo, "commit", "-q", "-m", message)

    async def list_commits(self, rev_range: str) -> list[dict[str, str]]:
        """Read ``head_before..HEAD`` back from git — the source of truth, not narration."""
        out = _git(self._repo, "rev-list", "--format=%H %s", "--no-commit-header", rev_range)
        commits: list[dict[str, str]] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            sha, _, subject = line.partition(" ")
            commits.append({"sha": sha, "subject": subject})
        return commits

    async def publish(self, *, unit_id: str, commits: list[dict[str, str]], destination: str) -> None:
        """Publish the committed tree to the staging destination — THROWS on a publish fault.

        Mirrors ``captureAndPublishCommits`` throwing on publish failure precisely so a
        subtask can't pass silently: if this raises, the executor MUST fail the subtask."""
        if self.fail_publish_on is not None and unit_id == self.fail_publish_on:
            raise RuntimeError(f"staging publish failed for {unit_id} (network/permission fault)")
        self.published.append({"unit_id": unit_id, "commits": list(commits), "destination": destination})


# ── the fake worker provider: one resumed query() per subtask ─────────────────


class FakeWorkerProvider:
    """A recording worker ``agentkit.Provider`` — ONE resumed ``query()`` per subtask (§3.6.2).

    Each ``stream()`` call is one resumed turn: it records the ``resume`` id + ``max_turns``
    + prompt it saw (so a test proves the turns resume the SAME session with a tight budget +
    a STOP instruction), performs the unit's commit effect against the REAL git repo (via the
    injected ``sandbox.commit``), and streams INIT → a TOOL_USE (the write) → RESULT.

    ``crash_after`` (a unit id) makes the provider RAISE mid-sequence AFTER that unit's turn
    starts but before it commits — modeling a process crash; a fresh executor then resumes.
    """

    name = "claude"

    def __init__(self, *, sandbox: RealGitSandbox, session_id: str, crash_on: str | None = None) -> None:
        self._sandbox = sandbox
        self._session_id = session_id
        self._crash_on = crash_on
        self.calls = 0
        self.seen_resume: list[str | None] = []
        self.seen_max_turns: list[int] = []
        self.seen_prompts: list[str] = []
        self.seen_allowed_tools: list[tuple[str, ...]] = []
        self.units_committed: list[str] = []

    def matches(self, model: str) -> bool:  # pragma: no cover - seam parity
        return True

    def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
        self.calls += 1
        self.seen_resume.append(getattr(query, "resume", None))
        self.seen_max_turns.append(int(getattr(query, "max_turns", 0) or 0))
        self.seen_prompts.append(prompt)
        self.seen_allowed_tools.append(tuple(getattr(query, "allowed_tools", ()) or ()))
        # The unit id + a file to write are threaded through the prompt-context the executor
        # renders (a real worker would infer them from the plan unit in the resumed context;
        # the fake reads them from the structured marker the executor includes).
        unit_id = _unit_id_from_prompt(prompt)
        sandbox = self._sandbox
        crash = self._crash_on == unit_id
        sid = self._session_id

        async def gen() -> AsyncIterator[AgentChunk]:
            yield AgentChunk(type="INIT", metadata={"session_id": sid})
            if crash:
                # A process crash mid-turn: the effect never lands, the stream dies.
                raise RuntimeError(f"sandbox died mid-build on {unit_id}")
            rel = f"unit_{unit_id}.py"
            await sandbox.commit(
                unit_id=unit_id,
                message=f"{unit_id}: build unit",
                files={rel: f"# built by {unit_id}\n"},
            )
            self.units_committed.append(unit_id)
            yield AgentChunk(
                type="TOOL_USE",
                metadata={"name": "mcp__code__write_file", "input": {"path": rel}},
            )
            yield AgentChunk(
                type="RESULT",
                metadata={"session_id": sid, "num_turns": 1, "total_cost_usd": 0.02},
            )

        return gen()


def _unit_id_from_prompt(prompt: str) -> str:
    """Extract the unit id the executor threaded into the subtask prompt (the STOP marker)."""
    for line in prompt.splitlines():
        if line.startswith("SUBTASK_ID:"):
            return line.split(":", 1)[1].strip()
    return "?"


# ── helpers ───────────────────────────────────────────────────────────────────


def _bundle(ask: str) -> Bundle:
    return Bundle(
        ask=ask,
        speaker="Sam",
        timestamp=datetime.now(timezone.utc),
        notes_ref=uuid.uuid4(),
        transcript_tail="…prior turns…",
        task_id=uuid.uuid4(),
    )


def _seed_plan(store: FakeStore, run_id: str, session_id: str = "plan-sess-1") -> None:
    """Persist a 4-unit plan + the session id into the operation_runs row (as plan-step would)."""
    store.claim(run_id=run_id, operation_type=f"workroom:{run_id}", progress={"ask": "build limiter"})
    from workroom.big_build import Plan, PlanUnit

    units = [PlanUnit.from_raw(u, fallback_order=i + 1) for i, u in enumerate(_PLAN_UNITS)]
    plan = Plan(units=units, session_id=session_id, ask="build limiter")
    store.rows[run_id]["progress"]["plan"] = plan.to_persisted()
    store.rows[run_id]["progress"]["session_id"] = session_id


def _make_executor(store: FakeStore, chat: FakeChat, provider: Any, sandbox: RealGitSandbox, **kw: Any) -> Any:
    from workroom.big_build import BigBuildExecutor

    return BigBuildExecutor(provider=provider, store=store, chat=chat, sandbox=sandbox, **kw)


# ── the tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subtasks_execute_sequentially_in_one_resumed_session(tmp_path: Path) -> None:
    """The units run SEQUENTIALLY, ONE at a time, each a resumed query() on the SAME persisted
    SDK session id with a tight max_turns + a STOP instruction (§3.6.2). V0 core is sequential
    — no fan-out/worktree path. Each unit is committed to the REAL repo in plan order."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id, session_id="plan-sess-42")
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider = FakeWorkerProvider(sandbox=sandbox, session_id="plan-sess-42")
    executor = _make_executor(store, chat, provider, sandbox)

    result = await executor.run(_bundle("Build the per-user rate-limiter"), run_id=run_id)

    # One resumed query() per unit, in plan order (sequential — not concurrent).
    assert provider.calls == len(_PLAN_UNITS)
    assert provider.units_committed == [u["id"] for u in _PLAN_UNITS], "units ran sequentially in order"
    # Every turn resumed the SAME persisted session id (one continuous conversation, §3.6.2).
    assert all(sid == "plan-sess-42" for sid in provider.seen_resume), "each turn resumes the plan session"
    # A tight max_turns (never the SDK default 1000) so a subtask can't run away (§3.11).
    assert all(0 < mt <= 8 for mt in provider.seen_max_turns), "each subtask has a tight max_turns"
    # The prompt carries an explicit STOP so the model does ONE subtask, not the whole plan.
    assert all("STOP" in p and "Do NOT start the next" in p for p in provider.seen_prompts)
    # Every unit succeeded → the build result reports all units done.
    assert result.status in ("done", "completed"), result
    assert result.units_done == [u["id"] for u in _PLAN_UNITS]


@pytest.mark.asyncio
async def test_done_is_read_back_from_git_never_off_model_narration(tmp_path: Path) -> None:
    """A unit is marked done ONLY off the READ-BACK git commits (head_before..HEAD), NEVER off
    the model's narration (§3.6.2). This asserts the executor captured real commit SHAs from
    the REAL repo per unit — the exact narration-based-done failure mode being designed out."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id)
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider = FakeWorkerProvider(sandbox=sandbox, session_id="plan-sess-1")
    executor = _make_executor(store, chat, provider, sandbox)

    result = await executor.run(_bundle("Build it"), run_id=run_id)

    # Each unit's checkpoint carries the REAL commit SHA read back from git (40-hex), and the
    # subject the worker actually committed — proving done came from git, not prose.
    checkpoints = result.checkpoints
    assert len(checkpoints) == len(_PLAN_UNITS)
    for unit, cp in zip(_PLAN_UNITS, checkpoints):
        assert cp["unit_id"] == unit["id"]
        assert cp["commits"], f"{unit['id']} must have read back at least one real commit"
        for c in cp["commits"]:
            assert len(c["sha"]) == 40 and all(ch in "0123456789abcdef" for ch in c["sha"])
            assert unit["id"] in c["subject"]
    # The SHAs are the REAL repo history (cross-check against git log directly).
    real_log = _git(repo, "rev-list", "HEAD").splitlines()
    read_back = [c["sha"] for cp in checkpoints for c in cp["commits"]]
    assert set(read_back) <= set(real_log), "every read-back SHA is a real commit in the repo"


@pytest.mark.asyncio
async def test_a_unit_with_no_readback_commit_fails_never_silent_green(tmp_path: Path) -> None:
    """If a resumed turn produces NO commit (head_before == HEAD — the model narrated work it
    never checkpointed), the subtask FAILS; it is never marked done off narration (§3.6.2)."""

    class NoCommitProvider(FakeWorkerProvider):
        def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
            self.calls += 1
            self.seen_resume.append(getattr(query, "resume", None))
            self.seen_max_turns.append(int(getattr(query, "max_turns", 0) or 0))
            self.seen_prompts.append(prompt)
            sid = self._session_id

            async def gen() -> AsyncIterator[AgentChunk]:
                yield AgentChunk(type="INIT", metadata={"session_id": sid})
                # The model NARRATES success but never commits (no git effect).
                yield AgentChunk(type="TEXT", text="Done! I built the unit and it works.", metadata={})
                yield AgentChunk(type="RESULT", metadata={"session_id": sid, "total_cost_usd": 0.01})

            return gen()

    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id)
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider = NoCommitProvider(sandbox=sandbox, session_id="plan-sess-1")
    executor = _make_executor(store, chat, provider, sandbox)

    result = await executor.run(_bundle("Build it"), run_id=run_id)

    # The build FAILED on the first unit (no read-back commit) — never a false green.
    assert result.status == "failed"
    assert result.units_done == [], "no unit is marked done without a read-back commit"
    assert result.failed_unit == _PLAN_UNITS[0]["id"]
    assert "no commit" in (result.reason or "").lower() or "read-back" in (result.reason or "").lower()
    # It stopped at the first failing unit — did not blindly march on.
    assert provider.calls == 1
    # Nothing was published for a unit that never checkpointed.
    assert sandbox.published == []


@pytest.mark.asyncio
async def test_publish_failure_fails_the_subtask_never_silent_green(tmp_path: Path) -> None:
    """A publish failure FAILS the subtask — it never reports success (§3.6.2). If publish
    throws, the subtask (and the build) fails, even though the commit landed in git. This is
    the exact silent-green failure mode being designed out."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id)
    repo = _init_repo(tmp_path / "clone")
    # publish THROWS on U2 (the commit still lands, but publish fails).
    sandbox = RealGitSandbox(repo, fail_publish_on="U2")
    provider = FakeWorkerProvider(sandbox=sandbox, session_id="plan-sess-1")
    executor = _make_executor(store, chat, provider, sandbox)

    result = await executor.run(_bundle("Build it"), run_id=run_id)

    # The build FAILED — a publish fault is never swallowed into a green.
    assert result.status == "failed", "a publish failure must fail the subtask, never silent-green"
    assert result.failed_unit == "U2"
    assert "publish" in (result.reason or "").lower()
    # U1 published fine (done); U2's publish threw (NOT in published, NOT in units_done).
    assert [p["unit_id"] for p in sandbox.published] == ["U1"]
    assert result.units_done == ["U1"], "only U1 (published) is done; U2 is not silent-green"
    # It did not march past the failed unit into U3/U4.
    assert "U3" not in result.units_done and "U4" not in result.units_done


@pytest.mark.asyncio
async def test_a_publish_failure_is_persisted_not_lost(tmp_path: Path) -> None:
    """The publish failure is recorded into the durable operation_runs row — the failure is
    spoken plainly + reproducible, never silently dropped (Law 2 / §3.1)."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id)
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo, fail_publish_on="U1")
    provider = FakeWorkerProvider(sandbox=sandbox, session_id="plan-sess-1")
    executor = _make_executor(store, chat, provider, sandbox)

    await executor.run(_bundle("Build it"), run_id=run_id)

    progress = await store.get_progress(run_id=run_id)
    build = progress.get("build") or {}
    assert build.get("status") == "failed"
    assert build.get("failed_unit") == "U1"
    assert store.tables_touched == {"operation_runs"}, "no bespoke table — the row IS the task"


@pytest.mark.asyncio
async def test_mid_crash_resume_skips_finished_units_never_redoes(tmp_path: Path) -> None:
    """A mid-build crash resumes WITHOUT redoing finished units (§3.6.2 / §3.13-step-8).

    Run 1 crashes on U3 (U1, U2 finished + checkpointed). Run 2 (a fresh executor reading the
    DURABLE checkpoints) resumes at U3 — it NEVER re-runs U1/U2. This is the exact
    'resumes without redoing finished units' the DoD names."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id, session_id="sess-crash")
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)

    # Run 1: crash mid-turn on U3 (U1, U2 finish + checkpoint durably).
    provider1 = FakeWorkerProvider(sandbox=sandbox, session_id="sess-crash", crash_on="U3")
    executor1 = _make_executor(store, chat, provider1, sandbox)
    result1 = await executor1.run(_bundle("Build it"), run_id=run_id)

    assert result1.status == "failed"
    assert provider1.units_committed == ["U1", "U2"], "U1, U2 committed before the U3 crash"
    # The finished units are durably checkpointed in the operation_runs row.
    progress = await store.get_progress(run_id=run_id)
    done_after_crash = [cp["unit_id"] for cp in (progress.get("build") or {}).get("checkpoints", [])]
    assert done_after_crash == ["U1", "U2"], "U1, U2 checkpointed durably; U3 did not finish"

    # Run 2: a FRESH executor resumes from the durable checkpoints — must skip U1, U2.
    provider2 = FakeWorkerProvider(sandbox=sandbox, session_id="sess-crash")
    executor2 = _make_executor(store, chat, provider2, sandbox)
    result2 = await executor2.run(_bundle("Build it"), run_id=run_id)

    # Resume ran ONLY the unfinished units (U3, U4) — U1/U2 were NOT redone.
    assert provider2.units_committed == ["U3", "U4"], "resume skipped the finished U1, U2"
    assert "U1" not in provider2.units_committed and "U2" not in provider2.units_committed
    # The whole build is now done across the two runs (all 4 units checkpointed).
    assert result2.status in ("done", "completed")
    final = await store.get_progress(run_id=run_id)
    all_done = [cp["unit_id"] for cp in (final.get("build") or {}).get("checkpoints", [])]
    assert all_done == ["U1", "U2", "U3", "U4"], "all units done exactly once across the crash+resume"


@pytest.mark.asyncio
async def test_resume_of_a_fully_finished_build_is_a_noop(tmp_path: Path) -> None:
    """Resuming an already-complete build does nothing — every unit is checkpointed, so a
    restart re-runs zero units (idempotent; never redoes finished work)."""
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id)
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider1 = FakeWorkerProvider(sandbox=sandbox, session_id="plan-sess-1")
    await _make_executor(store, chat, provider1, sandbox).run(_bundle("Build it"), run_id=run_id)
    assert provider1.units_committed == [u["id"] for u in _PLAN_UNITS]

    # A second run over the finished build re-runs NOTHING.
    provider2 = FakeWorkerProvider(sandbox=sandbox, session_id="plan-sess-1")
    result2 = await _make_executor(store, chat, provider2, sandbox).run(_bundle("Build it"), run_id=run_id)
    assert provider2.calls == 0, "a fully-finished build resumes to a no-op (no unit redone)"
    assert result2.status in ("done", "completed")
    assert result2.units_done == [u["id"] for u in _PLAN_UNITS]


@pytest.mark.asyncio
async def test_worker_turn_is_readwrite_and_streams_progress(tmp_path: Path) -> None:
    """The subtask turns run on the WORKER disposition (readwrite — the build path advertises
    the sandbox write tools) and stream tool_start + each captured commit as progress (§3.6.2).
    """
    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id)
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider = FakeWorkerProvider(sandbox=sandbox, session_id="plan-sess-1")

    progress_events: list[Any] = []

    async def on_progress(ev: Any) -> None:
        progress_events.append(ev)

    executor = _make_executor(store, chat, provider, sandbox, on_progress=on_progress)
    await executor.run(_bundle("Build it"), run_id=run_id)

    # The worker turn advertises the sandbox write set (readwrite build path).
    allowed = provider.seen_allowed_tools[0]
    assert "mcp__code__write_file" in allowed, "the build worker is readwrite (write tools mounted)"
    # Progress streamed at the real tool boundary (the write TOOL_USE), never off prose.
    assert progress_events, "the room sees live tool-boundary progress during the build"


@pytest.mark.asyncio
async def test_executor_never_throws_on_provider_error_fails_honestly(tmp_path: Path) -> None:
    """Rule 6 / §3.3: a provider ERROR on a subtask turn does not crash the executor — it
    fails the build honestly (never a raised exception through the host boundary)."""

    class ErrorProvider(FakeWorkerProvider):
        def stream(self, prompt: str, query: Any) -> AsyncIterator[AgentChunk]:
            self.calls += 1
            sid = self._session_id

            async def gen() -> AsyncIterator[AgentChunk]:
                yield AgentChunk(type="INIT", metadata={"session_id": sid})
                yield AgentChunk(type="ERROR", metadata={"message": "provider blew up"})

            return gen()

    store, chat = FakeStore(), FakeChat()
    run_id = str(uuid.uuid4())
    _seed_plan(store, run_id)
    repo = _init_repo(tmp_path / "clone")
    sandbox = RealGitSandbox(repo)
    provider = ErrorProvider(sandbox=sandbox, session_id="plan-sess-1")
    executor = _make_executor(store, chat, provider, sandbox)

    # No exception escapes — the build fails honestly.
    result = await executor.run(_bundle("Build it"), run_id=run_id)
    assert result.status == "failed"
    assert result.units_done == []
    assert sandbox.published == []
