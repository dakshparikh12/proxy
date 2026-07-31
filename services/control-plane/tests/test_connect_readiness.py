"""connect — the readiness state machine + the D-032 honest no-op.

The connect→index trigger (SPEC §8) drives the pre-meeting map build and streams readiness into
the durable store: connecting → cloning → indexing → ready, or an honest not_ready that NAMES the
gap (Law 1 / Law 2). These exercise the REAL trigger + the REAL readiness listener + the REAL
ConnectStore validation with an in-memory fake store (no Postgres) and a faked pipeline, so the
state progression and the honest-degrade verdicts are provable offline.
"""
from __future__ import annotations

from typing import Any


class _FakeStore:
    """In-memory stand-in for ConnectStore: records the state progression + terminal writes.

    Mirrors the ConnectStore surface the trigger + listener use (mark_state / set_ready /
    set_not_ready), and enforces the SAME canonical-state guard the real store does so a bad
    state is caught here too."""

    _VALID = frozenset({"connecting", "cloning", "indexing", "ready", "not_ready"})

    def __init__(self) -> None:
        self.states: list[str] = []
        self.ready: tuple[float, list[tuple[str, str]] | None] | None = None
        self.not_ready: list[str] | None = None

    def mark_state(self, install_id: str, state: str) -> None:
        if state not in self._VALID:
            raise ValueError(f"{state!r} not canonical")
        self.states.append(state)

    def set_ready(self, install_id: str, coverage_pct: float,
                  flagged: list[tuple[str, str]] | None = None) -> None:
        self.ready = (coverage_pct, flagged)

    def set_not_ready(self, install_id: str, gaps: list[str]) -> None:
        self.not_ready = list(gaps)


def test_no_map_provider_is_an_honest_not_ready_d032() -> None:
    """D-032: with no funded map-build model provider, the trigger NEVER fabricates a map — it
    records an honest not_ready NAMING the gap (unfunded key), and never runs the pipeline."""
    from control_plane.connect import trigger_connect_index

    store = _FakeStore()
    result = trigger_connect_index(
        store, "install-1", tenant_id="t", repo_url="https://github.com/x/y", map_provider=None
    )
    assert result is None                        # no pipeline run
    assert store.states == ["connecting"]        # it started honestly
    assert store.ready is None                   # never a fabricated ready
    assert store.not_ready is not None
    gap = " ".join(store.not_ready).lower()
    assert "map-build skipped" in gap and "no model provider" in gap and "d-032" in gap


def test_ready_verdict_streams_progress_then_writes_terminal_ready(monkeypatch) -> None:
    """With a funded provider and a clean verify, the trigger streams the progress states through
    the listener and writes a terminal ready at 100% coverage (the prose map covers the whole
    clone; no partial-coverage / flagged-file concept)."""
    import control_plane.connect as connect_mod
    from premeeting.pipeline import PipelineResult

    store = _FakeStore()

    async def _fake_pipeline(**kw: Any) -> PipelineResult:
        listener = kw["readiness_listener"]
        for s in ("connecting", "cloning", "indexing"):
            listener.emit(s)
        return PipelineResult(ready=True, repo="y", sha="abc", reasons=[])

    monkeypatch.setattr("premeeting.pipeline.run_pipeline", _fake_pipeline)

    result = connect_mod.trigger_connect_index(
        store, "install-2", tenant_id="t", repo_url="https://github.com/x/y",
        map_provider=object(), map_store=object(),
    )
    assert result is not None and result.ready is True
    # the progress states were streamed into the durable store (connecting/cloning/indexing):
    assert store.states == ["connecting", "cloning", "indexing"]
    assert store.ready == (100.0, [])            # terminal ready, full coverage, no flags
    assert store.not_ready is None


def test_not_ready_verdict_names_the_pipeline_gaps(monkeypatch) -> None:
    """A pipeline that fails verify -> terminal not_ready NAMING the reasons the pipeline produced
    (never a faked pass), after streaming whatever progress states it reached."""
    import control_plane.connect as connect_mod
    from premeeting.pipeline import PipelineResult

    store = _FakeStore()

    async def _fake_pipeline(**kw: Any) -> PipelineResult:
        listener = kw["readiness_listener"]
        listener.emit("connecting")
        listener.emit("cloning")
        return PipelineResult(
            ready=False, repo="y", reasons=["the repository could not be cloned"]
        )

    monkeypatch.setattr("premeeting.pipeline.run_pipeline", _fake_pipeline)

    connect_mod.trigger_connect_index(
        store, "install-3", tenant_id="t", repo_url="https://github.com/x/y",
        map_provider=object(),
    )
    assert store.ready is None                   # never a ready on a failed build
    assert store.not_ready == ["the repository could not be cloned"]
    assert store.states == ["connecting", "cloning"]


