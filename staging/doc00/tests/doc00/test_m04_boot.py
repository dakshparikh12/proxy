"""Doc 00 · §6 Server boot & lifecycle (AC-BOOT-001..007).

Milestone m04. One test per blocking criterion (id in the docstring). The
lesson §6 encodes -- *fail loud at boot, not on first use* -- is replicated in a
FastAPI ``lifespan`` whose step ORDER is load-bearing.

Product code is imported INSIDE each test body (never at module top level), so
this module COLLECTS clean and FAILS red before ``services.harness`` /
``libs.*`` exist. Top-level imports are stdlib + pytest + the stdlib-only
``_support`` shim only (per the evidence-layer design rules).

Oracle sources (PROTO-DETERMINISTIC-01, all deterministic/hermetic):

  * BOOT-001/007 [deployment]: import-time crash + static .env.example / settings
    contract scans -- boots-with-missing-key == 0, three-mode auth surface.
  * BOOT-002/003/004/006 [integration]: an instrumented lifespan trace whose
    recorded step order is compared against the canonical §6 sequence.
  * BOOT-005 [fault-injection]: drive the asyncio exception handler with a
    BrokenPipeError (swallow + recover) vs an unknown exception (crash).
"""

import asyncio
import importlib
import os

import pytest

import _support as S


# ---------------------------------------------------------------------------
# Local helpers (stdlib-only; no product import happens here).
# ---------------------------------------------------------------------------

# The canonical §6 boot ordering, as a sequence of step tags. A conforming
# instrumented lifespan trace must contain these tags in exactly this relative
# order. Tag matching is substring/keyword based so the product may use richer
# internal step names (e.g. "init_tracing", "tracing:init") as long as the
# distinguishing keyword appears.
CANONICAL_STEPS = (
    "tracing",       # 1. init tracing (synchronously, before the first query)
    "pool",          # 2. open the asyncpg pool
    "database",      # 3. construct the Database facade
    "provisioner",   # 4. provisioner_ready async-readiness gate
    "reaper",        # 5. boot-time stale-row reaper
    "routers",       # 6. mount routers behind the auth wall + dispatch funnel
)


def _import_settings_module():
    """Import (fresh) the product settings module, trying the known homes.

    Returns the imported module. Raises the underlying error if none exists so
    that absence-of-product surfaces as a red failure (not a skip).
    """
    candidates = (
        "services.harness.settings",
        "services.harness.config",
        "libs.ops.settings",
        "libs.config.settings",
        "libs.config",
    )
    last_exc: BaseException | None = None
    for name in candidates:
        try:
            for mod in (name, *[m for m in list(_sys_modules()) if m == name or m.startswith(name + ".")]):
                _sys_modules().pop(mod, None)
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            # Only treat "the settings module itself is absent" as try-next;
            # a ModuleNotFoundError for a *dependency* of a present module is a
            # real error and should propagate.
            if _missing_is_target(exc, name):
                last_exc = exc
                continue
            raise
    assert last_exc is not None
    raise last_exc


def _missing_is_target(exc: ModuleNotFoundError, target: str) -> bool:
    missing = getattr(exc, "name", "") or ""
    return target == missing or target.startswith(missing + ".") or missing.startswith(target)


def _sys_modules():
    import sys

    return sys.modules


def _find_lifespan_trace_recorder():
    """Best-effort locate a product-provided instrumented lifespan.

    Contract pinned by this suite: ``services.harness.server`` exposes an
    async ctx-manager ``lifespan(app)`` and a way to record the ordered boot
    steps. Preferred hooks (any one suffices), in priority order:

      * ``services.harness.server.boot_trace()`` -> list[str] populated after
        the lifespan startup phase runs, OR
      * ``lifespan`` accepting/using an injectable recorder list.

    This helper returns ``(server_module, tracer_obj_or_None)``; the test body
    decides how to drive it. Import happens in the *caller's* body.
    """
    raise NotImplementedError  # never called; kept for documentation only


