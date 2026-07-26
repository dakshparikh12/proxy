"""M7 freshness LIVE push ingress — POST /webhooks/github → WebhookHandler.handle.

The connect→index trigger builds a per-tenant pipeline carrying a live freshness
``webhook_handler``. Before this route that handler had NO live caller — a real inbound
GitHub push never reached it, so the push→reindex path was isolation-only. These tests
drive the REAL ``control_plane`` app and prove the ingress is wired end-to-end:

  * a bad-HMAC push is refused (401) and triggers NO rebuild (AC-M7-001) on the LIVE route;
  * a VALID-HMAC push routes to the pipeline the connect trigger registered and drives its
    ``WebhookHandler.handle`` → a real delta-pull + graph rebuild (AC-M7-008 / §3.6);
  * the tenant is resolved server-side from the SIGNED payload's repository, never a
    request field (Law 3 / the server-side tenant check);
  * the route is on the app LIVE and classifies as ``public`` (allowlisted), never ``raw``.

Product imports live inside the test bodies so this module COLLECTS clean.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

_SECRET = "proxy-github-test-secret-0123456789"


def _sign(secret: str, body: bytes) -> str:
    """The X-Hub-Signature-256 header value GitHub sends (sha256=<hex hmac>)."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _push_payload(repo_url: str, after: str, changed: list[str]) -> dict[str, Any]:
    """A minimal but real-shaped GitHub push payload."""
    return {
        "after": after,
        "forced": False,
        "repository": {"clone_url": repo_url, "html_url": repo_url},
        "commits": [
            {"id": after, "added": [], "modified": changed, "removed": []},
        ],
    }


@pytest.fixture()
def live_app(monkeypatch) -> Any:
    from control_plane.app import create_app

    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", _SECRET)
    return create_app()


def test_bad_hmac_push_is_401_and_triggers_no_rebuild(live_app) -> None:
    """AC-M7-001 on the LIVE route: a forged signature is 401 and no pipeline reindexes."""
    from control_plane.connect import ConnectStore, trigger_connect_index
    from control_plane.github_webhook import get_pipeline_registry
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    registry = get_pipeline_registry(live_app)
    store = ConnectStore()
    install_id = store.new_install(tenant_id="tenant-gh-bad", repo_url=fixture.url)
    pipeline = trigger_connect_index(
        store, install_id, tenant_id="tenant-gh-bad", repo_url=fixture.url, registry=registry
    )
    sha_before = pipeline.current_sha

    body = json.dumps(_push_payload(fixture.url, "sha_after_bad", ["pkg/a.py"])).encode()
    client = TestClient(live_app)
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=deadbeefbadsignature",
            "X-GitHub-Delivery": "delivery-bad",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401, f"forged push must be 401, got {resp.status_code}"
    assert pipeline.current_sha == sha_before, "a forged push must NOT advance the pipeline"


def test_valid_hmac_push_drives_the_registered_pipeline_reindex(live_app) -> None:
    """A signed push routes to the connect-registered pipeline and drives a real reindex."""
    from control_plane.connect import ConnectStore, trigger_connect_index
    from control_plane.github_webhook import get_pipeline_registry
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    registry = get_pipeline_registry(live_app)
    store = ConnectStore()
    install_id = store.new_install(tenant_id="tenant-gh-ok", repo_url=fixture.url)
    pipeline = trigger_connect_index(
        store, install_id, tenant_id="tenant-gh-ok", repo_url=fixture.url, registry=registry
    )
    assert pipeline.webhook_handler is not None

    new_sha = "sha_after_valid_push"
    body = json.dumps(_push_payload(fixture.url, new_sha, ["pkg/a.py"])).encode()
    client = TestClient(live_app)
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(_SECRET, body),
            "X-GitHub-Delivery": "delivery-ok-1",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200, f"valid push must be 200, got {resp.status_code}: {resp.text}"
    # The LIVE handler drove apply_push on the registered pipeline: the pin advanced and the
    # new SHA is retained as a graph version (the freshness reindex actually ran).
    assert pipeline.current_sha == new_sha, "valid push must advance the pipeline's current_sha"
    assert new_sha in pipeline.graph_retention_index, "the reindex must retain the new SHA version"


def test_push_delivery_is_deduped_on_the_live_route(live_app) -> None:
    """AC-M7-002 through the live ingress: a duplicate delivery reindexes exactly once."""
    from control_plane.connect import ConnectStore, trigger_connect_index
    from control_plane.github_webhook import get_pipeline_registry
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import GraphRebuildCounter

    fixture = small_repo_fixture()
    registry = get_pipeline_registry(live_app)
    store = ConnectStore()
    install_id = store.new_install(tenant_id="tenant-gh-dup", repo_url=fixture.url)
    pipeline = trigger_connect_index(
        store, install_id, tenant_id="tenant-gh-dup", repo_url=fixture.url, registry=registry
    )
    # Attach a rebuild counter to the pipeline's persistent handler to count reindexes.
    counter = GraphRebuildCounter()
    pipeline.webhook_handler._rebuild_counter = counter

    body = json.dumps(_push_payload(fixture.url, "sha_dup", ["pkg/a.py"])).encode()
    headers = {
        "X-Hub-Signature-256": _sign(_SECRET, body),
        "X-GitHub-Delivery": "delivery-dup-1",
        "Content-Type": "application/json",
    }
    client = TestClient(live_app)
    r1 = client.post("/webhooks/github", content=body, headers=headers)
    r2 = client.post("/webhooks/github", content=body, headers=headers)  # same GUID+SHA
    assert r1.status_code == 200 and r2.status_code == 200
    assert counter.count == 1, f"duplicate delivery must reindex exactly once, got {counter.count}"


def test_unknown_repo_is_accepted_not_an_error(live_app) -> None:
    """A signed push for a repo this host has not connected is a benign 202 (never a 500)."""
    body = json.dumps(_push_payload("https://github.com/never/connected", "s", [])).encode()
    client = TestClient(live_app)
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(_SECRET, body),
            "X-GitHub-Delivery": "delivery-unknown",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 202, f"unknown-repo push must be a benign 202, got {resp.status_code}"


def test_github_route_is_live_and_classified_public_not_raw(live_app) -> None:
    """The /webhooks/github route is mounted LIVE and classifies as public (allowlisted)."""
    from libs.http.registry import classify_route, route_key

    verdict = None
    for route in live_app.routes:
        if route_key(route) == "POST /webhooks/github":
            verdict = classify_route(route)
            break
    assert verdict is not None, "the GitHub push webhook route must be mounted on the live app"
    assert verdict == "public", (
        f"/webhooks/github must classify as public (HMAC-allowlisted), got {verdict!r}"
    )
