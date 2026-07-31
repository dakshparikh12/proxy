"""relay route — the host receiver for the in-sandbox meeting MCP server (SPEC §4/§5).

POST /meetings/{id}/relay authenticates the per-meeting bearer, resolves the meeting's live
connection, and lands the agent's chosen medium on it. It is a NEVER-THROW boundary: a forged /
misdirected / malformed relay returns honest error JSON, never a 500 or a crash of the agent's
turn. The existing boot-path test proves say/chat landing + the 401 fail-closed; these cover the
remaining edges — a send fault, a malformed body, a live-runtime-with-no-connection 404, and the
relay-URL string physics — with the vendor edges faked.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class _FakeAcquire:
    async def __aenter__(self) -> object:
        return SimpleNamespace()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeDB:
    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire()


class _Registry:
    """Minimal registry the relay route resolves runtimes off (get only)."""

    def __init__(self, runtimes: dict[str, Any]) -> None:
        self._runtimes = runtimes

    def get(self, meeting_id: str) -> Any:
        return self._runtimes.get(meeting_id)


def _app_with_runtime(meeting_id: str, runtime: Any) -> Any:
    from control_plane.app import create_app

    app = create_app()
    app.state.meeting_runtimes = _Registry({meeting_id: runtime})
    return app


def _runtime(*, connection: Any, relay_token: str = "tok") -> Any:
    workroom = SimpleNamespace(relay_token=relay_token)
    return SimpleNamespace(connection=connection, workroom=workroom)


def test_relay_never_throws_on_a_send_fault() -> None:
    """A connection whose to_meeting RAISES must not surface as a 500 — the never-throw boundary
    returns honest JSON at HTTP 200 with the error named (a bad send never crashes the plane)."""
    from fastapi.testclient import TestClient

    class _BoomConnection:
        sent: list[Any] = []

        async def to_meeting(self, content: str, medium: str = "say", to: str | None = None) -> Any:
            raise RuntimeError("cartesia 503")

    app = _app_with_runtime("m-1", _runtime(connection=_BoomConnection()))
    client = TestClient(app)
    r = client.post(
        "/meetings/m-1/relay",
        json={"content": "x", "medium": "say"},
        headers={"Authorization": "Bearer tok"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "cartesia 503" in body["error"]


def test_relay_malformed_body_is_an_honest_400() -> None:
    """A non-JSON / non-dict body is the caller's own bad input -> an honest 400, never a 500."""
    from fastapi.testclient import TestClient

    class _Conn:
        sent: list[Any] = []

        async def to_meeting(self, *a: Any, **k: Any) -> Any:  # pragma: no cover - not reached
            return SimpleNamespace(ok=True, medium="say", detail="")

    app = _app_with_runtime("m-1", _runtime(connection=_Conn()))
    client = TestClient(app)
    auth = {"Authorization": "Bearer tok"}

    # non-JSON body:
    r = client.post("/meetings/m-1/relay", data="not json", headers=auth)
    assert r.status_code == 400
    # a JSON scalar (not an object):
    r = client.post("/meetings/m-1/relay", json=42, headers=auth)
    assert r.status_code == 400


def test_relay_live_runtime_but_no_connection_is_a_404() -> None:
    """A runtime that exists (so the bearer resolves) but has no connection (raced teardown, or a
    meeting that booted without a workroom) is an honest 404 — never a crash."""
    from fastapi.testclient import TestClient

    # runtime present with a relay_token (so auth passes) but connection is None:
    app = _app_with_runtime("m-1", _runtime(connection=None))
    client = TestClient(app)
    r = client.post(
        "/meetings/m-1/relay",
        json={"content": "x"},
        headers={"Authorization": "Bearer tok"},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "no live meeting"


def test_relay_forwards_medium_and_to_and_reports_the_send_outcome() -> None:
    """The route forwards the posted {content, medium, to} to the connection and echoes back the
    MeetingSend outcome (ok/medium/detail) so the agent sees what actually happened."""
    from fastapi.testclient import TestClient

    class _Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str | None]] = []
            self.sent: list[Any] = []

        async def to_meeting(self, content: str, medium: str = "say", to: str | None = None) -> Any:
            self.calls.append((content, medium, to))
            send = SimpleNamespace(ok=True, medium=medium, detail=f"to={to}")
            self.sent.append(send)
            return send

    conn = _Conn()
    app = _app_with_runtime("m-1", _runtime(connection=conn))
    client = TestClient(app)
    r = client.post(
        "/meetings/m-1/relay",
        json={"content": "psst", "medium": "dm", "to": "p-7"},
        headers={"Authorization": "Bearer tok"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["medium"] == "dm" and body["detail"] == "to=p-7"
    assert conn.calls == [("psst", "dm", "p-7")]


def test_relay_url_for_string_physics(monkeypatch) -> None:
    """relay_url_for is pure string physics: a base origin yields the full route URL; no base ->
    "" (the honest degrade: no reachable relay)."""
    from control_plane.relay import relay_url_for

    assert relay_url_for("m-9", base_url="https://p.example.com") == (
        "https://p.example.com/meetings/m-9/relay"
    )
    # trailing slash is normalized:
    assert relay_url_for("m-9", base_url="https://p.example.com/") == (
        "https://p.example.com/meetings/m-9/relay"
    )
    # no base + no env -> honest "":
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    assert relay_url_for("m-9") == ""
    # env-sourced base is honored:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://env.example.com")
    assert relay_url_for("m-9") == "https://env.example.com/meetings/m-9/relay"