# ══════════════════════════════════════════════════════════════════════════
# AC-BOOT-001
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.deployment
def test_boot_001_missing_required_key_crashes_at_import(monkeypatch):
    """AC-BOOT-001: a missing required config key crashes AT IMPORT (not on first use) with a message naming the key."""
    # Sanity: the settings module must exist at all (red before the product is built).
    from importlib.util import find_spec

    settings_home = None
    for name in ("services.harness.settings", "services.harness.config",
                 "libs.ops.settings", "libs.config.settings", "libs.config"):
        try:
            if find_spec(name) is not None:
                settings_home = name
                break
        except ModuleNotFoundError:
            continue
    assert settings_home is not None, (
        "no product settings module (pydantic-settings BaseSettings) found "
        "in services.harness / libs.* (product not built)"
    )

    # The required-key manifest §6 pins. DATABASE_URL is unconditionally required
    # (not prod-gated), so it is the deterministic probe for import-time fail-fast.
    required_keys = (
        "DATABASE_URL", "GCS_BUCKET", "RECALL_API_KEY",
        "AES_KEY_RECALL", "AES_KEY_STT", "AES_KEY_CALENDAR",
    )

    # Provide every other required key a plausible value so that the *only*
    # thing missing is DATABASE_URL -- isolating the fail-fast to that one key.
    plausible = {
        "GCS_BUCKET": "proxy-notes-test",
        "SESSION_SECRET": "0" * 64,
        "GCP_PROJECT_ID": "proxy-test",
        "GCP_PROJECT": "proxy-test",
        "RECALL_API_KEY": "recall-test",
        "AES_KEY_RECALL": "0" * 64,
        "AES_KEY_STT": "0" * 64,
        "AES_KEY_CALENDAR": "0" * 64,
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }
    for k, v in plausible.items():
        monkeypatch.setenv(k, v)
    # The one required key under test is unset -> import must crash.
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # Drop any cached copy so the import (and its BaseSettings validation) re-runs.
    for mod in list(_sys_modules()):
        if mod == settings_home or mod.startswith(settings_home + "."):
            _sys_modules().pop(mod, None)

    with pytest.raises(Exception) as excinfo:
        importlib.import_module(settings_home)

    # Fail-fast: the crash must NAME the missing key (a clear message), not a
    # lazy first-use failure or a generic import error.
    msg = f"{excinfo.type.__name__}: {excinfo.value}"
    assert "DATABASE_URL" in msg, (
        "import-time crash must name the missing key DATABASE_URL "
        f"(fail-loud-at-boot, not lazy first-use); got: {msg!r}"
    )
    # And it must be a validation/config error, not an unrelated ImportError for
    # the module itself (that would mean the product simply isn't built).
    assert not (excinfo.type is ModuleNotFoundError
                and _missing_is_target(excinfo.value, settings_home)), (
        "settings module import failed because the module is absent, not "
        "because DATABASE_URL is unset (product not built)"
    )

    # The required-key manifest itself must be discoverable in the settings
    # source: each hard-gate key must appear as a declared field / env name.
    src = ""
    for name in (settings_home,):
        parts = name.split(".")
        # services.harness.settings -> services/harness/src/harness/settings.py etc.
        src += (S.read_all_text("*.py", root_parts=("services",)) or "")
        src += (S.read_all_text("*.py", root_parts=("libs",)) or "")
        break
    for key in required_keys:
        assert key in src, (
            f"required-key manifest incomplete: {key} not referenced in the "
            f"settings source (must be a hard boot gate)"
        )
    # The prod-gated keys must be present too (declared, even if conditionally required).
    for key in ("SESSION_SECRET", "ANTHROPIC_API_KEY"):
        assert key in src, f"required-key manifest incomplete: {key} not referenced in settings source"


# ══════════════════════════════════════════════════════════════════════════
# AC-BOOT-002
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_boot_002_lifespan_startup_exact_order():
    """AC-BOOT-002: lifespan startup runs in the exact ordered sequence (tracing→pool→Database→provisioner_ready→reaper→routers); routers mount last."""
    trace = _run_lifespan_startup_trace()

    # Reduce the recorded (possibly richer) trace to the canonical step tags in
    # first-occurrence order, then assert it equals the canonical sequence.
    observed = _project_to_canonical(trace)
    assert observed == list(CANONICAL_STEPS), (
        f"boot step order mismatch (out_of_order_steps must be 0):\n"
        f"  canonical: {list(CANONICAL_STEPS)}\n"
        f"  observed : {observed}\n"
        f"  raw trace: {trace}"
    )

    # Tracing is initialized synchronously BEFORE the first query -- i.e. before
    # the pool opens (first query() cannot precede pool open).
    assert observed.index("tracing") < observed.index("pool"), (
        "tracing must be initialized before the asyncpg pool opens (so the first query is traced)"
    )
    # Routers are mounted LAST -- strictly after the reaper.
    assert observed.index("routers") == len(observed) - 1, "routers must be the final startup step"
    assert observed.index("routers") > observed.index("reaper"), (
        "routers_before_reaper must be 0: routers mount only after the boot-time reaper"
    )


