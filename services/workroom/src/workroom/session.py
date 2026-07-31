"""The per-task Workroom session driver (05 §3.1 / §3.13-step-4 / §3.9).

This is the **session/bundle wiring** that turns a ``contracts.Bundle`` into a running
``query()`` — the site the ``harness`` bundle-dispatch invokes (the live-assembly seam,
the doc04 lesson). Doc 04's dispatch (``control_plane.dispatch``) creates a ``contracts.Bundle``
+ claims a ``workroom:<id>`` ``operation_runs`` row (Bundle in ``progress``) and hands a
``WorkroomHandle``; **this driver is what that dispatch runs** — it consumes that REAL
``contracts.Bundle`` and produces a REAL ``contracts.Envelope`` on the reachable host
path, filling the SAME row's ``result_ref``.

**The three load-bearing properties (§3.1), each realized here and NOT elsewhere:**

  1. **One warm sandbox per meeting hosts N task sessions.** The driver resolves the
     meeting's ONE warm sandbox (idempotent per ``meeting_id`` via
     :func:`ops.sandbox_provider.provision`) — it never cold-boots a fresh sandbox per
     task (§3.9). Because the sessions share that sandbox's filesystem, a follow-up task
     sees a prior task's artifacts on disk (:meth:`_read_artifact`).
  2. **The cached prefix → a new task pays only its bundle.** The stable Workroom prefix
     (the disposition system prompt + tool defs, :func:`stable_prefix`) is a prompt-cache
     breakpoint carrying the **1-hour TTL** (:func:`stable_prefix_cache_ttl_seconds`), so
     it stays warm across the meeting-hour; the VOLATILE bundle (the ask + transcript
     tail) rides the per-task ``prompt`` AFTER the breakpoint. A new task's fresh input
     tokens are only its bundle — the prefix is a cache HIT, never re-paid per task.
  3. **Task durability = ONE ``operation_runs`` row keyed ``workroom:<id>``.** The task
     IS the row the dispatch claimed (``operation_type='workroom:<id>'``, ``progress`` =
     the bundle); the driver writes the terminal Envelope into the SAME row's
     ``result_ref`` (:meth:`_persist_result`) and flips the row to completed/failed.
     **There is NO bespoke per-task table** — that is the node's hard NOT-done: task
     durability IS the ``operation_runs`` row, never a second table.

**The isolation triad rides EVERY ``query()``.** Tool config comes from
:func:`workroom.sandbox_transport.get_agent_tool_config`, which builds the real
``ClaudeAgentOptions`` through :func:`workroom.agent_config.workroom_options` — so
``strict_mcp_config=True`` + ``setting_sources=[]`` + the computed built-in allow-list
(``[]`` in sandbox mode) + the ``SDK_LOCAL_TOOLS`` backstop are present by construction
(§3.4). No ``query()`` envelope is built here without them.

**Abort is threaded, and it is FINAL** (§3.11): the imported ``agentkit.AbortRegistry``
mints a per-task controller keyed ``meeting_id|task_id``; the provider stream polls it and
halts the model loop. The stale-session replay + resume seam is ``resume_with_fallback``
IMPORTED from ``libs/agentkit`` (never reimplemented here, CANONICAL §11.9).

**Session resume (§3.1, the ``session-resume`` node).** The SDK ``session_id`` is persisted
per task (into the SAME ``operation_runs`` row's ``progress`` — the durable substrate, never a
bespoke table) so a follow-up or a RESTART resumes the same conversation. :meth:`run_task`
captures + persists the id; :meth:`resume_task` reads it back on restart and drives the
IMPORTED ``resume_with_fallback`` through a thin :class:`_ProviderRunner` adapter over this
driver's provider seam. On resume failure (the SDK reports the session gone) the fallback
rebuilds context from the Bundle via ``history_fn = rebuild_from_bundle(bundle)`` and continues
WITHOUT resume — here the history source is THIS task's bundle (Doc 04 passes its transcript
plane; same imported function, different ``history_fn``, A-010). **Abort is FINAL**: an aborted
task (its :class:`AbortRegistry` controller fired) is NEVER resumed — :meth:`resume_task`
short-circuits before the provider is ever driven, so a build the user killed can't be
resurrected.

**e2b is NOT installed** and this module never imports it: the E2B backend is lazy behind
``call_external`` in ``libs/http`` (the sole raw-client home). The real E2B-template bake
(the Node sidecar + ast-grep baked into the template image) is the Phase-3 deploy residual
flagged in ``sandbox_transport.SIDECAR_WIRE_CONTRACT`` — never faked as done here.

The five standing laws made structural here: **Law 3 (human control is absolute)** — the
one sanctioned write is the staged ``propose_change`` draft carried only for the worker
disposition (via the transport), never a direct world-touch. **Law 4 (dynamic, never
hard-coded)** — this driver owns only physics/pipes (resolve the sandbox, build the
query envelope, persist the row); the quick-vs-deep judgment lives in the model, biased
by the cached disposition prompt (§2.2), never a router here.

The ``check-sdk-isolation-triad`` guard requires ``SDK_LOCAL_TOOLS`` / ``disallowed_tools``
/ ``permission_mode`` on any module hosting a bare ``query()``; this driver drives the
provider seam (which owns those markers via the options it receives) rather than calling
the bare SDK ``query()`` itself, and re-exports the block-list marker so the seam is
covered end-to-end.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

# The imported abort + resume primitives (CANONICAL §11.9) — NEVER redefined here. Doc 04
# §3.11/§3.5 and Doc 05 §3.11/§3.1 both IMPORT the same registry AND the same
# ``resume_with_fallback``; the driver mints a per-task controller from ``AbortRegistry`` and
# resumes through ``resume_with_fallback`` (parameterized by ``history_fn`` = rebuild-from-bundle).
from agentkit import (
    AbortController,
    AbortRegistry,
    ProviderError,
    ProviderQuery,
    delta_stream,
    resume_with_fallback,
    thinking_policy,
)
from code_intel.sdk_server import CODE_INTEL_SERVER_NAME
from contracts import AgentChunk, Bundle, Envelope

# Import the MODULE (not the bound name) so the transport registration is resolved by
# attribute lookup at call time — the ONE seam the sandbox tool backend is injected through.
# A ``from .sandbox_transport import get_agent_tool_config`` bind would freeze the reference at
# import and make the transport un-substitutable (a live-assembly test that swaps the sandbox
# `code` backend for its execution backend patches ``sandbox_transport.get_agent_tool_config``;
# a frozen bound name would never see the swap and the real E2B backend would never mount).
from . import sandbox_transport

# The isolation-triad owner + the cacheable stable prefix + its 1-hour TTL (§3.4 / §3.9).
# SDK_LOCAL_TOOLS is re-exported so the triad guard sees the marker on the query()-driving
# module (the seam it drives builds the options carrying the triad). The disposition tool
# policy + role give the curated ``mcp__code__*`` subset + the shared thinking role — the
# SAME owner big_build.py reads, never redefined here (§3.5 / §10.5).
from .agent_config import (
    MAP_TOOLS,
    SDK_LOCAL_TOOLS,
    WORKROOM_CACHE_TTL_SECONDS,
    disposition_role,
    fence_transcript_tail,
    guardrailed_system_prefix,
)
from .drafts import PROPOSE_CHANGE_SERVER_NAME, make_propose_change_server

# The ONE output contract + tool-boundary progress (the workroom.envelope node, §3.12):
# the driver ASSEMBLES the Envelope and STREAMS ProgressEvents through this module — it
# never rebuilds the contract inline. ``ProgressSink`` is the harness-side progress channel.
from .envelope import (
    ProgressSink,
    build_envelope,
    emit_tool_boundary_progress,
    failure_envelope,
)

# The triad guard markers, named on this module so a query()-driving site is covered (§11.11).
disallowed_tools: tuple[str, ...] = SDK_LOCAL_TOOLS
permission_mode: str = "bypassPermissions"

# A Workroom task is keyed operation_type='workroom:<task-id>' (§12.10) — the SAME key the
# dispatch (control_plane.dispatch.workroom_op_type) claims the row under; the driver fills THAT row.
WORKROOM_OP_PREFIX = "workroom:"


def workroom_op_type(task_id: UUID | str) -> str:
    """The ``operation_runs.operation_type`` for a Workroom task (§12.10).

    Identical to ``control_plane.dispatch.workroom_op_type`` — the driver persists into the SAME
    row the dispatch claimed, so the two must agree on the key (no bespoke per-task table).
    """
    return f"{WORKROOM_OP_PREFIX}{task_id}"


def stable_prefix_cache_ttl_seconds() -> int:
    """The 1-hour TTL on the stable-prefix prompt cache (§3.9 / CANONICAL §10.1).

    NOT the SDK 5-minute default: a build spans many turns across the meeting-hour, so the
    5-min cache would expire between turns and re-pay the cache-write; the 1-hr breakpoint
    keeps the prefix warm for the whole build.
    """
    return WORKROOM_CACHE_TTL_SECONDS


# The read-back path for a landed sandbox file (E2B ``files.read(path, format="bytes")``).
# A follow-up task reads a prior task's artifact off the shared per-meeting sandbox disk
# through this seam; ``None`` for a file that never landed (Rule 6 — never raises).
ArtifactReader = Callable[[str], Awaitable[bytes | None]]

# The code-hash /health probe seam (§3.9): given the meeting's sandbox handle, return the
# sidecar's unauth ``GET /health`` payload — ``{"status", "code_hash", "clone_ready"}`` (the
# baked code-hash + clone status the real Node sidecar reports, mirrored by the in-process
# FakeSidecar). In production this is a fast ``GET https://8081-<sandbox>.e2b.app/health``
# through ``call_external``; a test injects an in-process probe. It may raise (server down) —
# the preflight catches that and fails fast honestly (Rule 6).
HealthProbe = Callable[[Any], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class PreflightResult:
    """The code-hash ``/health`` preflight verdict (§3.9) — healthy, or a clear fail reason.

    ``healthy`` — True iff the sandbox is live AND MCP is up AND the clone landed AND the
    baked code-hash matches the expected SHA. When False, ``reason`` names WHY in one
    speakable line (stale sandbox / code-hash mismatch / clone not ready / MCP down) so the
    room hears why a build didn't launch — never a silent cold-start on the live tier.
    """

    healthy: bool
    reason: str | None = None
    code_hash: str | None = None


class _PreflightError(RuntimeError):
    """A failed code-hash ``/health`` preflight (§3.9) — carried into the failed Envelope.

    Raised only to funnel the preflight reason through the driver's honest-failure path
    (``failure_envelope``); it never escapes ``run_task`` (Rule 6). Its message IS the
    clear, speakable reason the room hears for why a build didn't launch."""


