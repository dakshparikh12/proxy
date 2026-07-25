"""Doc 08 · §2.7/§4.6 — the connect page: GitHub-App install + REST readiness poll.

Node ``experience.connect-page``. The one out-of-meeting door: a public URL whose two
REST routes (GET /connect/status, POST /connect/install/start) launch the install and
poll Doc 01's readiness, rendering ALL FIVE states of the canonical Readiness enum
(CANONICAL §1.5) — connecting → cloning → indexing → ready, plus an explicit
not_ready(+gaps) terminal. The happy path carries the REAL coverage number and flagged
files; not_ready names the gaps. There is NO 'mapping' state and the poll is REST, not WS.

These are REAL-path tests:
  * the connect→index TRIGGER actually drives ``code_intel.run_full_pipeline`` on a tiny
    LOCAL git-repo fixture (never a fake) and readiness reaches ``ready`` with a real
    coverage_pct + flagged files;
  * the trigger sets ``pipeline.lsp = MultiLangResolver(clone_root)`` so find_references
    returns RESOLVED refs (closes precise_nav) and registers the freshness webhook;
  * GET /connect/status renders each of the five states from the store the trigger writes;
  * both routes are on the LIVE control_plane app and classify as ``public`` (allowlisted),
    never ``raw`` — the connect page is a public URL validated like any public API (§4.6);
  * no ``connect.*`` WS registry entry is (re-)introduced — the poll is REST (§12.12).

Product imports live inside the test bodies so this module COLLECTS clean and FAILS RED
before ``control_plane/connect.py`` exists.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


# --------------------------------------------------------------------------- #
# 1 · The connect→index TRIGGER actually runs the pipeline on a real repo.
# --------------------------------------------------------------------------- #
def test_trigger_runs_real_pipeline_and_reaches_ready_with_real_coverage() -> None:
    """The trigger drives run_full_pipeline on a tiny LOCAL git-repo fixture (real, not
    a fake) and readiness reaches ``ready`` with a REAL coverage number in (0, 1]."""
    from control_plane.connect import ConnectStore, trigger_connect_index
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    store = ConnectStore()
    install_id = store.new_install(tenant_id="tenant-connect-1", repo_url=fixture.url)

    pipeline = trigger_connect_index(store, install_id, tenant_id="tenant-connect-1", repo_url=fixture.url)

    report = store.status(install_id)
    assert report.status == "ready", f"expected ready, got {report.status!r} (states={store.states(install_id)})"
    # A REAL number, not a faked constant — every tracked .py file in the small fixture
    # parses, so coverage is a genuine indexed/(indexed+flagged) ratio in (0, 1].
    assert 0.0 < report.coverage_pct <= 1.0
    # And the pipeline object is the real one the trigger built.
    assert pipeline.clone_path.exists()


def test_trigger_emits_all_four_progress_states_in_order() -> None:
    """The readiness progression the poll renders is connecting→cloning→indexing→ready —
    the exact canonical order, with NO 'mapping' state anywhere."""
    from control_plane.connect import ConnectStore, trigger_connect_index
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    store = ConnectStore()
    install_id = store.new_install(tenant_id="tenant-connect-2", repo_url=fixture.url)

    trigger_connect_index(store, install_id, tenant_id="tenant-connect-2", repo_url=fixture.url)

    states = store.states(install_id)
    assert "mapping" not in states, "there is NO 'mapping' state (CANONICAL §1.5)"
    # The four progress states appear in canonical order.
    for expected in ("connecting", "cloning", "indexing", "ready"):
        assert expected in states, f"missing progress state {expected!r} in {states}"
    assert states.index("connecting") < states.index("cloning") < states.index("indexing") < states.index("ready")


def test_trigger_sets_multilang_resolver_so_find_references_is_resolved() -> None:
    """The trigger sets ``pipeline.lsp = MultiLangResolver(clone_root)`` and re-mints the
    query factory so find_references returns a RESOLVED ref (closes precise_nav)."""
    import asyncio

    from code_intel.warm_resolver import MultiLangResolver
    from control_plane.connect import ConnectStore, trigger_connect_index
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    store = ConnectStore()
    install_id = store.new_install(tenant_id="tenant-connect-3", repo_url=fixture.url)

    pipeline = trigger_connect_index(store, install_id, tenant_id="tenant-connect-3", repo_url=fixture.url)

    # The warm resolver is set on the pipeline (the seam the factory reads).
    assert isinstance(pipeline.lsp, MultiLangResolver)
    # A symbol defined in the fixture resolves through the resolver directly.
    known = fixture.known_symbol
    assert pipeline.lsp.references(known), f"resolver could not resolve {known!r}"
    # And the live query server (re-minted after lsp was set) returns a RESOLVED confidence.
    server = asyncio.get_event_loop().run_until_complete(pipeline.server_factory.create_for_query(known))
    result = server.find_references(known)
    assert result.status == "ok"
    assert any(item.confidence == "resolved" for item in result.results), (
        "find_references must return a RESOLVED ref once the warm MultiLangResolver is set"
    )


def test_trigger_registers_freshness_webhook_handler() -> None:
    """The trigger closes freshness: the pipeline carries a live webhook handler that a
    push delivery would drive (registered by the trigger, not left None)."""
    from control_plane.connect import ConnectStore, trigger_connect_index
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    store = ConnectStore()
    install_id = store.new_install(tenant_id="tenant-connect-4", repo_url=fixture.url)

    pipeline = trigger_connect_index(store, install_id, tenant_id="tenant-connect-4", repo_url=fixture.url)

    assert pipeline.webhook_handler is not None, "freshness webhook handler must be registered"


# --------------------------------------------------------------------------- #
# 2 · GET /connect/status renders ALL FIVE readiness states from the store.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def live_app() -> Any:
    """The REAL control_plane app with the connect routes mounted LIVE."""
    from control_plane.app import create_app

    return create_app()


def test_status_route_renders_ready_with_coverage_and_flagged_files(live_app) -> None:
    """On the happy path GET /connect/status returns status=ready, the real coverage_pct,
    and the flagged-file summary ('N files flagged: <reason>')."""
    from control_plane.connect import get_connect_store

    store = get_connect_store(live_app)
    install_id = store.new_install(tenant_id="t-ready", repo_url="local")
    # Seed a terminal ready report exactly as the trigger writes it, including flagged files.
    store.set_ready(install_id, coverage_pct=0.94, flagged=[("gen/thing.min.js", "generated")])

    client = TestClient(live_app)
    resp = client.get("/connect/status", params={"install_id": install_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["coverage_pct"] == pytest.approx(0.94)
    # The flagged files are surfaced honestly (the '12 files flagged: generated' shape).
    assert body["flagged_files"], "ready must surface the flagged files"
    assert body["flagged_files"][0]["path"] == "gen/thing.min.js"
    assert body["flagged_files"][0]["reason"] == "generated"


def test_status_route_renders_not_ready_with_named_gaps(live_app) -> None:
    """not_ready is a real TERMINAL state that NAMES the gaps — never an error page,
    never a pretended number."""
    from control_plane.connect import get_connect_store

    store = get_connect_store(live_app)
    install_id = store.new_install(tenant_id="t-notready", repo_url="local")
    store.set_not_ready(install_id, gaps=["submodule vendor/ could not be cloned", "3 files failed to parse"])

    client = TestClient(live_app)
    resp = client.get("/connect/status", params={"install_id": install_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["gaps"], "not_ready must name the gaps"
    assert "submodule vendor/ could not be cloned" in body["gaps"]


@pytest.mark.parametrize("state", ["connecting", "cloning", "indexing"])
def test_status_route_renders_each_progress_state(live_app, state) -> None:
    """Each of the three in-flight progress states renders over the public REST poll."""
    from control_plane.connect import get_connect_store

    store = get_connect_store(live_app)
    install_id = store.new_install(tenant_id=f"t-{state}", repo_url="local")
    store.mark_state(install_id, state)

    client = TestClient(live_app)
    resp = client.get("/connect/status", params={"install_id": install_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == state


def test_status_route_never_returns_a_mapping_state(live_app) -> None:
    """A 'mapping' state must be UNREPRESENTABLE — the store rejects it (CANONICAL §1.5)."""
    from control_plane.connect import ConnectStore, get_connect_store

    store = get_connect_store(live_app)
    install_id = store.new_install(tenant_id="t-map", repo_url="local")
    with pytest.raises((ValueError, KeyError)):
        store.mark_state(install_id, "mapping")


def test_status_route_unknown_install_is_connecting_not_an_internal_error(live_app) -> None:
    """An unknown/never-started install polls as 'connecting' (the initial state) — the
    public poll never leaks an internal error string (§4.6 safeError)."""
    client = TestClient(live_app)
    resp = client.get("/connect/status", params={"install_id": "does-not-exist"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "connecting"


# --------------------------------------------------------------------------- #
# 3 · POST /connect/install/start launches the install (and kicks the trigger).
# --------------------------------------------------------------------------- #
def test_install_start_returns_an_install_handle_and_the_github_app_url(live_app) -> None:
    """POST /connect/install/start launches the GitHub-App install flow: it returns an
    install_id to poll AND the GitHub-App install URL to open."""
    client = TestClient(live_app)
    resp = client.post("/connect/install/start", json={"repo_url": "https://github.com/acme/checkout"})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("install_id"), "install/start must return an install_id to poll"
    assert "github" in body.get("install_url", "").lower(), "install/start must return the GitHub-App install URL"
    # The freshly-started install is immediately pollable, at 'connecting'.
    poll = client.get("/connect/status", params={"install_id": body["install_id"]})
    assert poll.status_code == 200
    assert poll.json()["status"] in {"connecting", "cloning", "indexing", "ready", "not_ready"}


def test_install_start_handler_never_throws_on_bad_input(live_app) -> None:
    """The public install/start handler never throws on malformed input — it returns a
    validation error body, never a 500 with an internal string (never-throw boundary)."""
    client = TestClient(live_app)
    resp = client.post("/connect/install/start", json={})  # missing repo_url
    # A 4xx validation response (its own bad input), NEVER a 500 leaking an internal trace.
    assert resp.status_code in {400, 422}


# --------------------------------------------------------------------------- #
# 4 · The routes are on the LIVE app, public/allowlisted, and NOT WS.
# --------------------------------------------------------------------------- #
def test_connect_routes_are_live_and_classified_public_not_raw(live_app) -> None:
    """Both connect routes are mounted on the real app and classify as ``public`` (on the
    PUBLIC_ROUTES allowlist) — never ``raw`` (a tenant-isolation gap)."""
    from libs.http.registry import classify_route, route_key

    keys = {}
    for route in live_app.routes:
        key = route_key(route)
        if key in {"GET /connect/status", "POST /connect/install/start"}:
            keys[key] = classify_route(route)
    assert "GET /connect/status" in keys, "GET /connect/status must be mounted on the live app"
    assert "POST /connect/install/start" in keys, "POST /connect/install/start must be mounted"
    for key, verdict in keys.items():
        assert verdict == "public", f"{key} must classify public (allowlisted), got {verdict!r}"


def test_connect_poll_is_rest_not_a_ws_message() -> None:
    """The connect poll is REST (§12.12): there is NO connect.* WS message type in the
    contracts registry — re-introducing one is the risk this test guards."""
    from contracts.registry import MessageType

    for member in MessageType:
        assert not str(member.value).lower().startswith("connect"), (
            f"a connect.* WS message type ({member!r}) must NOT exist — the connect page is REST"
        )


# --------------------------------------------------------------------------- #
# 5 · apps/connect renders the teal page + all five states (the static app).
# --------------------------------------------------------------------------- #
def _repo_root() -> Any:
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def test_connect_app_renders_the_teal_page_with_install_and_invite() -> None:
    """apps/connect is a real static app: a teal page with the install flow and the
    invite instructions (not the bare scaffold it started as)."""
    root = _repo_root() / "apps" / "connect"
    index = (root / "index.html").read_text(encoding="utf-8")
    css = (root / "src" / "connect.css").read_text(encoding="utf-8")
    # The install flow: a form that posts to /connect/install/start (wired in main.js).
    assert "install-form" in index
    main_js = (root / "src" / "main.js").read_text(encoding="utf-8")
    assert "/connect/install/start" in main_js
    assert "/connect/status" in main_js  # the REST poll
    # The invite instructions.
    assert "Invite Proxy to a meeting" in index
    # Teal: the brand colour is defined in the stylesheet.
    assert "--teal" in css


def test_connect_app_poll_is_rest_fetch_not_a_websocket() -> None:
    """The connect app polls readiness with a REST fetch — it opens NO WebSocket
    (CANONICAL §12.12: the connect page is REST, not a WS message)."""
    main_js = (_repo_root() / "apps" / "connect" / "src" / "main.js").read_text(encoding="utf-8")
    assert "fetch(" in main_js
    assert "WebSocket" not in main_js and "new WebSocket" not in main_js


def test_connect_app_readiness_renderer_renders_all_five_states_headless() -> None:
    """The connect app's PURE readiness renderer draws all five canonical states honestly
    — real coverage + flagged on ready, named gaps on not_ready, a labelled panel per
    progress state, and NO 'mapping' branch. Driven headless via the checked-in node
    render check (the real renderReadiness path, no browser)."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available to drive the headless render check")
    check = _repo_root() / "apps" / "connect" / "src" / "readiness.render-check.mjs"
    proc = subprocess.run(  # noqa: S603 - fixed node binary + checked-in script, no shell
        [node, str(check)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"headless render check failed:\n{proc.stdout}\n{proc.stderr}"
    assert "RENDER CHECK OK" in proc.stdout