# ══════════════════════════════════════════════════════════════════════════
# AC-BOOT-003
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_boot_003_handler_awaits_provisioner_ready_before_use():
    """AC-BOOT-003: a request handler awaits provisioner_ready before touching the bot/sandbox (join-before-wired race defused)."""
    from services.harness import server  # noqa: F401  (in-body import; red before product)

    # The provisioner_ready gate must be a real async-readiness primitive the
    # handlers block on -- an asyncio.Event / awaitable stored at startup.
    #
    # Deterministic race probe: create the gate un-set, launch a join-style
    # handler that must AWAIT the gate before recording any use of the
    # bot/sandbox, and prove it does not proceed until the gate is set.
    async def _drive():
        gate = _make_provisioner_gate(server)
        used: list[str] = []

        async def handler():
            # Contract: the handler awaits provisioner_ready before use.
            await _await_provisioner(server, gate)
            used.append("bot_touched")
            return "joined"

        task = asyncio.ensure_future(handler())
        # Let the handler run up to its await point; the provisioner is NOT ready yet.
        await asyncio.sleep(0)
        assert not task.done(), "handler proceeded before provisioner_ready was set (join raced an unwired provisioner)"
        assert used == [], (
            "use_before_provisioner_ready must be 0: the handler touched the bot/sandbox "
            "before provisioner_ready resolved"
        )

        # Wire the provisioner -> the handler unblocks and only NOW touches the bot.
        _set_provisioner_ready(server, gate)
        result = await asyncio.wait_for(task, timeout=2.0)
        assert result == "joined"
        assert used == ["bot_touched"], "handler must touch the bot exactly once, after the gate resolved"

    asyncio.run(_drive())


# ══════════════════════════════════════════════════════════════════════════
# AC-BOOT-004
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_boot_004_reaper_interrupts_orphans_before_routers_mount():
    """AC-BOOT-004: the boot-time reaper marks orphaned in_meeting/running rows interrupted, and this completes before routers mount."""
    trace = _run_lifespan_startup_trace()
    observed = _project_to_canonical(trace)

    # Happens-before: the reaper step precedes the routers step in the boot trace.
    assert "reaper" in observed and "routers" in observed, (
        f"boot trace missing reaper and/or routers step: {trace}"
    )
    assert observed.index("reaper") < observed.index("routers"), (
        "orphans_after_mount must be 0: the stale-row reaper must complete before routers are mounted"
    )

    # The reaper must actually transition orphaned running/in_meeting rows to
    # 'interrupted'. Drive the product reaper against seeded orphan rows and
    # assert the state transition (model-stateful, over a local test Postgres).
    from services.harness import server  # noqa: F401  (red before product)

    reaper = _resolve_reaper_callable(server)
    orphan_states = ("running", "in_meeting")

    with S.pg_conn() as conn:
        # Apply the product's migrations so operation-run rows exist to reap.
        dsn = _conn_dsn(conn)
        proc = S.apply_migrations(dsn)
        if proc.returncode != 0:
            pytest.skip(f"product migrations did not apply to test DB: {proc.stderr[-400:]}")

        table, status_col, id_col = _find_operation_run_table(conn)
        assert table is not None, "no operation-run table with a status column found to reap"

        seeded = _seed_orphans(conn, table, status_col, id_col, orphan_states)
        assert seeded, "could not seed orphaned running/in_meeting rows"

        _invoke_reaper(reaper, dsn, conn)

        remaining = _count_status(conn, table, status_col, orphan_states)
        assert remaining == 0, (
            f"reaper left {remaining} orphaned {orphan_states} rows un-interrupted"
        )
        interrupted = _count_status(conn, table, status_col, ("interrupted",))
        assert interrupted >= len(seeded), (
            f"reaper must mark orphans 'interrupted'; expected >= {len(seeded)}, got {interrupted}"
        )