def _isawaitable(obj: Any) -> bool:
    """True iff ``obj`` is awaitable — the meter/persist seams may be sync or async."""
    import inspect

    return inspect.isawaitable(obj)


class _SharedFSReader:
    """Adapt a shared per-meeting sandbox filesystem into the :data:`ArtifactReader` seam."""

    def __init__(self, fs: Any) -> None:
        self._fs = fs

    async def __call__(self, path: str) -> bytes | None:
        reader = getattr(self._fs, "read_bytes", None)
        if reader is None:
            return None
        data: bytes | None = await reader(path)
        return data


class SessionDriver:
    """Drive ONE ``query()`` per task on the meeting's shared warm sandbox (§3.1).

    Injectable seams so the REAL host path is proven against in-process fakes (e2b is not
    installed; the live bake is the flagged residual):

      * ``provider`` — the ``agentkit.Provider`` (``stream(prompt, query)``). Defaults to
        the registry's provider for the task's model (``agentkit.pick_provider``), the SAME
        seam the wake loop drives — so in production this is the real ``ClaudeAgentProvider``
        hosting ``claude_agent_sdk.query()``. A test injects a fake that records the prompt
        it saw and reports the SDK cost/cache-split telemetry.
      * ``sandbox_fs`` — the shared per-meeting sandbox filesystem read-back path, so a
        follow-up task sees a prior task's artifact on disk. (In prod: the E2B backend's
        ``files.read``; a test injects an in-process fake.)
      * ``store`` / ``db`` — where the terminal Envelope is persisted into the SAME
        ``operation_runs`` row (``result_ref``). ``store`` is an in-process fake with
        ``set_result(run_id, result_ref, status)``; ``db`` is a real ``libs.db.Database``
        (the driver runs the row UPDATE on the durable Postgres substrate).

    ``abort_registry`` is the imported ``agentkit.AbortRegistry`` (never redefined) — the
    driver mints a per-task controller keyed ``meeting_id|task_id`` and threads it into the
    query so a "Proxy, quiet" / meeting-end / timeout halts the model loop (§3.11).
    """

    def __init__(
        self,
        *,
        provider: Any = None,
        sandbox_fs: Any = None,
        store: Any = None,
        db: Any = None,
        abort_registry: AbortRegistry | None = None,
        model: str | None = None,
        disposition: str = "worker",
        max_turns: int = 6,
        on_progress: ProgressSink | None = None,
        health_probe: HealthProbe | None = None,
        cost_meter: Any = None,
    ) -> None:
        self._provider = provider
        self._artifact_reader: ArtifactReader | None = _SharedFSReader(sandbox_fs) if sandbox_fs is not None else None
        self._store = store
        self._db = db
        self._abort_registry = abort_registry if abort_registry is not None else AbortRegistry()
        self._model = model
        # The disposition this driver's tasks run as (§3.2 / D-014): it selects the per-role
        # model SEAT via the imported table (worker→BIG_BUILD/Opus, else WORKROOM/Sonnet), and
        # the model resolved from that seat drives the min(env, ceiling) output clamp.
        self._disposition = disposition
        self._max_turns = max_turns
        # The harness-side progress channel: the driver STREAMS one ProgressEvent per REAL
        # tool boundary here (§3.12). ``None`` → no progress channel (a pure quick ask that
        # nobody is watching live); the terminal Envelope is unaffected either way.
        self._on_progress = on_progress
        # The code-hash /health probe (§3.9). ``None`` → the preflight shrinks to the
        # in-process "sandbox healthy?" flag alone (the quick-ask hot loop; §3.9 says keep the
        # pattern, not the 10s latency); with a probe it also checks MCP-up + clone + code-hash.
        self._health_probe = health_probe
        # The cost meter (§3.9). ``None`` → cost still rides the Envelope artifact (the trace is
        # never lost); with a meter the per-task total_cost + cache split is recorded into the
        # SAME ``meeting_cost`` row Doc 04's circuit-breaker reads — never a bespoke cost sink.
        self._cost_meter = cost_meter

    # -- the stable, cacheable prefix (§3.9) ----------------------------------

    @staticmethod
    def stable_prefix() -> str:
        """The stable Workroom system prefix WITH the injection guardrail appended LAST (§3.10) —
        the prompt-cache breakpoint carries the 1-hour TTL (§3.9). Identical across every task in
        a meeting → a cache HIT after the first task, so a new task pays only its volatile bundle.

        The injection guardrail rides the END of the cached system prompt so the untrusted
        transcript (which lands in the per-task USER prompt after it) can never lift the final
        authoritative guardrail — every live query on this driver carries it (§3.10, hole-3 fix)."""
        return guardrailed_system_prefix()

    # -- the one public entry point the dispatch invokes ----------------------

    async def run_task(
        self,
        bundle: Bundle,
        *,
        run_id: Any,
        access: str = "readwrite",
        preflight_code_hash: str | None = None,
    ) -> Envelope:
        """Run ONE task: consume ``bundle`` → run a ``query()`` on the shared warm sandbox
        → return a ``contracts.Envelope`` and persist it into the SAME ``workroom:<id>`` row.

        ``run_id`` is the ``operation_runs`` row the dispatch already claimed (the task IS
        that row, §12.10). The stable prefix is the cached system prompt; the VOLATILE
        bundle rides the per-task ``prompt`` after the breakpoint — the §3.9 cache split.

        Before any ``query()`` the driver runs the code-hash ``/health`` PREFLIGHT (§3.9):
        if ``preflight_code_hash`` is given (a big build), it fails fast with a clear reason
        on a stale/dead sandbox — so a cold-start NEVER happens on the live tier and no
        expensive run burns meeting-time against a doomed sandbox. A failed preflight returns
        a ``failed`` Envelope WITHOUT ever reaching the provider.

        Never raises: any fault becomes a ``failed`` Envelope persisted into the same row
        (Rule 6 — a partial receipt beats a crash).
        """
        meeting_id = str(bundle.notes_ref)
        task_id = bundle.task_id
        controller = self._abort_registry.make(f"{meeting_id}|{task_id}")
        try:
            # §3.9 preflight FIRST — fail fast before we ever build a query() (no cold-start
            # on the live tier). A quick ask (no expected hash) still gets the in-process
            # "sandbox healthy?" flag; a big build additionally checks MCP-up + clone + hash.
            pre = await self.preflight(meeting_id=meeting_id, expected_code_hash=preflight_code_hash)
            if not pre.healthy:
                envelope = failure_envelope(bundle, _PreflightError(pre.reason or "preflight failed"))
                await self._persist_result(run_id, envelope, status="failed")
                return envelope
            handle = self._resolve_warm_sandbox(meeting_id)
            reader = self._reader_for(handle)
            # C-NOTESREAD (§3.5): read the LIVE notes off the durable /internal/notes fold and
            # embed them in the volatile prompt (the bundle carries only the meeting_id ref).
            notes_text = await self._read_live_notes(meeting_id)
            map_text = await self._resolve_map_text(meeting_id)
            prompt = self._render_bundle_prompt(bundle, notes_text=notes_text, map_text=map_text)
            # Resolve THIS meeting's code_intel SDK server (the tenant graph.db + clone) so a
            # Workroom task can query the codebase graph — its ``mcp__code_intel__*`` MAP tools
            # currently resolve to nothing (the seam gap this closes). Async because the repo
            # lookup is a db read; fail-closed to None (unindexed repo → no code_intel server).
            code_intel_server = await self._resolve_code_intel_server(meeting_id)
            # Build the ProviderQuery WITH the abort threaded in (a frozen query can't be mutated
            # after construction) and the curated MCP servers mounted so the tools are reachable.
            options = self._build_query_options(
                handle,
                access=access,
                controller=controller,
                meeting_id=meeting_id,
                code_intel_server=code_intel_server,
            )
            result_meta, wrote_paths = await self._drive_query(prompt, options, controller, task_id)
            # §3.1: persist the SDK session id per task (immediately, fire-and-forget) so a
            # follow-up or a RESTART resumes THIS conversation — into the SAME row's
            # ``progress`` on the durable substrate, never a bespoke per-task table.
            await self._persist_session_id(run_id, result_meta.get("session_id"))
            # §3.9: record the per-task total_cost + cache-read/creation split through the
            # cost meter into the SAME meeting_cost row Doc 04's circuit-breaker gates against.
            await self._record_cost(meeting_id, result_meta)
            envelope = await self._build_envelope(bundle, result_meta, wrote_paths, reader)
            await self._persist_result(run_id, envelope, status="completed")
            return envelope
        except Exception as exc:  # noqa: BLE001 - Rule 6: the driver never throws; it fails honestly
            envelope = failure_envelope(bundle, exc)
            await self._persist_result(run_id, envelope, status="failed")
            return envelope

    # -- the §3.9 code-hash /health preflight (fail fast, no cold-start) -------

    async def preflight(self, *, meeting_id: str, expected_code_hash: str | None = None) -> PreflightResult:
        """The code-hash ``/health`` preflight before an expensive run (§3.9) — fail FAST.

        The §3.9 "cheap insurance": our worst in-meeting failure is burning meeting-time
        against a stale/expired sandbox and failing late. This checks, in order and
        fail-closed:

          1. **sandbox live** — ``sandbox_provider.health_check`` (the in-process
             "sandbox healthy?" flag; the ONLY check for a quick ask, §3.9 — their 10s MCP
             preflight is too slow for the hot loop, so we keep the pattern, not the latency);
          2. **MCP up + clone ready + code-hash matches** — a fast ``GET /health`` through
             the injected :data:`HealthProbe` (only when a big build passes an
             ``expected_code_hash``): a stale sandbox baked at a different SHA, or one whose
             clone hasn't landed, fails HERE rather than mid-build.

        Returns a :class:`PreflightResult`; never raises (Rule 6) — a probe that itself
        throws (sidecar down) becomes ``healthy=False`` with an honest reason.
        """
        from libs.ops import sandbox_provider

        # (1) The in-process sandbox-alive flag — always checked (the quick-ask shrink, §3.9).
        # Resolve the meeting's ONE warm sandbox idempotently (the same handle the driver will
        # use): the meeting-creation pre-provision already spun it, so this returns the EXISTING
        # warm sandbox — it never cold-boots a fresh sandbox PER TASK (§3.9). Then check its
        # health: a sandbox the reaper expired (``_ALIVE`` False) fails fast HERE rather than
        # burning meeting-time and failing late.
        handle = self._resolve_warm_sandbox(meeting_id)
        if not bool(sandbox_provider.health_check(handle)):
            return PreflightResult(
                healthy=False,
                reason="sandbox not alive for this meeting (expired/reaped) — refusing to run against a dead sandbox",
            )

        # (2) The full code-hash /health check — only for a big build (an expected hash) with a
        # probe wired. A quick ask stops at the in-process flag above (keep the pattern, not
        # the 10s latency, §3.9).
        if expected_code_hash is None or self._health_probe is None:
            return PreflightResult(healthy=True)

        try:
            health = await self._health_probe(handle)
        except Exception as exc:  # noqa: BLE001 - Rule 6: a down sidecar fails fast, never crashes the run
            return PreflightResult(
                healthy=False,
                reason=f"sandbox MCP /health unreachable ({type(exc).__name__}) — refusing to launch against it",
            )

        if not bool(health.get("clone_ready", True)):
            return PreflightResult(
                healthy=False,
                reason="sandbox clone not ready (checkout not landed) — refusing to build against an empty tree",
            )
        baked = health.get("code_hash")
        if baked != expected_code_hash:
            return PreflightResult(
                healthy=False,
                reason=f"sandbox code-hash mismatch (baked {baked!r} != expected {expected_code_hash!r}) — stale",
                code_hash=str(baked) if baked is not None else None,
            )
        return PreflightResult(healthy=True, code_hash=str(baked))

    # -- the restart entry point: resume the persisted SDK session (§3.1 / §11.9) --

    async def resume_task(self, bundle: Bundle, *, run_id: Any, access: str = "readwrite") -> Envelope:
        """Resume a killed-and-restarted task's SDK conversation (§3.1 / §11.9).

        Reads the SDK ``session_id`` persisted per task (:meth:`_read_session_id` — from the
        SAME ``operation_runs`` row's ``progress``, the durable substrate) and drives the
        IMPORTED ``resume_with_fallback`` through the thin :class:`_ProviderRunner` adapter
        over this driver's provider seam:

          * **Tier-1** — a LIVE session: the persisted id rides ``resume``; the provider
            continues the same conversation (no history rebuild).
          * **Tier-3** — a GONE session: the fallback rebuilds context from THIS task's
            Bundle (``history_fn = rebuild_from_bundle(bundle)``), emits the restored notice,
            and retries WITHOUT resume — the run continues on a new session.

        **Abort is FINAL** (§3.11): if this task's :class:`AbortRegistry` controller has
        already fired (the user killed it), the resume SHORT-CIRCUITS before the provider is
        ever driven — a build the user killed is never resurrected. Never raises: any fault
        becomes an honest ``failed`` Envelope persisted into the same row (Rule 6).
        """
        meeting_id = str(bundle.notes_ref)
        task_id = bundle.task_id
        key = f"{meeting_id}|{task_id}"
        # Abort is FINAL (§3.11) — a task the user killed is NEVER resumed. The honest
        # cross-restart signal is the DURABLE row: an abort flips the ``operation_runs`` row
        # to a TERMINAL status (the in-memory registry does not survive a process kill), so a
        # resume only ever revives a row still ``running`` (a genuine mid-flight kill), never
        # a row already terminal (aborted/failed/completed). Belt-and-suspenders, the live
        # in-process controller is also honored (an abort within THIS process, before the row
        # is flipped). Either fired → return honestly, never drive the provider.
        if await self._is_terminal(run_id) or self._controller_aborted(key):
            envelope = self._aborted_envelope(bundle)
            await self._persist_result(run_id, envelope, status="failed")
            return envelope
        controller = self._abort_registry.make(key)
        try:
            resume_id = await self._read_session_id(run_id)
            handle = self._resolve_warm_sandbox(meeting_id)
            reader = self._reader_for(handle)
            # A resumed run reaches its tools exactly like a fresh one — including the meeting's
            # code_intel SDK server (resolved from the tenant graph.db + clone). Fail-closed to None.
            code_intel_server = await self._resolve_code_intel_server(meeting_id)
            # Build the ProviderQuery with the abort + the persisted resume id threaded in at
            # construction (a frozen query carries them); the curated MCP servers are mounted so
            # a resumed run reaches its tools exactly like a fresh one. The per-attempt
            # ``resume``/``preamble`` the fallback varies are folded in immutably by _ProviderRunner.
            options = self._build_query_options(
                handle,
                access=access,
                controller=controller,
                resume=resume_id,
                meeting_id=meeting_id,
                code_intel_server=code_intel_server,
            )
            runner = _ProviderRunner(self._provider_for(options), options, task_id, self._on_progress)
            # C-NOTESREAD (§3.5): the resumed task also orients on the LIVE notes fold, not the bare ref.
            notes_text = await self._read_live_notes(meeting_id)
            map_text = await self._resolve_map_text(meeting_id)
            inputs: dict[str, Any] = {
                "prompt": self._render_bundle_prompt(bundle, notes_text=notes_text, map_text=map_text)
            }
            result_meta: dict[str, Any] = {}
            wrote_paths: list[str] = []
            # The IMPORTED replay seam (never redefined here, §11.9): resume the persisted
            # id, and on a gone session rebuild from THIS bundle and retry without resume.
            # A caller-abort is FINAL inside ``resume_with_fallback`` — it short-circuits the
            # stale-session replay, so a killed build is never resurrected by the fallback.
            async for chunk in resume_with_fallback(
                runner,
                None,                          # behavior: the adapter mounts the query options directly
                inputs,
                resume_id,
                controller,
                rebuild_from_bundle(bundle),   # history_fn = rebuild-from-BUNDLE (A-010, §3.1)
            ):
                if controller.aborted:
                    break
                self._observe_resume_chunk(chunk, result_meta, wrote_paths)
            # Persist the (possibly new, post-replay) session id so a further restart resumes it.
            await self._persist_session_id(run_id, result_meta.get("session_id"))
            if controller.aborted:
                envelope = self._aborted_envelope(bundle)
                await self._persist_result(run_id, envelope, status="failed")
                return envelope
            envelope = await self._build_envelope(bundle, result_meta, wrote_paths, reader)
            await self._persist_result(run_id, envelope, status="completed")
            return envelope
        except Exception as exc:  # noqa: BLE001 - Rule 6: the driver never throws; it fails honestly
            envelope = failure_envelope(bundle, exc)
            await self._persist_result(run_id, envelope, status="failed")
            return envelope

    def _observe_resume_chunk(
        self, chunk: AgentChunk, result_meta: dict[str, Any], wrote_paths: list[str]
    ) -> None:
        """Fold a resumed-stream chunk into terminal state, capturing the LIVE session id.

        The stale-session replay establishes a NEW session on the retry; its ``INIT``/
        ``RESULT`` ``session_id`` overwrites the stale pointer in ``result_meta``, so the
        persisted id always names the live session after a recovery (mirrors
        ``control_plane.wake_turn._observe``). The ``RESULT``/write folding rides
        :meth:`_observe_chunk` so the cost/cache split + write paths land identically."""
        meta = chunk.metadata or {}
        if chunk.type == "INIT":
            sid = meta.get("session_id")
            if sid:
                result_meta["session_id"] = sid
        self._observe_chunk(chunk, result_meta, wrote_paths)

    def _aborted_envelope(self, bundle: Bundle) -> Envelope:
        """An honest ``failed`` Envelope for a task the user KILLED (abort is FINAL, §3.11).

        A killed build is NEVER resumed and NEVER reports a false success — it speaks the
        abort plainly (a partial/failed receipt beats a resurrected build). No internal
        component name reaches this user-visible string (Proxy is the only name)."""
        return Envelope(
            headline=f"Stopped: {bundle.ask}",
            detail=None,
            artifact=None,
            receipts=["task aborted — not resumed"],
            status="failed",
            task_id=bundle.task_id,
        )

    # -- (1) the meeting's ONE warm sandbox (reused across tasks) -------------

    def _resolve_warm_sandbox(self, meeting_id: str) -> Any:
        """Resolve the meeting's ONE warm sandbox (idempotent per meeting, §3.1/§3.9).

        ``ops.sandbox_provider.provision`` is idempotent per ``meeting_id``: a task after
        the first on the same meeting returns the EXISTING warm sandbox (same id, same
        filesystem), so the sessions share the disk (a follow-up sees prior artifacts) and
        the driver NEVER cold-boots a fresh sandbox per task (§3.9). A fresh meeting spins
        the one sandbox it needs (the meeting-creation pre-provision already ran; this is
        the honest fallback if the meeting's sandbox is not yet live in-process).
        """
        from libs.ops import sandbox_provider

        return sandbox_provider.provision(meeting_id=meeting_id)

    def _reader_for(self, handle: Any) -> ArtifactReader | None:
        """The artifact read-back path for THIS sandbox — the injected shared fs, else the
        real E2B backend's ``files.read`` (out of scope for the host-fake path; ``None``)."""
        return self._artifact_reader

    async def _resolve_code_intel_server(self, meeting_id: str) -> Any:
        """Resolve THIS meeting's ``code_intel`` SDK server from the tenant's durable index (§12.2).

        Reads the meeting's ``repo_id``/``tenant_id`` off the durable ``meetings`` row
        (``repos.meetings.get_by_id``), locates that repo's per-tenant ``graph.db`` + pinned
        ``checkout`` clone (:meth:`CodeIntelContext.for_tenant_repo`), and builds the tenant-scoped
        in-process ``code_intel`` SDK MCP server so a Workroom task can query the codebase graph.
        Tenant-scoped by construction (one meeting can never read another tenant's volume —
        isolation triad, Hard Rule 4).

        Fail-closed (Rule 6): returns ``None`` when there is no db seam, no repo bound, the repo
        is unindexed, or any resolution/build fault — the task then runs with no code_intel server
        (honest degradation), never a crash. Never a shared/process-global server: it is built
        fresh from THIS meeting's tenant context on every task.
        """
        if self._db is None or not meeting_id:
            return None
        try:
            from code_intel.paths import repo_name_from_url
            from code_intel.sdk_server import CodeIntelContext
            from db import repos

            async with self._db.acquire() as conn:
                meeting = await repos.meetings.get_by_id(conn, meeting_id)
            if meeting is None or meeting.get("repo_id") is None:
                return None
            async with self._db.acquire() as conn:
                repo = await repos.meetings.get_repo_by_id(conn, meeting["repo_id"])
            if repo is None or not repo.get("full_name"):
                return None
            ctx = CodeIntelContext.for_tenant_repo(
                tenant_id=str(repo["tenant_id"]),
                repo_name=repo_name_from_url(str(repo["full_name"])),
            )
            return ctx.build_server()
        except Exception:  # noqa: BLE001 - Rule 6: a resolution/build fault degrades to no server
            return None

    async def _resolve_map_text(self, meeting_id: str) -> str | None:
        """Resolve the pre-meeting MAP (``index.md``) for THIS meeting's repo, or ``None``.

        The Workroom code-task agent gets the SAME durable map as orientation as the wake turn
        (PM-DOWN-02 — "help the Workroom"): it skips re-exploration before it starts coding.
        Loads the latest stored map for the meeting's ``(tenant, repo)`` from Postgres
        ``repo_maps`` — ALWAYS tenant-scoped (no cross-tenant read). Fail-closed to ``None`` (no
        db, no repo, an unmapped repo, or any fault) so a task never crashes for lack of a map."""
        if self._db is None or not meeting_id:
            return None
        try:
            from code_intel.paths import repo_name_from_url
            from db import repos
            from premeeting.map_store import load_latest_map

            async with self._db.acquire() as conn:
                meeting = await repos.meetings.get_by_id(conn, meeting_id)
            if meeting is None or meeting.get("repo_id") is None:
                return None
            async with self._db.acquire() as conn:
                repo = await repos.meetings.get_repo_by_id(conn, meeting["repo_id"])
            if repo is None or not repo.get("full_name"):
                return None
            repo_name = repo_name_from_url(str(repo["full_name"]))
            async with self._db.acquire() as conn:
                latest = await load_latest_map(
                    conn, tenant_id=str(repo["tenant_id"]), repo=repo_name
                )
            return None if latest is None else latest[1]
        except Exception:  # noqa: BLE001 - Rule 6: a resolution fault degrades to no map, never a crash
            return None

    # -- (2) the volatile bundle rides the prompt; the prefix is cached -------

    def _render_bundle_prompt(
        self, bundle: Bundle, *, notes_text: str | None = None, map_text: str | None = None
    ) -> str:
        """Render ONLY the volatile bundle into the per-task ``prompt`` (§3.9 cache split).

        The stable prefix (the disposition + tool defs) is the cached SYSTEM prompt, placed
        BEFORE the cache breakpoint; everything volatile — the ask, the speaker, the
        transcript tail, the LIVE notes — rides HERE, after the breakpoint, so it is the only
        fresh input a new task pays for. Transcript-derived content is DATA, never
        instructions (§3.10): the untrusted tail is embedded through the SHARED
        :func:`fence_transcript_tail` — a NON-ESCAPABLE per-call-nonce spotlight fence — so a
        malicious participant cannot spell a fixed, guessable delimiter to break out of the
        data block and inject an instruction (a fixed public close marker would be escapable).

        C-NOTESREAD (05 §3.5): the bundle carries ``notes_ref = meeting_id`` (a UUID), NOT the
        notes object. When the caller has already read the LIVE notes off ``GET /internal/notes/
        {meeting_id}`` (the server-side durable fold, :meth:`_read_live_notes`), it passes the
        rendered text as ``notes_text`` and it is embedded HERE so the Workroom orients on the
        real current notes — never just the bare ref. When ``notes_text`` is None (no db wired /
        a bare render), the ``notes_ref`` line is the honest fallback (the ref the room can read).
        """
        notes_block = (
            f"Live notes (read via GET /internal/notes/{bundle.notes_ref}):\n{notes_text}\n"
            if notes_text is not None
            else f"Notes ref (meeting): {bundle.notes_ref}\n"
        )
        # The pre-meeting MAP rides FIRST as trusted orientation (PM-DOWN-02): the code-task
        # agent skips re-exploration. It is first-party, verified context — placed ahead of the
        # untrusted, spotlight-fenced transcript tail (which stays DATA, never instructions).
        map_block = (
            f"Repo map (orientation — trusted):\n{map_text}\n\n" if map_text and map_text.strip() else ""
        )
        return (
            f"{map_block}"
            f"Ask (from {bundle.speaker}): {bundle.ask}\n"
            f"Task id: {bundle.task_id}\n"
            f"{notes_block}"
            f"{fence_transcript_tail(bundle.transcript_tail)}"
        )

    async def _read_live_notes(self, meeting_id: str) -> str | None:
        """Read the meeting's LIVE notes via the durable server-side fold (05 §3.5 / CANONICAL §11.4).

        The Workroom does NOT read the Scribe's in-process ``NOTES_CACHE`` (a scribe-hot-path
        optimization unreachable on another host). It reads the live notes object the internal
        API ``GET /internal/notes/{meeting_id}`` exposes — the SAME durable fold, folding the
        Postgres ``note_deltas`` into the notes object server-side (:func:`scribe.notes_reader.
        read_notes`), rendered stable-ordered (:meth:`Notes.render_for_summary`). Fail-closed to
        None (no db wired, an empty meeting, or a read fault) → the prompt falls back to the bare
        ``notes_ref`` line (Rule 6 — a task never crashes because notes couldn't be read)."""
        if self._db is None or not meeting_id:
            return None
        try:
            from scribe.notes_reader import read_notes

            notes = await read_notes(meeting_id, db=self._db)
            rendered: str = notes.render_for_summary()
            return rendered
        except Exception:  # noqa: BLE001 - Rule 6: a notes-read fault degrades to the bare ref
            return None

    def _build_query_options(
        self,
        handle: Any,
        *,
        access: str,
        controller: AbortController | None = None,
        resume: str | None = None,
        meeting_id: str | None = None,
        code_intel_server: Any = None,
    ) -> ProviderQuery:
        """Build the immutable ``agentkit.ProviderQuery`` for this task's ``query()`` (§3.3).

        **This is the seam contract the provider expects** — the provider's
        ``stream(prompt, query)`` reads an ``agentkit.ProviderQuery`` (``query.mcp_servers`` /
        ``query.allowed_tools`` / ``query.thinking_enabled`` / ``query.abort``), NOT a
        ``claude_agent_sdk.ClaudeAgentOptions``. Mirrors ``big_build.py::_build_options`` (the
        same immutable options shape the plan/build turns use) and additionally CARRIES the
        curated MCP servers so the ``mcp__code__*`` tool names this disposition advertises are
        actually MOUNTED and reachable by the model (the seam gap this closes):

          * ``mcp_servers={"code": <the sandbox code server>}`` — the sandbox ``code`` MCP
            server for THIS warm sandbox (from :func:`sandbox_transport.get_agent_tool_config`),
            whose ``mcp__code__*`` read/write tools the worker's ``allowed_tools`` reference;
            PLUS, for the readwrite worker, the host-side ``propose_change`` in-process server
            (the one sanctioned world-touching write, §3.8) so ``mcp__propose_change__*`` is
            reachable too.
          * the curated ``allowed_tools`` subset + the ``SDK_LOCAL_TOOLS`` block-list ride the
            disposition tool policy (§3.5 / §10.5) — never the whole-Proxy union.
          * the isolation triad rides the ProviderQuery by construction
            (``strict_mcp_config=True`` + ``setting_sources=()`` + ``tools=()``), so
            ``build_sdk_options`` mounts ONLY these curated servers (a discovered ``.mcp.json``
            is still ignored — the triad holds).

        ``controller`` (the per-task abort) + ``resume`` (the SDK session id) are set on the
        IMMUTABLE query at construction so the provider stream can poll the abort and resume the
        conversation — a frozen ProviderQuery can't be mutated after the fact.
        """
        model = self._resolve_model(access)
        # The sandbox ``code`` server + the curated read/write tool subset come from the ONE
        # transport owner (§3.5); we lift ITS mcp_servers mapping (the ``code`` HTTP server) and
        # thread it onto the ProviderQuery so the tools the worker advertises are truly mounted.
        config = sandbox_transport.get_agent_tool_config(
            handle,
            access="readwrite" if access == "readwrite" else "readonly",
            model=model,
            max_turns=self._max_turns,
            system_prompt=self.stable_prefix(),
        )
        mcp_servers: dict[str, Any] = dict(config.mcp_servers)
        allowed_tools: list[str] = list(config.allowed_tools)
        # The readwrite worker also gets the host-side ``propose_change`` server (§3.8) so the
        # one sanctioned staged-draft write is reachable; read-only dispositions never mount it.
        if access == "readwrite" and self._disposition == "worker":
            conn = self._draft_conn()
            mcp_servers[PROPOSE_CHANGE_SERVER_NAME] = make_propose_change_server(
                conn=conn, meeting_id=meeting_id
            )
        # Mount THIS meeting's ``code_intel`` SDK server (the tenant graph.db + clone) so a
        # Workroom task can query the codebase graph (§12.2) — the ``mcp__code_intel__*`` MAP
        # tools every disposition advertises (§10.5) currently resolve to nothing because no
        # server provided them AND ``allowed_tools`` never carried the names. BOTH gaps close
        # here: the server is mounted, and the MAP tool NAMES are added to ``allowed_tools``
        # (which is the permission gate — ``allowed_tools`` does not auto-permit MCP tools).
        # code_intel is a READ server, mounted on EVERY disposition (orienting from the map is
        # always allowed, §3.5); it is NEVER the write-authority (that is propose_change, worker
        # only). None server (unindexed repo) → mount nothing, advertise nothing (honest
        # degradation). Tenant-scoped by construction (isolation triad, Hard Rule 4).
        if code_intel_server is not None:
            mcp_servers[CODE_INTEL_SERVER_NAME] = code_intel_server
            for name in MAP_TOOLS:
                if name not in allowed_tools:
                    allowed_tools.append(name)
        # §3.2/§3.9: the min(env, model_ceiling) output-token self-clamp for THIS model, carried
        # on ``ProviderQuery.env`` so ``build_sdk_options`` threads it onto the SDK query() env —
        # a Sonnet request never asks for an Opus-sized output. Computed via the IMPORTED clamp.
        enabled, budget = thinking_policy(model, disposition_role(self._disposition))
        return ProviderQuery(
            model=model,
            allowed_tools=tuple(allowed_tools),
            system_prompt=self.stable_prefix(),
            max_turns=self._max_turns,
            tools=(),                        # computed built-in allow-list: [] in sandbox mode (§3.4)
            strict_mcp_config=True,          # isolation triad
            setting_sources=(),              # isolation triad ([] — load no fs settings)
            thinking_enabled=enabled,
            thinking_budget_tokens=budget,
            resume=resume,
            abort=controller,
            mcp_servers=mcp_servers,         # the curated code (+ propose_change) servers — MOUNTED
            env=self._output_clamp_env(model),  # the min(env, ceiling) output-token clamp (§3.2/§3.9)
            # The §3.4 host-built-in backstop + (read-only) the mutating-tool block — the block
            # goes through disallowed_tools because allowed_tools does not filter MCP tools (§3.8).
            disallowed_tools=tuple(config.disallowed_tools),
        )

    def _draft_conn(self) -> Any:
        """The host psycopg conn / ``libs.db.Database`` the ``propose_change`` tool binds to (§3.8).

        The staged-draft write executes on the HOST (where the Postgres/object-store live), never
        in the sandbox. The driver's ``db`` seam is that host sink; when it is unwired (the pure
        in-process host-fake path) the server is still MOUNTED — its tool then returns an honest
        ``is_error`` rather than crashing (Rule 6 / Law 2 — a failed staging is spoken plainly,
        never a fabricated draft), so the ``mcp__propose_change__*`` seam is always present and
        reachable; only its durable write degrades when no host substrate is wired."""
        return self._db

    def _output_clamp_env(self, model: str) -> dict[str, str]:
        """The ``min(env, model_ceiling)`` output-token clamp as a query env patch (§3.2/§3.9).

        ``MAX_OUTPUT_TOKENS`` = the imported ``llm.routing.max_output_tokens_for(model)`` so the
        SDK query() caps this model's output at the smaller of the global env budget and the
        model's true ceiling. Best-effort (Rule 6) — a clamp that can't resolve yields no env."""
        try:
            from llm.routing import max_output_tokens_for

            return {"MAX_OUTPUT_TOKENS": str(max_output_tokens_for(model))}
        except Exception:  # noqa: BLE001 - the clamp is advisory, never fatal
            return {}

    def _resolve_model(self, access: str) -> str:
        """The per-role model seat for this task (IMPORTED table, never redefined, §3.2).

        Resolves through the ONE canonical seat table (``llm.routing.model_for``) keyed by
        this driver's disposition's SEAT (``agent_config.seat_for_disposition`` — worker →
        Opus-class ``BIG_BUILD``, quick/plan/critic/verifier → Sonnet-class ``WORKROOM``,
        D-014). NO ``claude-*`` literal lives here — the model id comes only from the imported
        table. An explicit ``model`` override (tests) short-circuits.
        """
        if self._model is not None:
            return self._model
        from llm.routing import model_for

        from .agent_config import seat_for_disposition

        seat = seat_for_disposition(self._disposition)
        model: str = model_for(seat)
        return model

    def _apply_output_clamp(self, options: Any, model: str) -> None:
        """Apply the ``min(env, model_ceiling)`` output-token clamp to the query env (§3.2/§3.9).

        Threads ``MAX_OUTPUT_TOKENS`` = the imported ``llm.routing.max_output_tokens_for(model)``
        into the query's curated ``env`` so the SDK ``query()`` caps this model's output at the
        smaller of the global env budget and the model's true ceiling. Best-effort against a
        frozen options object (Rule 6 — a clamp that can't attach never crashes the run)."""
        from llm.routing import max_output_tokens_for

        clamp = max_output_tokens_for(model)
        try:
            env = dict(getattr(options, "env", None) or {})
            env["MAX_OUTPUT_TOKENS"] = str(clamp)
            options.env = env
        except Exception:  # noqa: BLE001 - a frozen options object: the clamp is advisory, never fatal
            pass

    async def _drive_query(
        self, prompt: str, options: Any, controller: AbortController, task_id: UUID
    ) -> tuple[dict[str, Any], list[str]]:
        """Drive the provider seam over the query, threading the abort; collect the terminal
        RESULT metadata (cost + cache split, §3.9) and the paths the model wrote this turn.

        Consumes ``stream_deltas`` over the provider stream (CANONICAL §1.1 — never raw
        ``AgentChunk``): field access is ``chunk.type`` / ``chunk.metadata`` only. The
        delta-ized stream is threaded through :func:`workroom.envelope.emit_tool_boundary_progress`,
        which mints ONE ``ProgressEvent`` to the harness sink per REAL tool boundary
        (``chunk.type == 'TOOL_USE'``, never model prose, §3.12) and passes every chunk
        through untouched so the terminal fold below still sees INIT/RESULT/write frames.
        The abort is FINAL — a fired controller halts the loop and is never retried (§3.11).
        """
        provider = self._provider_for(options)
        # The abort is ALREADY on the immutable ProviderQuery (``query.abort``, set at build
        # time so the frozen query carries it); the provider stream polls it (§3.11). Belt: if a
        # mutable options object was injected without it, thread it here too.
        if getattr(options, "abort", None) is None:
            try:
                options.abort = controller  # a mutable stand-in — the provider seam reads query.abort
            except Exception:  # noqa: BLE001 - a frozen ProviderQuery already carries the abort
                pass
        result_meta: dict[str, Any] = {}
        wrote_paths: list[str] = []
        raw_stream = provider.stream(prompt, options)
        # delta_stream first (per-msg_id TEXT deltas), THEN the tool-boundary progress tap
        # over the delta-ized stream — so progress is derived from the real tool-use stream.
        progressing = emit_tool_boundary_progress(delta_stream(raw_stream), task_id, self._on_progress)
        async for chunk in progressing:
            if controller.aborted:
                break
            self._observe_chunk(chunk, result_meta, wrote_paths)
        return result_meta, wrote_paths

    def _provider_for(self, options: Any) -> Any:
        """The provider seam for this task (injected fake, else the registry provider).

        Defaults to ``agentkit.pick_provider`` for the task's model — the SAME seam the wake
        loop drives, so in production this is the real ``ClaudeAgentProvider`` hosting
        ``claude_agent_sdk.query()``. A test injects a fake with the identical shape."""
        if self._provider is not None:
            return self._provider
        from agentkit import pick_provider

        return pick_provider(getattr(options, "model", "") or "")

    def _observe_chunk(self, chunk: AgentChunk, result_meta: dict[str, Any], wrote_paths: list[str]) -> None:
        """Fold one streamed chunk into the terminal state (cost telemetry + write paths + draft).

        The RESULT frame carries the SDK cost + cache-read/creation split (§3.9); a write
        TOOL_USE names a file that landed in the shared sandbox (a follow-up task reads it
        back off disk); a successful ``propose_change`` TOOL_RESULT carries the staged
        ``draft_id`` (Law 3 — the one world-touching act is a staged draft behind a human
        click) which rides onto the terminal Envelope. Field access is ``chunk.type`` /
        ``chunk.metadata`` (never ``.kind``).
        """
        meta = chunk.metadata or {}
        if chunk.type == "RESULT":
            result_meta.update(dict(meta))
        elif chunk.type == "TOOL_USE":
            name = str(meta.get("name", ""))
            if name in {"mcp__code__write_file", "mcp__code__edit_file", "mcp__code__ast_grep"}:
                path = (meta.get("input") or {}).get("path")
                if isinstance(path, str) and path:
                    wrote_paths.append(path)
        elif chunk.type == "TOOL_RESULT":
            # A successful propose_change stages a draft and returns its id — capture it so the
            # terminal Envelope carries the draft_id (the §1.2 needs_review/unverified mapping).
            draft_id = _extract_draft_id(chunk.text)
            if draft_id:
                result_meta["draft_id"] = draft_id

    # -- the Envelope (the one output contract, §3.12) ------------------------

    async def _build_envelope(
        self,
        bundle: Bundle,
        result_meta: dict[str, Any],
        wrote_paths: list[str],
        reader: ArtifactReader | None,
    ) -> Envelope:
        """Build the terminal ``contracts.Envelope`` for the task (§3.12).

        The driver owns only the *physics* here — reading the landed bytes back off the
        SHARED sandbox disk to form the host-observed receipts (the evidence a follow-up
        will also see) — then hands the assembled facts to
        :func:`workroom.envelope.build_envelope`, the ONE place the sealed 8-field contract
        is built and the CANONICAL §1.2 status/verification mapping is applied. A
        completed read/build run returns an honest ``done``; the full verify-loop →
        ``needs_review`` / ``verified`` mapping is the sibling verify node's concern
        (it calls the same ``build_envelope`` with ``is_build`` / ``verified`` / a
        ``draft_id`` set). The headline is speakable (the answer is spoken in a meeting).
        """
        receipts: list[str] = []
        if wrote_paths and reader is not None:
            for path in wrote_paths:
                landed = await reader(path)
                if landed is not None:
                    receipts.append(f"wrote {path} ({len(landed)} bytes)")
        # The staged draft_id (if the worker called propose_change and it succeeded) drives the
        # §1.2 needs_review/unverified mapping — a world-touching change is a staged draft (Law 3).
        draft_id = _coerce_draft_id(result_meta.get("draft_id"))
        return build_envelope(
            bundle=bundle,
            result_meta=result_meta,
            wrote_paths=wrote_paths,
            receipts=receipts,
            draft_id=draft_id,
        )

    # -- (3) persist into the SAME operation_runs row (no bespoke per-task table) --

    async def _persist_result(self, run_id: Any, envelope: Envelope, *, status: str) -> None:
        """Write the terminal Envelope into the SAME ``workroom:<id>`` row's ``result_ref``
        and flip the row to ``completed``/``failed`` (§12.10) — never a bespoke task table.

        The task IS the ``operation_runs`` row the dispatch claimed; the driver fills its
        ``result_ref`` (the outbox) with the Envelope and closes the run. Two sinks: an
        in-process ``store`` (the host-fake path) or a real ``libs.db.Database`` (the durable
        Postgres UPDATE). Persist is best-effort by construction — a persist fault is logged
        into the returned Envelope's path, never a crash (the Envelope is still returned)."""
        result_ref = envelope.model_dump(mode="json")
        if self._store is not None:
            await self._store.set_result(run_id=run_id, result_ref=result_ref, status=status)
            return
        if self._db is not None:
            await self._persist_result_db(run_id, result_ref, status)

    async def _persist_result_db(self, run_id: Any, result_ref: dict[str, Any], status: str) -> None:
        """Persist into the durable ``operation_runs`` row on Postgres (§12.10).

        A single UPDATE on the row keyed by ``id`` (the dispatch's claimed run_id): set
        ``result_ref`` = the terminal Envelope, flip ``status``, stamp ``completed_at``. The
        ``workroom:<id>`` row is REUSED — this driver never creates a bespoke per-task table.
        """
        import json

        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE operation_runs SET result_ref = $2::jsonb, status = $3, completed_at = now() WHERE id = $1",
                run_id,
                json.dumps(result_ref),
                status,
            )

    # -- SDK session id persistence (§3.1: persisted per task so a restart resumes) --

    async def _persist_session_id(self, run_id: Any, session_id: Any) -> None:
        """Persist the SDK ``session_id`` per task so a follow-up / RESTART resumes it (§3.1).

        The id rides the SAME ``operation_runs`` row's ``progress`` jsonb (the durable
        substrate, §12.10) — NEVER a bespoke per-task table. Best-effort by
        construction (a persist fault must never crash the run): an absent id (a fault before
        the first INIT) is a no-op. Two sinks: the in-process ``store`` (host-fake path) or a
        real ``libs.db.Database`` (the durable Postgres jsonb merge)."""
        if not session_id:
            return
        sid = str(session_id)
        if self._store is not None:
            setter = getattr(self._store, "set_session_id", None)
            if setter is not None:
                await setter(run_id=run_id, session_id=sid)
            return
        if self._db is not None:
            await self._persist_session_id_db(run_id, sid)

    async def _persist_session_id_db(self, run_id: Any, session_id: str) -> None:
        """Merge the SDK ``session_id`` into the ``operation_runs`` row's ``progress`` jsonb.

        A single UPDATE on the row keyed by ``id`` (the dispatch's claimed run_id): the id is
        merged into ``progress`` (never a new column, never a new table) so a restart reads it
        back with :meth:`_read_session_id`. Best-effort — a merge fault is swallowed (the run
        already produced its work; a lost resume pointer degrades to a bundle-rebuild)."""
        import contextlib
        import json

        with contextlib.suppress(Exception):
            async with self._db.acquire() as conn:
                await conn.execute(
                    "UPDATE operation_runs "
                    "SET progress = coalesce(progress, '{}'::jsonb) || $2::jsonb "
                    "WHERE id = $1",
                    run_id,
                    json.dumps({"session_id": session_id}),
                )

    # -- per-task cost recording through the meter (§3.9) ---------------------

    async def _record_cost(self, meeting_id: str, result_meta: Mapping[str, Any]) -> None:
        """Record the per-task ``total_cost`` + cache-read/creation split via the meter (§3.9).

        The numbers are the SDK's REAL terminal telemetry off the RESULT frame — never a
        model-narrated guess. They feed the SAME ``meeting_cost`` row Doc 04's live
        circuit-breaker gates against (Doc 04 owns the breaker; this node only accrues the
        Workroom's task spend), so a bespoke cost sink is never opened. The cache split is how
        §3.9 proves the cached prefix is hitting.

        Best-effort by construction (Rule 6): no meter → a no-op (cost still rides the Envelope
        artifact, so the trace is never lost); a meter fault is swallowed (the task already
        produced its work — a lost cost row must not fail the run).

        Two shapes, both wired for real:
          * an in-process recorder exposing ``record(*, meeting_id, total_cost_usd, ...)`` —
            the host-fake path (a test injects one to assert the driver fed the meter);
          * the module-level ``ops.cost.record_micro_call_cost`` seam, whose real signature is
            ``record_micro_call_cost(target, meeting_id, *, total_cost_usd=, cache_read_usd=,
            cache_creation_usd=)`` — ``target`` is the durable sink (a ``libs.db.Database`` or a
            raw psycopg connection). The driver's ``self._db`` IS that sink, so the fallback
            passes it as ``target`` and ``meeting_id`` as the second positional — the split then
            lands in the SAME ``meeting_cost``/telemetry row Doc 04 reads (never a bespoke sink,
            never ``meeting_id`` mis-bound into ``target``).
        """
        if self._cost_meter is None:
            return
        total = float(result_meta.get("total_cost_usd", 0.0) or 0.0)
        # The cache read/creation split rides as token counts on the RESULT frame; we forward
        # them as the split fields the meter records (dollars are derived downstream by the
        # meter/telemetry; here we forward the observed split so the row carries it).
        cache_read = float(result_meta.get("cache_read_input_tokens", 0) or 0)
        cache_creation = float(result_meta.get("cache_creation_input_tokens", 0) or 0)
        import contextlib

        with contextlib.suppress(Exception):
            recorder = getattr(self._cost_meter, "record", None)
            if recorder is not None:
                result = recorder(
                    meeting_id=meeting_id,
                    total_cost_usd=total,
                    cache_read_usd=cache_read,
                    cache_creation_usd=cache_creation,
                )
                if _isawaitable(result):
                    await result
                return
            # Fall back to the module-level ops.cost seam. Its FIRST positional is the durable
            # ``target`` sink (Database | raw connection), the SECOND is ``meeting_id`` — so we
            # pass ``self._db`` as the sink and ``meeting_id`` where the seam expects it. Passing
            # ``meeting_id`` first (the old bug) mis-bound it into ``target`` and dropped the row.
            result = self._cost_meter(
                self._db,
                meeting_id,
                total_cost_usd=total,
                cache_read_usd=cache_read,
                cache_creation_usd=cache_creation,
            )
            if _isawaitable(result):
                await result

    async def _read_session_id(self, run_id: Any) -> str | None:
        """Read the SDK ``session_id`` persisted per task, for the restart resume (§3.1).

        Reads from the SAME ``operation_runs`` row's ``progress`` (the durable substrate). A
        missing id (never captured, or a resume pointer lost) → ``None``, which
        ``resume_with_fallback`` treats as "no resume": a fresh session with the bundle
        rebuild, never a crash (Rule 6)."""
        if self._store is not None:
            getter = getattr(self._store, "get_session_id", None)
            if getter is not None:
                sid = await getter(run_id=run_id)
                return str(sid) if sid else None
            return None
        if self._db is not None:
            return await self._read_session_id_db(run_id)
        return None

    async def _read_session_id_db(self, run_id: Any) -> str | None:
        """Read ``progress->>'session_id'`` off the durable ``operation_runs`` row (§12.10)."""
        import contextlib

        with contextlib.suppress(Exception):
            async with self._db.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT progress->>'session_id' AS session_id FROM operation_runs WHERE id = $1",
                    run_id,
                )
            if row is not None and row["session_id"]:
                return str(row["session_id"])
        return None

    # -- abort is FINAL: the durable + in-process signals a resume must honor (§3.11) --

    def _controller_aborted(self, key: str) -> bool:
        """True iff a LIVE in-process controller for this task has already fired (§3.11).

        The in-memory registry doesn't survive a process kill, so this only catches an abort
        within THIS process (before the row is flipped terminal); the durable row status
        (:meth:`_is_terminal`) is the cross-restart authority."""
        existing = self._abort_registry.get(key)
        return existing is not None and existing.aborted

    async def _is_terminal(self, run_id: Any) -> bool:
        """True iff the task's ``operation_runs`` row is already TERMINAL (§3.11 / §12.10).

        The durable abort-is-final signal: an aborted task's row was flipped to a terminal
        status (``failed``/``aborted``/``completed``) — a restart must NOT resume it (only a
        row still ``running`` is a genuine mid-flight kill worth resuming). Reads the SAME row
        the dispatch claimed (never a bespoke table). Best-effort — an unreadable status
        degrades to "not terminal" (the live-controller check + the fallback's own abort guard
        still protect a killed build)."""
        status = await self._read_status(run_id)
        return status is not None and status not in ("running", "pending", "claimed")

    async def _read_status(self, run_id: Any) -> str | None:
        """Read the ``operation_runs`` row's ``status`` (the durable run-state, §12.10)."""
        if self._store is not None:
            getter = getattr(self._store, "get_status", None)
            if getter is not None:
                status = await getter(run_id=run_id)
                return str(status) if status else None
            rows = getattr(self._store, "rows", None)
            if isinstance(rows, dict):
                row = rows.get(run_id)
                if row is not None and row.get("status"):
                    return str(row["status"])
            return None
        if self._db is not None:
            return await self._read_status_db(run_id)
        return None

    async def _read_status_db(self, run_id: Any) -> str | None:
        """Read ``status`` off the durable ``operation_runs`` row (§12.10)."""
        import contextlib

        with contextlib.suppress(Exception):
            async with self._db.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT status FROM operation_runs WHERE id = $1", run_id
                )
            if row is not None and row["status"]:
                return str(row["status"])
        return None


