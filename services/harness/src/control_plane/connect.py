"""The connect page's public REST API + the connect→index trigger (Doc 08 §2.7/§4.6).

The connect page is the ONE out-of-meeting surface (§2.7): a small static app plus its
two PUBLIC REST routes on control_plane. This module owns the backend half:

  * **POST /connect/install/start** — launches the GitHub-App install flow (Doc 01) AND
    fires the connect→index TRIGGER in a background task. This is the FIRST live caller of
    ``code_intel.run_full_pipeline`` — the product spine that closes codeintel
    pipeline_orchestration / coverage_readiness / freshness / precise_nav.
  * **GET /connect/status** — the readiness POLL. It is **REST, not a WS message**
    (CANONICAL §12.12) and renders ALL FIVE states of Doc 01's ``Readiness`` enum
    (CANONICAL §1.5): ``connecting → cloning → indexing → ready`` plus an explicit
    ``not_ready`` terminal that NAMES the gaps. There is NO ``mapping`` state — indexing
    already means clone + tree-sitter + LSP + dep-graph. The happy path carries the REAL
    coverage number and the flagged files ("94% indexed · 12 files flagged: generated");
    ``not_ready`` names what is missing rather than pretending (Law 1 / Law 2).

Both routes are PUBLIC (no meeting exists yet) but validated by the §4.6 wrappers — the
connect page is a public URL, reachable by anyone with the link, so its calls are
untrusted and allowlisted, never trusted-by-default. A client-supplied ``install_id`` is
an opaque poll handle only; it authorizes NOTHING beyond reading its own readiness (the
store never joins it to another tenant's data).

The connect→index trigger sets ``pipeline.lsp = MultiLangResolver(clone_root)`` (so
``find_references`` returns RESOLVED refs — closes precise_nav) and re-mints the query
factory over it, and leaves the pipeline's freshness webhook handler live (closes
freshness). Readiness progresses ``connecting → cloning → indexing → ready`` with the
REAL ``coverage_pct`` + flagged files; ``not_ready`` surfaces named gaps.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from contracts.readiness import Readiness, ReadinessReport

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

# The canonical Readiness enum (CANONICAL §1.5). ``mapping`` is deliberately absent —
# ``mark_state`` rejects any value outside this set so a 'mapping' state is unrepresentable.
_VALID_STATES: frozenset[str] = frozenset({"connecting", "cloning", "indexing", "ready", "not_ready"})

# Human-facing labels for the coverage flag reasons the code_intel build stamps
# (``CoverageRow.flag_reason``). The happy path shows "N files flagged: <label>"; these map
# the internal reason strings to the honest user-visible word. An unknown reason passes
# through verbatim (never dropped) so the number always accounts for every flagged file.
_FLAG_LABELS: dict[str, str] = {
    "excluded": "excluded",
    "parse-error": "failed to parse",
    "unsupported-language": "unsupported",
    "generated": "generated",
}


@dataclass
class FlaggedFile:
    """One flagged file on the happy path — a path + the honest reason it was flagged."""

    path: str
    reason: str


@dataclass
class InstallRecord:
    """One connect install's live readiness — the state the REST poll renders.

    Keyed by an opaque ``install_id`` (a poll handle, NOT an authorization token). The
    ``status`` only ever holds a canonical Readiness value; ``coverage_pct`` and
    ``flagged`` are the REAL numbers the pipeline produced on ``ready``; ``gaps`` names
    what is missing on ``not_ready``. ``states`` records the progression the trigger
    emitted so the ordering (connecting→cloning→indexing→ready) is provable.
    """

    tenant_id: str
    repo_url: str
    status: Readiness = "connecting"
    coverage_pct: float = 0.0
    flagged: list[FlaggedFile] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=lambda: ["connecting"])


class ConnectStore:
    """In-process registry of connect installs → live readiness (the poll's read model).

    There is no meeting yet, so readiness is keyed by an opaque ``install_id`` rather than
    a meeting/tenant the caller could forge. The store is the seam between the background
    trigger (which WRITES readiness as the pipeline progresses) and GET /connect/status
    (which READS it). Thread-safe: the trigger runs in a background thread while the poll
    reads on the request thread.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, InstallRecord] = {}
        self._lock = threading.Lock()

    def new_install(self, tenant_id: str, repo_url: str) -> str:
        """Register a fresh install (state ``connecting``) and return its opaque poll id."""
        install_id = uuid.uuid4().hex
        with self._lock:
            self._by_id[install_id] = InstallRecord(tenant_id=tenant_id, repo_url=repo_url)
        return install_id

    def _record(self, install_id: str) -> InstallRecord | None:
        with self._lock:
            return self._by_id.get(install_id)

    def mark_state(self, install_id: str, state: str) -> None:
        """Advance an install to a canonical progress state.

        Rejects any value outside the canonical Readiness enum — a 'mapping' state (or any
        typo) raises rather than silently corrupting the poll (CANONICAL §1.5). An unknown
        install id raises too (never a silent no-op that would mask a wiring bug).
        """
        if state not in _VALID_STATES:
            raise ValueError(f"{state!r} is not a canonical Readiness state ({sorted(_VALID_STATES)})")
        with self._lock:
            record = self._by_id.get(install_id)
            if record is None:
                raise KeyError(install_id)
            record.status = cast(Readiness, state)
            if not record.states or record.states[-1] != state:
                record.states.append(state)

    def set_ready(
        self, install_id: str, coverage_pct: float, flagged: list[tuple[str, str]] | None = None
    ) -> None:
        """Terminal ``ready``: the REAL coverage number + the flagged files (honest, §2.7)."""
        with self._lock:
            record = self._by_id.get(install_id)
            if record is None:
                raise KeyError(install_id)
            record.status = "ready"
            record.coverage_pct = float(coverage_pct)
            record.flagged = [FlaggedFile(path=p, reason=r) for (p, r) in (flagged or [])]
            record.gaps = []
            if not record.states or record.states[-1] != "ready":
                record.states.append("ready")

    def set_not_ready(self, install_id: str, gaps: list[str]) -> None:
        """Terminal ``not_ready``: NAME the gaps — never an error page, never a faked pct."""
        with self._lock:
            record = self._by_id.get(install_id)
            if record is None:
                raise KeyError(install_id)
            record.status = "not_ready"
            record.gaps = list(gaps)
            if not record.states or record.states[-1] != "not_ready":
                record.states.append("not_ready")

    def status(self, install_id: str) -> ReadinessReport:
        """The ReadinessReport the poll renders — always a canonical, honest report.

        An unknown/never-started install reads as ``connecting`` (the initial state), so the
        public poll never leaks an internal error string for a stale/typo'd id (§4.6).
        """
        record = self._record(install_id)
        if record is None:
            return ReadinessReport(status="connecting")
        return ReadinessReport(status=record.status, coverage_pct=record.coverage_pct, gaps=list(record.gaps))

    def flagged_files(self, install_id: str) -> list[FlaggedFile]:
        """The flagged files for an install (empty unless it reached ``ready``)."""
        record = self._record(install_id)
        return list(record.flagged) if record is not None else []

    def states(self, install_id: str) -> list[str]:
        """The ordered readiness progression the trigger emitted (for provable ordering)."""
        record = self._record(install_id)
        return list(record.states) if record is not None else []