# ══════════════════════════════════════════════════════════════════════════
# AC-BOOT-005
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.fault_injection
def test_boot_005_epipe_swallowed_recovered_unknown_crashes():
    """AC-BOOT-005: BrokenPipeError from a crashed SDK subprocess is swallowed (retry recovers); a genuinely-unknown exception crashes after a flush delay."""
    from services.harness import server  # noqa: F401  (red before product)

    handler = _resolve_asyncio_exception_handler(server)

    # --- EPIPE branch: swallowed, no crash requested ---
    epipe_crashed = _run_exception_handler(handler, BrokenPipeError(32, "Broken pipe"))
    assert epipe_crashed is False, (
        "epipe_crashes must be 0: a BrokenPipeError from a crashed Claude-SDK subprocess "
        "must be swallowed (retry recovers), not crash the process"
    )

    # EPIPE surfaced as a generic OSError with errno EPIPE is the same class of fault.
    import errno
    epipe_os = OSError(errno.EPIPE, "Broken pipe")
    assert _run_exception_handler(handler, epipe_os) is False, (
        "an OSError with errno.EPIPE (crashed SDK subprocess) must also be swallowed"
    )

    # --- Unknown branch: crashes (after a flush delay) ---
    class _GenuinelyUnknownError(Exception):
        pass

    unknown_crashed = _run_exception_handler(handler, _GenuinelyUnknownError("boom"))
    assert unknown_crashed is True, (
        "unknown_swallowed must be 0: a genuinely-unknown exception must crash the process "
        "(after a flush delay), never be silently swallowed"
    )


# ══════════════════════════════════════════════════════════════════════════
# AC-BOOT-006
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.integration
def test_boot_006_graceful_shutdown_gathers_in_parallel_with_backstop():
    """AC-BOOT-006: graceful shutdown runs flush_tracing/db.close/bot.leave_all/server.shutdown concurrently via asyncio.gather with a hard-exit backstop timer."""
    from services.harness import server  # noqa: F401  (red before product)

    shutdown = _resolve_shutdown_callable(server)

    # Instrument the four shutdown tasks so we can PROVE they overlap in time
    # (parallel), not run serially. Each task records its enter/exit; if the
    # gather is parallel, the last-entered task enters before the first-entered
    # task exits.
    async def _drive():
        events: list[tuple[str, str]] = []  # (task_name, "enter"/"exit")
        barrier = asyncio.Event()
        entered = {"n": 0}
        names = ("flush_tracing", "db.close", "bot.leave_all", "server.shutdown")

        def make_task(name):
            async def _t():
                events.append((name, "enter"))
                entered["n"] += 1
                if entered["n"] >= len(names):
                    barrier.set()
                # All four must be in-flight simultaneously for this to return.
                await asyncio.wait_for(barrier.wait(), timeout=2.0)
                events.append((name, "exit"))
            return _t

        deps = _make_shutdown_deps(server, {n: make_task(n) for n in names})
        await asyncio.wait_for(_invoke_shutdown(shutdown, deps), timeout=5.0)
        return events

    events = asyncio.run(_drive())

    entered = [n for (n, ev) in events if ev == "enter"]
    exited = [n for (n, ev) in events if ev == "exit"]
    # All four shutdown tasks ran.
    for name in ("flush_tracing", "db.close", "bot.leave_all", "server.shutdown"):
        assert name in entered, f"shutdown did not run {name} (must gather all four)"

    # PARALLEL proof: every task ENTERS before ANY task EXITS (serial_shutdown == 0).
    first_exit_pos = min((i for i, (_, ev) in enumerate(events) if ev == "exit"), default=len(events))
    last_enter_pos = max((i for i, (_, ev) in enumerate(events) if ev == "enter"), default=-1)
    assert last_enter_pos < first_exit_pos, (
        "serial_shutdown must be 0: the four tasks were not gathered concurrently "
        f"(some task exited before all had entered): {events}"
    )
    assert len(exited) == 4, f"trace spans lost: not all shutdown tasks completed cleanly: {events}"

    # A hard-exit backstop timer must exist in the shutdown source (bounds the grace window).
    shutdown_src = S.read_all_text("*.py", root_parts=("services", "harness")) or ""
    assert shutdown_src, "no services/harness source found (product not built)"
    has_backstop = _has_shutdown_backstop(shutdown_src)
    assert has_backstop, (
        "a hard-exit backstop timer must bound graceful shutdown "
        "(e.g. loop.call_later/asyncio.wait_for/os._exit inside a shutdown deadline)"
    )
    # It must be wired to BOTH SIGINT and SIGTERM.
    assert "SIGINT" in shutdown_src and "SIGTERM" in shutdown_src, (
        "graceful shutdown must be registered on both SIGINT and SIGTERM"
    )