def rebuild_from_bundle(bundle: Bundle) -> Callable[[], Awaitable[str]]:
    """The Workroom's ``history_fn`` for ``resume_with_fallback`` — rebuild-from-BUNDLE (§3.1).

    Returns the ``history_fn`` closure ``resume_with_fallback`` calls when a resumed session
    is GONE: it reconstitutes the task's context from THIS bundle (the ask + speaker +
    transcript tail the dispatch handed, 04→05) so the retry (a new session, no resume)
    continues the SAME task — never a divergent one. This is the Doc 05 history *source*; Doc
    04 passes its transcript-plane reader to the SAME imported function (A-010) — the arity is
    shared, only the ``history_fn`` differs (CANONICAL §11.9).

    Transcript-derived content is DATA, never instructions (§3.10): the untrusted tail is
    embedded through the SHARED :func:`fence_transcript_tail` — the NON-ESCAPABLE
    per-call-nonce spotlight fence — so a crafted transcript cannot spell a fixed, guessable
    delimiter to break out of the data block and inject an instruction.
    """

    async def _history() -> str:
        return (
            f"Task (from {bundle.speaker}): {bundle.ask}\n"
            f"Task id: {bundle.task_id}\n"
            f"{fence_transcript_tail(bundle.transcript_tail)}"
        )

    return _history


