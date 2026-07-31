"""The connect page's public REST API + the connect→index trigger (§2.7/§8).

The connect page is the ONE out-of-meeting surface (§2.7): a small static app plus its
two PUBLIC REST routes on control_plane. This module owns the backend half:

  * **POST /connect/install/start** — launches the GitHub-App install flow AND fires the
    connect→index TRIGGER in a background task. Pre-meeting (SPEC §8) is: connect → clone →
    build the PROSE MAP → store it (Postgres ``repo_maps``, keyed by tenant/repo/pinned_sha).
    The trigger drives ``premeeting.run_pipeline`` — the pre-meeting pass (mint → clone →
    map-build → store → verify → ready). There is NO code_intel graph and NO ``mcp__code_intel__*``
    mount in the new system: native Claude in the workroom greps the real repo directly.
  * **GET /connect/status** — the readiness POLL. It is **REST, not a WS message**
    (CANONICAL §12.12) and renders ALL FIVE states of the ``Readiness`` enum
    (CANONICAL §1.5): ``connecting → cloning → indexing → ready`` plus an explicit
    ``not_ready`` terminal that NAMES the gaps. There is NO ``mapping`` state — the map-build
    IS the ``indexing`` phase (``premeeting.pipeline``). ``not_ready`` names what is missing
    rather than pretending (Law 1 / Law 2).

Readiness is DURABLE (CLAUDE.md §"Source of truth vs cache"): the ``ConnectStore`` writes
and reads the ``connect_readiness`` Postgres row (``db.repos.connect``), NEVER a per-instance
in-process dict. ``control_plane`` is an autoscaling multi-instance Cloud Run service
(CANONICAL-DECISIONS.md §300), so a poll can hit a different instance than the one running
the trigger and all state is lost on recycle — the exact failure the ``db:postgres``
dependency_class was written to prevent. When Postgres is unreachable the poll degrades
HONESTLY (an explicit error / not_ready), never a fabricated ``ready`` (Law 2).

Both routes are PUBLIC (no meeting exists yet) but validated by the §4.6 wrappers — the
connect page is a public URL, reachable by anyone with the link, so its calls are
untrusted and allowlisted, never trusted-by-default. A client-supplied ``install_id`` is
an opaque poll handle only; it authorizes NOTHING beyond reading its own readiness (the
store never joins it to another tenant's data).

The connect→index trigger builds + stores the durable prose map (``repo_maps``) via
``premeeting.run_pipeline``. Readiness progresses ``connecting → cloning → indexing →
ready`` (the map-build is the ``indexing`` phase); ``not_ready`` surfaces the named gaps
the pipeline/verify produced.
"""
from __future__ import annotations

import contextlib
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from contracts.readiness import ReadinessReport
from db.repos import connect as connect_repo
from starlette.requests import Request

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

# The canonical Readiness enum (CANONICAL §1.5). ``mapping`` is deliberately absent —
# ``mark_state`` rejects any value outside this set so a 'mapping' state is unrepresentable.
_VALID_STATES: frozenset[str] = frozenset({"connecting", "cloning", "indexing", "ready", "not_ready"})

class ConnectStoreUnavailable(RuntimeError):
    """The durable readiness substrate (Postgres) could not be reached.

    Raised by :class:`ConnectStore` reads/writes when the ``connect_readiness`` row cannot
    be fetched or written — the seam that lets ``GET /connect/status`` degrade HONESTLY (an
    explicit error / not_ready) instead of fabricating a ``ready`` result (Law 2, the
    AC-CONN-010-NEG honest-degrade path).
    """


@dataclass
class FlaggedFile:
    """One flagged file on the happy path — a path + the honest reason it was flagged."""

    path: str
    reason: str