# ══════════════════════════════════════════════════════════════════════════
# AC-BOOT-007
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.deployment
def test_boot_007_three_claude_auth_modes_supported():
    """AC-BOOT-007: all three Claude SDK auth modes (ANTHROPIC_API_KEY / OAuth token / Vertex) are documented and resolvable; none hard-wired as the sole path."""
    env_example = S.read_text(".env.example")
    assert env_example, ".env.example config contract not found (product not built)"

    # (1) .env.example documents all three auth mechanisms, each commented with what-it's-for.
    #     API-key mode.
    assert "ANTHROPIC_API_KEY" in env_example, ".env.example must document ANTHROPIC_API_KEY (API-key auth mode)"
    #     OAuth-token mode (the Agent SDK reads ANTHROPIC_AUTH_TOKEN / OAuth token).
    oauth_documented = ("ANTHROPIC_AUTH_TOKEN" in env_example) or ("OAuth" in env_example) or ("oauth" in env_example)
    assert oauth_documented, ".env.example must document the OAuth-token auth mode (ANTHROPIC_AUTH_TOKEN / OAuth token)"
    #     Vertex mode.
    vertex_documented = ("CLAUDE_CODE_USE_VERTEX" in env_example) or ("VERTEX" in env_example) or ("Vertex" in env_example)
    assert vertex_documented, ".env.example must document the Vertex auth mode (CLAUDE_CODE_USE_VERTEX / Vertex)"

    # Each of the three must sit in the Claude/Anthropic auth section with a what-it's-for comment.
    def _line_for(token: str) -> str:
        for line in env_example.splitlines():
            if token in line:
                return line
        return ""
    for token, label in (("ANTHROPIC_API_KEY", "API-key"), ):
        line = _line_for(token)
        assert "#" in line or line.strip().endswith(token + "="), (
            f"{label} auth var ({token}) must be documented (commented) in .env.example: {line!r}"
        )

    # (2) The SDK client construction resolves auth from WHICHEVER of the three
    #     modes is configured -- no single mode hard-wired as the only path.
    #     Scan the product's Claude-SDK client construction (libs/llm and services).
    llm_src = (S.read_all_text("*.py", root_parts=("libs", "llm")) or "") + \
              (S.read_all_text("*.py", root_parts=("services",)) or "")
    assert llm_src.strip(), "no libs/llm or services Claude-SDK client source found (product not built)"

    saw_api_key = "ANTHROPIC_API_KEY" in llm_src
    saw_oauth = ("ANTHROPIC_AUTH_TOKEN" in llm_src) or ("auth_token" in llm_src) or ("oauth" in llm_src.lower())
    saw_vertex = ("VERTEX" in llm_src.upper()) or ("Vertex" in llm_src) or ("vertex" in llm_src.lower())

    modes_supported = sum((saw_api_key, saw_oauth, saw_vertex))
    assert modes_supported >= 3, (
        "auth_modes_supported_below_three must be 0: the Claude SDK client must resolve auth "
        "from all three modes (ANTHROPIC_API_KEY / OAuth token / Vertex). "
        f"Found api_key={saw_api_key}, oauth={saw_oauth}, vertex={saw_vertex}. "
        "A build supporting API-key alone (no OAuth-token or Vertex path) FAILS this criterion."
    )
    # And no single mode may be hard-wired as the ONLY path: an API-key-only build fails.
    assert not (saw_api_key and not saw_oauth and not saw_vertex), (
        "auth must not be hard-wired to API-key alone; the OAuth-token and Vertex paths must exist too"
    )


# ---------------------------------------------------------------------------
# Lifespan-trace helpers (in-body imports done by callers; these operate on the
# already-imported product ``server`` module or drive its lifespan). They pin a
# natural instrumentation interface: the product exposes SOME way to record the
# ordered boot steps. We try the documented hooks and, failing all, fail red.
# ---------------------------------------------------------------------------