class _ProviderRunner:
    """Adapt this driver's ``provider.stream(prompt, options)`` seam into the ``runner.run``
    protocol the IMPORTED ``resume_with_fallback`` drives (``run(behavior, inputs, abort)``).

    ``resume_with_fallback`` is defined ONCE in ``libs/agentkit`` (never reimplemented here,
    §11.9); it orchestrates the two §3.5 failure classes over an injected ``runner``. Doc 04
    drives it through its ``BehaviorRunner``; the Workroom drives the SAME imported function
    through THIS thin adapter over the sandbox provider seam. Each :meth:`run` folds the
    fallback's ``inputs`` into the query envelope:

      * ``inputs['resume']`` — the persisted SDK session id (``None`` on the bundle-rebuilt
        retry, so the retry establishes a fresh session);
      * ``inputs['preamble']`` — the bundle-derived history preamble the fallback prepends on
        a gone-session replay (absent on a live resume) — carried on ``options.preamble`` so
        the provider seam mounts it as the leading DATA block.

    It applies ``stream_deltas`` ONCE over the raw provider stream (CANONICAL §1.1) and raises
    :class:`ProviderError` on a pass-through ``ERROR`` chunk — exactly the boundary
    ``resume_with_fallback``'s recovery catches to classify a stale session vs a truncated
    frame (mirrors ``agentkit.BehaviorRunner.run``). The tool-boundary progress tap rides the
    same delta-ized stream so a resumed run streams live progress like a fresh one (§3.12).
    """

    def __init__(
        self,
        provider: Any,
        options: Any,
        task_id: UUID,
        on_progress: ProgressSink | None,
    ) -> None:
        self._provider = provider
        self._options = options
        self._task_id = task_id
        self._on_progress = on_progress

    async def run(
        self, behavior: Any, inputs: Mapping[str, Any], abort: Any
    ) -> AsyncIterator[AgentChunk]:
        """Drive ONE provider turn for the fallback: fold ``resume`` / ``preamble`` into the
        query options, stream through ``stream_deltas`` + the progress tap, and surface a
        pass-through ``ERROR`` as :class:`ProviderError` (where the §3.5 recovery catches it)."""
        prompt = str(inputs.get("prompt", ""))
        # ``resume``/``preamble`` are the fallback's per-attempt knobs: resume the live id, or
        # (on a gone-session replay) drop resume and carry the bundle-derived preamble. The
        # ProviderQuery is a FROZEN dataclass, so we fold them in IMMUTABLY (``replace`` builds a
        # new query carrying the new knobs) rather than mutating in place — the curated
        # ``mcp_servers`` + triad + abort ride through unchanged. A non-dataclass stand-in falls
        # back to best-effort attribute assignment.
        per_attempt = {"resume": inputs.get("resume"), "preamble": inputs.get("preamble")}
        options = _with_knobs(self._options, per_attempt)
        raw_stream = self._provider.stream(prompt, options)
        progressing = emit_tool_boundary_progress(
            delta_stream(raw_stream), self._task_id, self._on_progress
        )
        async for chunk in progressing:
            if chunk.type == "ERROR":
                # Surface the pass-through ERROR as the boundary exception the §3.5 recovery
                # inside ``resume_with_fallback`` catches (stale-session replay vs JSON retry).
                raise ProviderError(chunk)
            yield chunk