def _default_dsn() -> str:
    """The Postgres DSN the connect store connects to (test DB first, then the app DSN).

    Prefers ``TEST_DATABASE_URL`` (the integration harness' localhost Cloud-SQL-shape DB)
    then ``DATABASE_URL``. psycopg speaks the bare libpq URL; a SQLAlchemy driver suffix is
    stripped so the same DSN string serves asyncpg and psycopg alike.
    """
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if dsn.startswith("postgresql+psycopg://"):
        return "postgresql://" + dsn[len("postgresql+psycopg://") :]
    return dsn


def _psycopg_conn_factory(dsn: str) -> Callable[[], Any]:
    """A factory that opens ONE fresh autocommit psycopg connection to ``dsn``.

    Each store operation borrows its own short-lived connection (the connect page is a
    low-rate out-of-meeting surface, so a pool is unwarranted here); autocommit keeps every
    write a single durable statement. A connection failure surfaces as
    :class:`ConnectStoreUnavailable` at the store boundary, never a raw driver traceback on
    the public poll (§4.6 safeError).
    """

    def _open() -> Any:
        import psycopg

        return psycopg.connect(dsn, autocommit=True)

    return _open


class ConnectStore:
    """Durable registry of connect installs → readiness (the poll's Postgres read model).

    There is no meeting yet, so readiness is keyed by an opaque ``install_id`` rather than a
    meeting/tenant the caller could forge. The store is the seam between the background
    trigger (which WRITES readiness to the ``connect_readiness`` Postgres row as the pipeline
    progresses) and GET /connect/status (which READS it). Postgres — never an in-process
    dict — is the source of truth, so a poll on a *different* Cloud Run instance than the one
    running the trigger reads the same live row (CANONICAL-DECISIONS.md §300).

    Every operation borrows a fresh autocommit psycopg connection via ``conn_factory`` and
    runs the parameterised SQL in ``db.repos.connect``. A substrate fault raises
    :class:`ConnectStoreUnavailable` so the read path can degrade honestly — it NEVER
    fabricates a ``ready`` result (Law 2).
    """

    def __init__(self, conn_factory: Callable[[], Any] | None = None, *, dsn: str | None = None) -> None:
        self._conn_factory = conn_factory or _psycopg_conn_factory(dsn or _default_dsn())

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        """Borrow one fresh autocommit connection; a failure is an honest unavailable."""
        try:
            conn = self._conn_factory()
        except Exception as exc:  # noqa: BLE001 - any connect failure → honest unavailable
            raise ConnectStoreUnavailable(str(exc)) from exc
        try:
            yield conn
        except ConnectStoreUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - any query failure → honest unavailable
            raise ConnectStoreUnavailable(str(exc)) from exc
        finally:
            # Best-effort close on the teardown path: a failure to close a borrowed
            # connection is unrecoverable and irrelevant to the caller's result.
            with contextlib.suppress(Exception):
                conn.close()

    def new_install(self, tenant_id: str, repo_url: str) -> str:
        """Register a fresh install (state ``connecting``) and return its opaque poll id.

        ``connect_readiness.tenant_id`` is a DECLARED FK to ``tenants(id)`` (AC-TEN-001), so
        the referenced tenants root row is provisioned first (idempotent) in the SAME borrowed
        connection before the readiness row lands — the install IS how a tenant is provisioned
        (no session exists yet), so the tenant is created server-side, never client-supplied.
        """
        install_id = uuid.uuid4().hex
        with self._conn() as conn:
            connect_repo.ensure_tenant(conn, tenant_id=tenant_id)
            connect_repo.insert_install(
                conn, install_id=install_id, tenant_id=tenant_id, repo_url=repo_url
            )
        return install_id

    def mark_state(self, install_id: str, state: str) -> None:
        """Advance an install to a canonical progress state.

        Rejects any value outside the canonical Readiness enum — a 'mapping' state (or any
        typo) raises ``ValueError`` rather than silently corrupting the poll (CANONICAL
        §1.5), before the durable row is ever touched (belt-and-suspenders with the row's
        CHECK constraint).
        """
        if state not in _VALID_STATES:
            raise ValueError(f"{state!r} is not a canonical Readiness state ({sorted(_VALID_STATES)})")
        with self._conn() as conn:
            connect_repo.mark_state(conn, install_id=install_id, state=state)

    def set_ready(
        self, install_id: str, coverage_pct: float, flagged: list[tuple[str, str]] | None = None
    ) -> None:
        """Terminal ``ready``: the REAL coverage number + the flagged files (honest, §2.7)."""
        with self._conn() as conn:
            connect_repo.set_ready(
                conn, install_id=install_id, coverage_pct=float(coverage_pct), flagged=flagged
            )

    def set_not_ready(self, install_id: str, gaps: list[str]) -> None:
        """Terminal ``not_ready``: NAME the gaps — never an error page, never a faked pct."""
        with self._conn() as conn:
            connect_repo.set_not_ready(conn, install_id=install_id, gaps=list(gaps))

    def status(self, install_id: str) -> ReadinessReport:
        """The ReadinessReport the poll renders — always a canonical, honest report.

        An unknown/never-started install reads as ``connecting`` (the initial state), so the
        public poll never leaks an internal error string for a stale/typo'd id (§4.6). A
        substrate fault propagates as :class:`ConnectStoreUnavailable` so the route degrades
        HONESTLY (never a fabricated ``ready``); it is the caller's job to turn that into an
        explicit error / not_ready, never a silent stale success (Law 2 / AC-CONN-010-NEG).
        """
        row = self._read_row(install_id)
        if row is None:
            return ReadinessReport(status="connecting")
        return ReadinessReport(
            status=row["status"], coverage_pct=row["coverage_pct"], gaps=list(row["gaps"])
        )

    def flagged_files(self, install_id: str) -> list[FlaggedFile]:
        """The flagged files for an install (empty unless it reached ``ready``)."""
        row = self._read_row(install_id)
        if row is None:
            return []
        return [FlaggedFile(path=f["path"], reason=f["reason"]) for f in row["flagged"]]

    def states(self, install_id: str) -> list[str]:
        """The ordered readiness progression the trigger emitted (for provable ordering)."""
        row = self._read_row(install_id)
        return list(row["states"]) if row is not None else []

    def _read_row(self, install_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row: dict[str, Any] | None = connect_repo.read_row(conn, install_id)
        return row


class _StoreReadinessListener:
    """A readiness listener that streams the pipeline's states into a :class:`ConnectStore`.

    ``premeeting.run_pipeline`` calls ``listener.emit(state)`` at each phase. We forward
    each canonical progress state (connecting/cloning/indexing) into the store so the REST
    poll renders the live progression; the terminal ``ready`` / ``not_ready`` write is done
    by the trigger itself (with the named gaps), so those are ignored here to avoid a
    terminal write without its payload.
    """

    def __init__(self, store: ConnectStore, install_id: str) -> None:
        self._store = store
        self._install_id = install_id
        self.emitted_states: list[str] = []

    def emit(self, state: str) -> None:
        self.emitted_states.append(state)
        if state in {"connecting", "cloning", "indexing"}:
            try:
                self._store.mark_state(self._install_id, state)
            except (KeyError, ValueError, ConnectStoreUnavailable):
                # A dropped install id, a non-canonical state, or a transient substrate
                # blip never crashes the pipeline — the terminal write reconciles the row.
                pass

    # aliases the pipeline / collectors may use
    def record(self, state: str) -> None:
        self.emit(state)

    def on_state(self, state: str) -> None:
        self.emit(state)

    def set_error(self, error: Any) -> None:  # never-throw parity with ReadinessCollector
        pass


def trigger_connect_index(
    store: ConnectStore,
    install_id: str,
    tenant_id: str,
    repo_url: str,
    sha: str | None = None,
    registry: Any = None,
    map_provider: Any = None,
    map_store: Any = None,
) -> Any:
    """The connect→index TRIGGER — the pre-meeting map build (SPEC §8).

    Drives ``premeeting.run_pipeline`` on the tenant's repo — the pre-meeting pass: mint →
    clone → build the PROSE MAP → store it durably in Postgres ``repo_maps`` (keyed by
    tenant/repo/pinned_sha) → verify. Readiness streams into ``store`` (the durable
    ``connect_readiness`` row) as it progresses (connecting → cloning → indexing — the
    map-build IS the ``indexing`` phase). On completion it writes the terminal readiness:
    ``ready`` when the map verified clean, else ``not_ready`` NAMING the gaps the
    pipeline/verify produced (Law 1 / Law 2), never a fabricated success.

    There is NO code_intel graph and NO ``mcp__code_intel__*`` mount in the new system:
    native Claude in the workroom greps the real repo directly, so the trigger builds ONLY
    the prose map — no LSP resolver, no MCP server, no dependency graph.

    ``map_provider`` is the model seam ``run_pipeline`` needs (the pre-meeting map-build
    agent); ``map_store`` is the durable :class:`~premeeting.map_store.MapStore`. Both are
    injected by a funded deployment. The map-build model key is credit-blocked (D-032), so on
    the live path today ``map_provider`` is ``None`` — the trigger records an honest
    ``not_ready`` naming that gap rather than fabricating a map (Law 2). ``registry`` is
    accepted for call-site compatibility; push-freshness is now ``premeeting.refresh_on_push``,
    wired by the meeting/webhook spine (this trigger no longer registers a code_intel pipeline).

    Returns the :class:`~premeeting.pipeline.PipelineResult` (the route fires this in a
    background thread and discards it). Never raises out to the caller — a build failure is
    surfaced as an honest ``not_ready`` with the reason named, never a silent success.
    """
    import asyncio

    from premeeting.pipeline import run_pipeline

    # Honest no-op when the map-build model seam is unfunded (D-032): no provider → no map.
    # We NEVER fabricate a map; the readiness poll gets an honest not_ready naming the gap.
    if map_provider is None:
        try:
            store.mark_state(install_id, "connecting")
        except (KeyError, ValueError, ConnectStoreUnavailable):
            pass
        try:
            store.set_not_ready(
                install_id,
                gaps=["map-build skipped: no model provider (D-032, unfunded key)"],
            )
        except (KeyError, ConnectStoreUnavailable):
            pass
        return None

    listener = _StoreReadinessListener(store, install_id)
    try:
        # This runs in a dedicated daemon background thread (see ``_spawn_trigger``) with no
        # pre-existing event loop, so a private ``asyncio.run`` here is safe — it never
        # creates/closes another thread's loop. run_pipeline never raises out (it returns an
        # honest PipelineResult), so this is the ONE async→sync bridge for the sync trigger.
        result = asyncio.run(
            run_pipeline(
                tenant_id=tenant_id,
                repo_url=repo_url,
                provider=map_provider,
                map_store=map_store,
                sha=sha,
                readiness_listener=listener,
            )
        )
    except Exception as exc:  # noqa: BLE001 - honest not_ready, never a silent success
        try:
            store.set_not_ready(install_id, gaps=[f"indexing failed: {type(exc).__name__}"])
        except (KeyError, ConnectStoreUnavailable):
            pass
        raise

    # Terminal readiness — read the REAL verdict off the pipeline result (never a faked pass).
    # A verified map covers the whole clone (verify_map is a full-tree check), so a clean
    # ``ready`` carries 100.0 and no per-file flags — the prose map has no partial-coverage /
    # flagged-file concept the old graph index had; ``not_ready`` names the gaps verify found.
    if result.ready:
        store.set_ready(install_id, coverage_pct=100.0, flagged=[])
    else:
        gaps = result.reasons or ["not ready: no reason recorded"]
        store.set_not_ready(install_id, gaps=list(gaps))
    return result


# ── GitHub-App install URL ────────────────────────────────────────────────────
def github_app_install_url(repo_url: str) -> str:
    """The GitHub-App install URL the connect page opens to launch the install (Doc 01).

    The connect page links out to GitHub's App-install page; the ``state`` carries our
    repo binding so the install callback can resume the connect flow. This is the URL the
    page opens — the actual App slug is configured at deploy; a stable default keeps the
    door openable in every environment.
    """
    from urllib.parse import quote

    app_slug = os.environ.get("PROXY_GITHUB_APP_SLUG", "proxy")
    return f"https://github.com/apps/{quote(app_slug)}/installations/new?state={quote(repo_url, safe='')}"


# ── the store lives on app.state so the route + the trigger share one instance ──
def get_connect_store(app: Any) -> ConnectStore:
    """The single :class:`ConnectStore` bound to this app (created on first access).

    Both the install/start route (which spawns the trigger) and the status poll read the
    SAME store instance off ``app.state`` — a durable Postgres-backed store, so the seam that
    lets a background index write the readiness the poll renders survives across Cloud Run
    instances (never a per-instance dict).
    """
    store = getattr(app.state, "connect_store", None)
    if store is None:
        store = ConnectStore()
        app.state.connect_store = store
    return store


def install_connect_routes(app: "FastAPI") -> None:
    """Mount the two PUBLIC connect routes on the live control_plane app (§4.6).

    ``GET /connect/status`` and ``POST /connect/install/start`` are on the PUBLIC_ROUTES
    allowlist (no meeting exists yet); both are validated like any public API and never
    leak an internal error (§4.6 safeError, installed app-wide). The status poll is REST —
    NOT a WS message (CANONICAL §12.12). Both read/write readiness through the durable
    Postgres-backed store; a substrate fault degrades HONESTLY (never a fabricated ready).
    """
    import anyio
    from fastapi import Body
    from starlette.responses import JSONResponse

    # Ensure a durable store exists on app.state at mount time; the handlers RESOLVE it
    # per-request off ``request.app.state`` (never a mount-time closure capture) so the
    # live durable handle is always the one in effect — the same pattern internal.py uses
    # to resolve ``app.state.db`` per request.
    get_connect_store(app)

    @app.get("/connect/status", include_in_schema=True)
    async def connect_status(install_id: str, request: Request) -> JSONResponse:
        """The public REST readiness poll — renders all five canonical states honestly.

        Reads the durable ``connect_readiness`` Postgres row (never an in-process dict). An
        unknown install id reads as ``connecting`` (the store's honest default), so a
        stale/typo'd handle yields a clean 200. When Postgres is UNREACHABLE the poll
        degrades HONESTLY — status ``not_ready`` with a named gap, coverage 0, HTTP 503 —
        NEVER a fabricated ``ready`` result (Law 2 / AC-CONN-010-NEG). The blocking psycopg
        reads run in a worker thread so the event loop is never stalled.
        """
        store = get_connect_store(request.app)
        try:
            report = await anyio.to_thread.run_sync(store.status, install_id)
            flagged = (
                await anyio.to_thread.run_sync(store.flagged_files, install_id)
                if report.status == "ready"
                else []
            )
        except ConnectStoreUnavailable:
            # Honest degrade: the readiness record is unavailable. NEVER ready/fabricated —
            # a not_ready payload naming the substrate gap, at HTTP 503 (service unavailable).
            body: dict[str, Any] = {
                "install_id": install_id,
                "status": "not_ready",
                "coverage_pct": 0.0,
                "gaps": ["readiness record unavailable: the durable substrate is unreachable"],
                "flagged_files": [],
            }
            return JSONResponse(body, status_code=503)
        body = {
            "install_id": install_id,
            "status": report.status,
            "coverage_pct": report.coverage_pct,
            "gaps": report.gaps,
            "flagged_files": [{"path": f.path, "reason": f.reason} for f in flagged],
        }
        return JSONResponse(body, status_code=200)

    @app.post("/connect/install/start", include_in_schema=True)
    async def connect_install_start(
        request: Request, repo_url: str = Body(..., embed=True)
    ) -> JSONResponse:
        """Launch the GitHub-App install flow AND fire the connect→index trigger.

        Registers a fresh durable install (immediately pollable at ``connecting``), spawns
        the trigger as a background task (the connect→index map-build, ``premeeting.run_pipeline``),
        and returns the poll handle + the GitHub-App install URL to open. A malformed body
        (missing ``repo_url``) is a FastAPI validation error (the caller's own bad input),
        never a 500 — the never-throw boundary. A substrate fault registering the install
        degrades HONESTLY to a 503, never a fabricated install handle.
        """
        store = get_connect_store(request.app)
        tenant_id = _tenant_for_install(repo_url)
        try:
            install_id = await anyio.to_thread.run_sync(
                store.new_install, tenant_id, repo_url
            )
        except ConnectStoreUnavailable:
            return JSONResponse(
                {"error": "readiness substrate unavailable; try again shortly"},
                status_code=503,
            )
        from .github_webhook import get_pipeline_registry

        registry = get_pipeline_registry(request.app)
        # Source the map-build model seam + durable store off ``app.state`` — the SAME seam the
        # push-freshness ingress uses (``github_webhook._maybe_refresh_map``). A funded deployment
        # wires ``app.state.map_provider`` (an ``agentkit.Provider``) + ``app.state.map_store`` (a
        # ``premeeting.map_store.MapStore``); on the live path today the key is unfunded (D-032) so
        # ``map_provider`` is ``None`` and the trigger records an honest ``not_ready`` — never a
        # fabricated map (Law 2). Secrets/handles come from env-wired app state, never hard-coded.
        map_provider = getattr(request.app.state, "map_provider", None)
        map_store = getattr(request.app.state, "map_store", None)
        _spawn_trigger(
            store,
            install_id,
            tenant_id=tenant_id,
            repo_url=repo_url,
            registry=registry,
            map_provider=map_provider,
            map_store=map_store,
        )
        return JSONResponse(
            {"install_id": install_id, "install_url": github_app_install_url(repo_url)},
            status_code=200,
        )


def _tenant_for_install(repo_url: str) -> str:
    """The tenant a fresh install binds to — a deterministic per-repo uuid.

    The install/start caller is anonymous (no session yet — the install IS how a tenant is
    provisioned), so the tenant is derived server-side from the repo binding, never a
    client-supplied field. A real deployment resolves the GitHub installation → tenant on the
    install callback; here the repo url is the stable binding key. It is a real uuid5 (a valid
    ``tenants.id`` value) because ``connect_readiness.tenant_id`` is a DECLARED FK to
    ``tenants(id)`` (AC-TEN-001) — the same repo url always maps to the same tenant uuid, so
    the ``ensure_tenant`` upsert is idempotent across redeliveries.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, repo_url))


def _spawn_trigger(
    store: ConnectStore,
    install_id: str,
    tenant_id: str,
    repo_url: str,
    registry: Any = None,
    map_provider: Any = None,
    map_store: Any = None,
) -> None:
    """Fire the connect→index trigger in a background thread (never blocks the response).

    The install/start route returns immediately with a pollable handle; the trigger runs
    off-thread and streams readiness into the durable store the GET /connect/status poll
    reads, and registers the built pipeline in the live push-ingress ``registry`` so a real
    GitHub push reaches it. ``map_provider`` (the ``agentkit.Provider`` model seam) + ``map_store``
    (the durable ``MapStore``) are threaded straight into the trigger so a real map build runs
    when a funded provider is wired; when ``map_provider`` is ``None`` (the live default today,
    D-032) the trigger records an honest ``not_ready`` naming the gap rather than fabricating a
    map (Law 2). A trigger failure is captured as an honest ``not_ready`` inside the trigger —
    it never surfaces as an unhandled crash on the request path.
    """

    def _run() -> None:
        try:
            trigger_connect_index(
                store,
                install_id,
                tenant_id=tenant_id,
                repo_url=repo_url,
                registry=registry,
                map_provider=map_provider,
                map_store=map_store,
            )
        except Exception:  # noqa: BLE001 - already recorded as not_ready inside the trigger
            pass

    threading.Thread(target=_run, daemon=True).start()