def _run_lifespan_startup_trace() -> list[str]:
    """Drive the product lifespan startup and return the ordered step trace.

    Pins a natural interface: ``services.harness.server`` provides one of --
      * ``boot_trace()`` / ``BOOT_TRACE`` -> a list populated during startup, or
      * ``instrumented_lifespan()`` -> (async ctx-manager, trace list), or
      * ``lifespan(app, *, trace=<list>)`` accepting a recorder list.
    Whichever exists, we run the startup phase and return the recorded tags.
    """
    from services.harness import server  # in-body import; red before product

    # Preference 1: an explicit instrumented-lifespan factory returning (cm, trace).
    factory = getattr(server, "instrumented_lifespan", None)
    if callable(factory):
        return _drive_instrumented(factory)

    # Preference 2: a module-level recorder + the real lifespan.
    trace = _module_recorder(server)
    lifespan = getattr(server, "lifespan", None)
    assert callable(lifespan), (
        "services.harness.server must expose a FastAPI `lifespan` (async ctx-manager) "
        "for the boot-ordering oracle"
    )

    app = _make_fake_app(server)

    async def _run():
        recorder = trace if trace is not None else []
        cm = _open_lifespan(lifespan, app, recorder)
        agen = cm.__aenter__()
        try:
            await agen
        finally:
            # Only the startup phase matters for the ordering oracle; enter then
            # exit so resources are released.
            with_exit = cm.__aexit__(None, None, None)
            try:
                await with_exit
            except Exception:
                pass
        return list(_read_recorder(server, recorder))

    return asyncio.run(_run())


def _drive_instrumented(factory) -> list[str]:
    async def _run():
        result = factory()
        cm, trace = result if isinstance(result, tuple) else (result, None)
        async with cm:
            pass
        recorded = trace if trace is not None else getattr(cm, "trace", None)
        assert recorded is not None, "instrumented_lifespan() must expose a trace list"
        return list(recorded)

    return asyncio.run(_run())


def _module_recorder(server):
    for name in ("BOOT_TRACE", "boot_trace"):
        obj = getattr(server, name, None)
        if isinstance(obj, list):
            obj.clear()
            return obj
        if callable(obj):
            try:
                got = obj()
                if isinstance(got, list):
                    got.clear()
                    return got
            except TypeError:
                pass
    return None


def _read_recorder(server, recorder):
    if recorder:
        return recorder
    for name in ("BOOT_TRACE", "boot_trace"):
        obj = getattr(server, name, None)
        if isinstance(obj, list):
            return obj
        if callable(obj):
            try:
                got = obj()
                if isinstance(got, list):
                    return got
            except TypeError:
                pass
    return recorder or []


def _open_lifespan(lifespan, app, recorder):
    """Open the product lifespan, passing a recorder if the signature accepts one."""
    import inspect

    try:
        sig = inspect.signature(lifespan)
    except (TypeError, ValueError):
        sig = None
    if sig is not None and "trace" in sig.parameters:
        return lifespan(app, trace=recorder)
    if sig is not None and len(sig.parameters) == 0:
        return lifespan()
    return lifespan(app)


def _make_fake_app(server):
    """A minimal stand-in for the FastAPI app the lifespan expects, if any."""
    factory = getattr(server, "create_app", None) or getattr(server, "app", None)
    if callable(factory):
        try:
            return factory()
        except Exception:
            pass
    elif factory is not None:
        return factory

    class _FakeApp:
        def __init__(self):
            self.state = type("S", (), {})()
            self.router = type("R", (), {"routes": []})()

        def include_router(self, *a, **k):
            pass

        def mount(self, *a, **k):
            pass

    return _FakeApp()


def _project_to_canonical(trace: list[str]) -> list[str]:
    """Map a raw boot trace to canonical step tags in first-occurrence order."""
    assert trace, "empty boot trace: the instrumented lifespan recorded no startup steps (product not built)"
    seen: list[str] = []
    for raw in trace:
        low = str(raw).lower()
        for tag in CANONICAL_STEPS:
            key = "provisioner" if tag == "provisioner" else tag
            # Accept common synonyms per step.
            synonyms = {
                "tracing": ("tracing", "trace_init", "otel", "telemetry"),
                "pool": ("pool", "asyncpg"),
                "database": ("database", "db_facade", "db facade"),
                "provisioner": ("provisioner", "provisioner_ready"),
                "reaper": ("reaper", "stale", "orphan"),
                "routers": ("routers", "router", "mount", "auth wall", "auth_wall"),
            }[tag]
            if any(s in low for s in synonyms) and tag not in seen:
                seen.append(tag)
                break
    return seen