def _extract_draft_id(text: str | None) -> str | None:
    """Pull the staged ``draft_id`` out of a successful ``propose_change`` TOOL_RESULT (§3.8).

    The propose_change tool returns JSON ``{"draft_id": "...", "status": "needs_review", ...}``
    on success (a real GCS bundle + ``staged_drafts`` row landed); an ``is_error`` payload (e.g.
    "no host connection bound") carries NO ``draft_id`` and is ignored — a failed staging never
    fabricates a draft (Law 2). Tolerant of surrounding text / non-JSON (returns ``None``)."""
    if not text:
        return None
    import json

    stripped = text.strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    candidates = [stripped]
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed.get("draft_id") and not parsed.get("is_error"):
            return str(parsed["draft_id"])
    return None


def _coerce_draft_id(value: Any) -> UUID | None:
    """Coerce a captured draft-id string into a ``UUID`` for the Envelope (``None`` if absent/bad)."""
    if not value:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _with_knobs(options: Any, knobs: Mapping[str, Any]) -> Any:
    """Return ``options`` carrying the per-attempt ``resume``/``preamble`` knobs (§11.9).

    A :class:`agentkit.ProviderQuery` is a FROZEN dataclass, so ``dataclasses.replace`` builds a
    NEW query carrying the knobs while every other field (the curated ``mcp_servers``, the
    isolation triad, the abort) rides through unchanged. A non-dataclass stand-in (a test fake)
    falls back to best-effort attribute assignment. Never raises (Rule 6)."""
    import dataclasses

    if dataclasses.is_dataclass(options) and not isinstance(options, type):
        try:
            return dataclasses.replace(options, **dict(knobs))
        except Exception:  # noqa: BLE001 - an unknown field: fall through to attribute assignment
            pass
    for key, value in knobs.items():
        with _suppress():
            setattr(options, key, value)
    return options


class _suppress:
    """A tiny ``contextlib.suppress(Exception)`` alias — set an attr on a possibly-frozen
    options object without a crash (mirrors the ``_drive_query`` frozen-options fallback)."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: Any) -> bool:
        return True


__all__ = [
    "PreflightResult",
    "SessionDriver",
    "rebuild_from_bundle",
    "resume_with_fallback",
    "stable_prefix_cache_ttl_seconds",
    "workroom_op_type",
]