def test_pipeline_raise_records_not_ready_then_reraises(monkeypatch) -> None:
    """A pipeline that RAISES is captured as an honest not_ready (the type named) before it
    propagates — the readiness record never silently claims success."""
    import control_plane.connect as connect_mod

    store = _FakeStore()

    async def _boom(**kw: Any) -> Any:
        raise ValueError("kaboom")

    monkeypatch.setattr("premeeting.pipeline.run_pipeline", _boom)

    raised = False
    try:
        connect_mod.trigger_connect_index(
            store, "install-4", tenant_id="t", repo_url="https://github.com/x/y",
            map_provider=object(),
        )
    except ValueError:
        raised = True
    assert raised is True
    assert store.ready is None
    assert store.not_ready is not None
    assert "indexing failed" in " ".join(store.not_ready) and "ValueError" in " ".join(store.not_ready)


def test_readiness_listener_forwards_only_progress_states() -> None:
    """The _StoreReadinessListener forwards connecting/cloning/indexing into the store but IGNORES
    the terminal ready/not_ready (those carry payloads the trigger writes itself). All emitted
    states are still recorded for provable ordering, and a listener blip never crashes."""
    from control_plane.connect import _StoreReadinessListener

    store = _FakeStore()
    listener = _StoreReadinessListener(store, "install-5")
    for s in ("connecting", "cloning", "indexing", "ready", "not_ready"):
        listener.emit(s)
    # only the three progress states reached the store (ready/not_ready are ignored here):
    assert store.states == ["connecting", "cloning", "indexing"]
    # but every emitted state is captured on the listener for ordering:
    assert listener.emitted_states == ["connecting", "cloning", "indexing", "ready", "not_ready"]

    # a store blip (ValueError / unavailable) during a progress mark is swallowed:
    class _BlipStore(_FakeStore):
        def mark_state(self, install_id: str, state: str) -> None:
            raise ValueError("bad state")

    listener2 = _StoreReadinessListener(_BlipStore(), "install-6")
    listener2.emit("cloning")  # must not raise
    assert listener2.emitted_states == ["cloning"]


def test_connect_store_mark_state_rejects_a_non_canonical_state() -> None:
    """The store's mark_state rejects any value outside the canonical Readiness enum — a 'mapping'
    state is unrepresentable (CANONICAL §1.5), caught before the durable row is touched."""
    from control_plane.connect import ConnectStore

    # a conn factory that would raise if ever used — proves rejection happens BEFORE any DB touch:
    def _explode() -> Any:
        raise AssertionError("mark_state must reject before opening a connection")

    store = ConnectStore(conn_factory=_explode)
    for bad in ("mapping", "done", "READY", ""):
        raised = False
        try:
            store.mark_state("install-x", bad)
        except ValueError:
            raised = True
        assert raised is True, f"{bad!r} should be rejected"


def test_connect_store_unavailable_on_a_dead_substrate() -> None:
    """A conn factory that fails surfaces as ConnectStoreUnavailable at the store boundary (not a
    raw driver traceback) — the seam that lets GET /connect/status degrade honestly."""
    from control_plane.connect import ConnectStore, ConnectStoreUnavailable

    def _dead() -> Any:
        raise RuntimeError("connection refused")

    store = ConnectStore(conn_factory=_dead)
    for op in (lambda: store.status("i"), lambda: store.set_not_ready("i", ["g"])):
        raised = False
        try:
            op()
        except ConnectStoreUnavailable:
            raised = True
        assert raised is True


def test_github_app_install_url_carries_the_repo_binding(monkeypatch) -> None:
    """The install URL opens the configured GitHub App and url-encodes the repo binding into
    ``state`` so the callback can resume the connect flow."""
    from control_plane.connect import github_app_install_url

    monkeypatch.setenv("PROXY_GITHUB_APP_SLUG", "proxy-app")
    url = github_app_install_url("https://github.com/calcom/cal.com")
    assert url.startswith("https://github.com/apps/proxy-app/installations/new?state=")
    # the repo url is percent-encoded into the state param (":" and "/" escaped):
    assert "https%3A%2F%2Fgithub.com%2Fcalcom%2Fcal.com" in url