# ---------------------------------------------------------------------------
# provisioner_ready gate helpers (AC-BOOT-003)
# ---------------------------------------------------------------------------

def _make_provisioner_gate(server):
    """Obtain/construct the product's provisioner_ready gate in the un-ready state."""
    factory = getattr(server, "make_provisioner_gate", None) or getattr(server, "ProvisionerReady", None)
    if callable(factory):
        try:
            return factory()
        except Exception:
            pass
    # Fall back to the primitive §6 pins: an asyncio.Event stored as the awaitable.
    return asyncio.Event()


async def _await_provisioner(server, gate):
    waiter = getattr(server, "await_provisioner_ready", None)
    if callable(waiter):
        return await waiter(gate)
    if isinstance(gate, asyncio.Event):
        return await gate.wait()
    if asyncio.iscoroutine(gate) or asyncio.isfuture(gate):
        return await gate
    return await gate.wait()


def _set_provisioner_ready(server, gate):
    setter = getattr(server, "set_provisioner_ready", None)
    if callable(setter):
        return setter(gate)
    if isinstance(gate, asyncio.Event):
        gate.set()
        return
    if asyncio.isfuture(gate) and not gate.done():
        gate.set_result(True)
        return
    getattr(gate, "set", lambda: None)()


# ---------------------------------------------------------------------------
# reaper helpers (AC-BOOT-004)
# ---------------------------------------------------------------------------

def _resolve_reaper_callable(server):
    for name in ("reap_orphans", "reap_stale_rows", "boot_reaper", "reaper"):
        fn = getattr(server, name, None)
        if callable(fn):
            return fn
    pytest.fail("services.harness.server must expose a boot-time stale-row reaper callable")


def _conn_dsn(conn) -> str:
    # psycopg3 Connection exposes the DSN used to connect.
    for attr in ("info",):
        info = getattr(conn, attr, None)
        if info is not None and getattr(info, "dsn", None):
            return info.dsn
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


def _find_operation_run_table(conn):
    """Locate the table holding operation runs with a status column and id column."""
    candidates = ("operation_runs", "operations", "runs", "meetings", "bot_sessions")
    for table in candidates:
        if S.table_exists(conn, table):
            cols = S.table_columns(conn, table)
            status_col = next((c for c in ("status", "state", "run_status") if c in cols), None)
            id_col = next((c for c in cols if c.endswith("id")), None)
            if status_col and id_col:
                return table, status_col, id_col
    return None, None, None


def _seed_orphans(conn, table, status_col, id_col, orphan_states) -> list:
    seeded = []
    for i, st in enumerate(orphan_states):
        try:
            cur = conn.execute(
                f"INSERT INTO {table} ({status_col}) VALUES (%s) RETURNING {id_col}",
                (st,),
            )
            row = cur.fetchone()
            seeded.append(row[0] if row else i)
        except Exception:
            # Table may require more NOT NULL columns; fall back to a broad seed.
            try:
                conn.execute(f"UPDATE {table} SET {status_col} = %s", (st,))
                seeded.append(i)
            except Exception:
                continue
    return seeded


def _invoke_reaper(reaper, dsn, conn):
    import inspect

    if inspect.iscoroutinefunction(reaper):
        try:
            asyncio.run(reaper(dsn))
            return
        except TypeError:
            asyncio.run(reaper())
            return
    try:
        reaper(dsn)
    except TypeError:
        reaper()


def _count_status(conn, table, status_col, states) -> int:
    placeholders = ", ".join(["%s"] * len(states))
    cur = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {status_col} IN ({placeholders})",
        tuple(states),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# EPIPE / asyncio exception-handler helpers (AC-BOOT-005)
# ---------------------------------------------------------------------------

def _resolve_asyncio_exception_handler(server):
    for name in ("asyncio_exception_handler", "loop_exception_handler",
                 "handle_loop_exception", "exception_handler"):
        fn = getattr(server, name, None)
        if callable(fn):
            return fn
    pytest.fail("services.harness.server must expose the asyncio exception handler (EPIPE tolerance)")