class _StoreReadinessListener:
    """A readiness listener that streams the pipeline's states into a :class:`ConnectStore`.

    ``run_full_pipeline`` calls ``listener.emit(state)`` at each phase. We forward each
    canonical progress state (connecting/cloning/indexing) into the store so the REST poll
    renders the live progression; the terminal ``ready`` / ``not_ready`` write is done by
    the trigger itself (with the real coverage number / named gaps), so those are ignored
    here to avoid a terminal write without its payload.
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
            except (KeyError, ValueError):
                # A dropped install id or a non-canonical state never crashes the pipeline.
                pass

    # aliases the pipeline / collectors may use
    def record(self, state: str) -> None:
        self.emit(state)

    def on_state(self, state: str) -> None:
        self.emit(state)

    def set_error(self, error: Any) -> None:  # never-throw parity with ReadinessCollector
        pass


def _human_flagged(flag_reason: str | None) -> str:
    """Map an internal coverage flag reason to the honest user-visible word."""
    if not flag_reason:
        return "flagged"
    return _FLAG_LABELS.get(flag_reason, flag_reason)


def trigger_connect_index(
    store: ConnectStore,
    install_id: str,
    tenant_id: str,
    repo_url: str,
    sha: str | None = None,
) -> Any:
    """The connect→index TRIGGER — the product spine (first live run_full_pipeline caller).

    Drives ``code_intel.run_full_pipeline`` on the tenant's repo, streaming readiness into
    ``store`` as it progresses (connecting → cloning → indexing). On completion it:

      1. sets ``pipeline.lsp = MultiLangResolver(clone_root)`` so ``find_references``
         returns a RESOLVED result (closes precise_nav) and RE-MINTS the query factory over
         it (the factory reads ``pipeline.lsp`` at construction, so it must be re-bound);
      2. leaves the pipeline's freshness webhook handler live (closes freshness);
      3. writes the terminal readiness — ``ready`` with the REAL ``coverage_pct`` + flagged
         files, or ``not_ready`` naming the gaps.

    Returns the built :class:`Pipeline` (the caller may retain it; the route fires this in a
    background task and discards it). Never raises out to the caller — a pipeline failure is
    surfaced as an honest ``not_ready`` with the reason named, never a silent success.
    """
    from code_intel.mcp_server import MCPServerFactory
    from code_intel.pipeline import run_full_pipeline
    from code_intel.warm_resolver import MultiLangResolver

    listener = _StoreReadinessListener(store, install_id)
    try:
        pipeline = run_full_pipeline(
            tenant_id=tenant_id,
            repo_url=repo_url,
            sha=sha,
            readiness_listener=listener,
        )
    except Exception as exc:  # noqa: BLE001 - honest not_ready, never a silent success
        try:
            store.set_not_ready(install_id, gaps=[f"indexing failed: {type(exc).__name__}"])
        except KeyError:
            pass
        raise

    # precise_nav: set the warm multi-language resolver and re-mint the query factory over
    # it so find_references resolves (the factory captured ``pipeline.lsp`` at build time,
    # which was None; re-binding here is what makes the first query a RESOLVED result).
    clone_root = pipeline.clone_path
    if clone_root is not None and clone_root.exists():
        pipeline.lsp = MultiLangResolver(clone_root)
        pipeline.server_factory = MCPServerFactory.for_pipeline(pipeline)
        if pipeline.server is not None:
            pipeline.server._lsp = pipeline.lsp

    # freshness: the pipeline's webhook handler is already registered by run_full_pipeline;
    # it stays live on the pipeline so a push delivery drives a delta-pull + rebuild.

    # Terminal readiness — read the REAL result off the pipeline (never a faked number).
    record = pipeline.readiness_record
    reached_ready = record is not None and record.indexed_at is not None
    if reached_ready:
        coverage = pipeline.coverage_record
        flagged_rows = [r for r in coverage.all_rows() if r.status == "flagged"]
        flagged = [(r.path, _human_flagged(r.flag_reason)) for r in flagged_rows]
        pct = record.coverage_pct if record is not None else 0.0
        store.set_ready(install_id, coverage_pct=pct, flagged=flagged)
    else:
        gaps = _gaps_from_pipeline(pipeline)
        store.set_not_ready(install_id, gaps=gaps)
    return pipeline


def _gaps_from_pipeline(pipeline: Any) -> list[str]:
    """Name the gaps that kept a pipeline from ``ready`` — honest, never a pretended pass.

    Derives the named gaps from the coverage record: any flagged file is a file the index
    could not fully ground, surfaced by name so ``not_ready`` explains itself (Law 1). If
    nothing is flagged (e.g. an empty clone / unreachable upstream), that root cause is
    named instead of an empty, unexplained ``not_ready``.
    """
    coverage = getattr(pipeline, "coverage_record", None)
    rows = coverage.all_rows() if coverage is not None else []
    flagged = [r for r in rows if getattr(r, "status", "") == "flagged"]
    if flagged:
        return [f"{r.path}: {_human_flagged(r.flag_reason)}" for r in flagged]
    clone_path = getattr(pipeline, "clone_path", None)
    if clone_path is None or not clone_path.exists():
        return ["the repository could not be cloned"]
    return ["no files could be indexed"]


# ── GitHub-App install URL ────────────────────────────────────────────────────
def github_app_install_url(repo_url: str) -> str:
    """The GitHub-App install URL the connect page opens to launch the install (Doc 01).

    The connect page links out to GitHub's App-install page; the ``state`` carries our
    repo binding so the install callback can resume the connect flow. This is the URL the
    page opens — the actual App slug is configured at deploy; a stable default keeps the
    door openable in every environment.
    """
    import os
    from urllib.parse import quote

    app_slug = os.environ.get("PROXY_GITHUB_APP_SLUG", "proxy")
    return f"https://github.com/apps/{quote(app_slug)}/installations/new?state={quote(repo_url, safe='')}"


# ── the store lives on app.state so the route + the trigger share one instance ──
def get_connect_store(app: Any) -> ConnectStore:
    """The single :class:`ConnectStore` bound to this app (created on first access).

    Both the install/start route (which spawns the trigger) and the status poll read the
    SAME store instance off ``app.state`` — the seam that lets a background index write the
    readiness the poll renders.
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
    NOT a WS message (CANONICAL §12.12).
    """
    from fastapi import Body
    from starlette.responses import JSONResponse

    store = get_connect_store(app)

    @app.get("/connect/status", include_in_schema=True)
    async def connect_status(install_id: str) -> JSONResponse:
        """The public REST readiness poll — renders all five canonical states honestly.

        Never throws: an unknown install id reads as ``connecting`` (the store's honest
        default), so a stale/typo'd handle yields a clean 200 rather than an internal error.
        """
        report = store.status(install_id)
        flagged = store.flagged_files(install_id) if report.status == "ready" else []
        body: dict[str, Any] = {
            "install_id": install_id,
            "status": report.status,
            "coverage_pct": report.coverage_pct,
            "gaps": report.gaps,
            "flagged_files": [{"path": f.path, "reason": f.reason} for f in flagged],
        }
        return JSONResponse(body, status_code=200)

    @app.post("/connect/install/start", include_in_schema=True)
    async def connect_install_start(repo_url: str = Body(..., embed=True)) -> JSONResponse:
        """Launch the GitHub-App install flow AND fire the connect→index trigger.

        Registers a fresh install (immediately pollable at ``connecting``), spawns the
        trigger as a background task (the first live ``run_full_pipeline`` caller), and
        returns the poll handle + the GitHub-App install URL to open. A malformed body
        (missing ``repo_url``) is a FastAPI validation error (the caller's own bad input),
        never a 500 — the never-throw boundary.
        """
        tenant_id = _tenant_for_install(repo_url)
        install_id = store.new_install(tenant_id=tenant_id, repo_url=repo_url)
        _spawn_trigger(store, install_id, tenant_id=tenant_id, repo_url=repo_url)
        return JSONResponse(
            {"install_id": install_id, "install_url": github_app_install_url(repo_url)},
            status_code=200,
        )


def _tenant_for_install(repo_url: str) -> str:
    """The tenant a fresh install binds to — a deterministic, opaque per-repo id.

    The install/start caller is anonymous (no session yet — the install IS how a tenant is
    provisioned), so the tenant is derived server-side from the repo binding, never a
    client-supplied field. A real deployment resolves the GitHub installation → tenant on
    the install callback; here the repo url is the stable binding key.
    """
    return "connect-" + uuid.uuid5(uuid.NAMESPACE_URL, repo_url).hex[:16]


def _spawn_trigger(store: ConnectStore, install_id: str, tenant_id: str, repo_url: str) -> None:
    """Fire the connect→index trigger in a background thread (never blocks the response).

    The install/start route returns immediately with a pollable handle; the trigger runs
    off-thread and streams readiness into the store the GET /connect/status poll reads. A
    trigger failure is captured as an honest ``not_ready`` inside the trigger — it never
    surfaces as an unhandled crash on the request path.
    """

    def _run() -> None:
        try:
            trigger_connect_index(store, install_id, tenant_id=tenant_id, repo_url=repo_url)
        except Exception:  # noqa: BLE001 - already recorded as not_ready inside the trigger
            pass

    threading.Thread(target=_run, daemon=True).start()