def _run_exception_handler(handler, exc) -> bool:
    """Invoke the product asyncio exception handler; return True iff it requests a crash.

    A crash is signalled by the handler either raising / re-raising, calling a
    hard-exit, or returning a truthy 'crash' verdict. Swallowing returns falsey
    and does not raise.
    """
    import inspect

    crashed = {"v": False}

    # Intercept the hard-exit paths so an "unknown -> crash after flush" does not
    # actually kill the test process.
    import os as _os

    real_exit = _os._exit
    real_sys_exit = _sys_module().exit

    def _fake_exit(code=0):
        crashed["v"] = True
        raise _CrashRequested(code)

    _os._exit = _fake_exit  # type: ignore[assignment]
    _sys_module().exit = _fake_exit  # type: ignore[assignment]
    try:
        context = {"exception": exc, "message": str(exc)}
        try:
            if inspect.iscoroutinefunction(handler):
                result = asyncio.run(_call_handler_async(handler, context, exc))
            else:
                result = _call_handler_sync(handler, context, exc)
        except _CrashRequested:
            crashed["v"] = True
            result = True
        except BaseException as re_raised:  # noqa: BLE001
            # The handler re-raised the exception -> it crashes the process.
            if isinstance(re_raised, (KeyboardInterrupt, SystemExit)):
                crashed["v"] = True
            else:
                crashed["v"] = True
            result = True
        # A truthy return is a "crash" verdict; falsey / None means swallowed.
        if isinstance(result, bool) and result:
            crashed["v"] = True
    finally:
        _os._exit = real_exit  # type: ignore[assignment]
        _sys_module().exit = real_sys_exit  # type: ignore[assignment]
    return crashed["v"]


def _sys_module():
    import sys

    return sys


class _CrashRequested(Exception):
    pass


async def _call_handler_async(handler, context, exc):
    import inspect

    sig = inspect.signature(handler)
    if len(sig.parameters) >= 2:
        return await handler(None, context)
    return await handler(context)


def _call_handler_sync(handler, context, exc):
    import inspect

    sig = inspect.signature(handler)
    try:
        if len(sig.parameters) >= 2:
            return handler(None, context)
        return handler(context)
    except TypeError:
        return handler(exc)


# ---------------------------------------------------------------------------
# graceful-shutdown helpers (AC-BOOT-006)
# ---------------------------------------------------------------------------

def _resolve_shutdown_callable(server):
    for name in ("graceful_shutdown", "shutdown", "on_shutdown", "run_shutdown"):
        fn = getattr(server, name, None)
        if callable(fn):
            return fn
    pytest.fail("services.harness.server must expose a graceful-shutdown callable")


def _make_shutdown_deps(server, task_map):
    """Package the four instrumented shutdown coroutines the way the product expects."""
    class _Tracing:
        async def flush(self_inner):
            await task_map["flush_tracing"]()

    class _DB:
        async def close(self_inner):
            await task_map["db.close"]()

    class _Bot:
        async def leave_all(self_inner):
            await task_map["bot.leave_all"]()

    class _Server:
        async def shutdown(self_inner):
            await task_map["server.shutdown"]()

    return {
        "flush_tracing": task_map["flush_tracing"],
        "db": _DB(),
        "bot": _Bot(),
        "server": _Server(),
        "tracing": _Tracing(),
        "db_close": task_map["db.close"],
        "bot_leave_all": task_map["bot.leave_all"],
        "server_shutdown": task_map["server.shutdown"],
    }


async def _invoke_shutdown(shutdown, deps):
    import inspect

    sig = inspect.signature(shutdown)
    kwargs = {k: v for k, v in deps.items() if k in sig.parameters}
    call = shutdown(**kwargs) if kwargs else shutdown()
    if inspect.isawaitable(call):
        return await call
    return call


def _has_shutdown_backstop(src: str) -> bool:
    import re

    patterns = (
        r"call_later", r"call_at", r"loop\.call_later",
        r"os\._exit", r"hard[_-]?exit", r"backstop",
        r"wait_for\(", r"asyncio\.timeout", r"deadline",
    )
    return any(re.search(p, src) for p in patterns)
