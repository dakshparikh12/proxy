"""The offer + screen sinks wired into the live MeetingConnection (Law-3 safe).

Proves the provisioner now builds BOTH sinks and hands them to ``MeetingConnection``, so a woken
turn that chooses ``medium='offer'`` (a world-touching change) or ``medium='screen'`` reaches a
real surface instead of the "not available" honest-error:

* ``to_meeting(content, medium='offer')`` STAGES a durable draft (one ``staged_drafts`` row at
  ``proposed`` + one persisted GCS bundle) and returns ``MeetingSend(ok=True)`` with an approve URL
  built from ``PUBLIC_BASE_URL`` + the accept route, AND posts the approve link into chat (per
  ``meeting_connection._route``) — and NOTHING pushes from the sandbox side (Law 3).
* ``to_meeting(url, medium='screen')`` points the Output-Media surface at the URL and returns
  ``MeetingSend(ok=True)`` with the URL — an honest surface intent, never a fake success.
* Never-throw holds: a staging fault degrades to an honest ``MeetingSend`` (no approve link), not a
  crash.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any


class _FakeConn:
    """A conn whose ``fetchrow`` mimics the ``insert_draft`` RETURNING row (a real draft_id)."""

    def __init__(self, sink: dict[str, Any]) -> None:
        self._sink = sink

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        # args = (meeting_id, kind, summary, artifact_ref, status) per repos.drafts.insert_draft
        self._sink["inserted"] = {
            "meeting_id": args[0], "kind": args[1], "summary": args[2],
            "artifact_ref": args[3], "status": args[4],
        }
        return {
            "draft_id": "draft-abc123",
            "meeting_id": args[0],
            "kind": args[1],
            "summary": args[2],
            "artifact_ref": args[3],
            "status": args[4],
            "created_at": None,
        }


class _FakeAcquire:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


from libs.db import Database  # noqa: E402 - test-local import after the fakes above


class _FakeDB(Database):
    """A real ``Database`` subclass (so ``propose_change`` takes the async pool path) whose
    ``acquire`` yields the fake conn — no live Postgres."""

    def __init__(self, sink: dict[str, Any]) -> None:
        self._conn = _FakeConn(sink)  # do NOT call super().__init__ — no real pool

    def acquire(self) -> _FakeAcquire:  # type: ignore[override]
        return _FakeAcquire(self._conn)


class _Speak:
    def __init__(self) -> None:
        self.said: list[str] = []

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def cut(self) -> None:
        return None


class _Room:
    """RoomSink — records chat posts. NEVER a push/send-to-origin surface (Law 3)."""

    def __init__(self) -> None:
        self.chats: list[str] = []

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        self.chats.append(message)

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        self.chats.append(f"dm:{message}")

    async def mute(self, bot_id: str) -> None:
        return None

    async def unmute(self, bot_id: str) -> None:
        return None


def _wired_connection(db: Any, meeting_id: str = "m-sink-1") -> Any:
    """A REAL MeetingConnection with the REAL provisioner-built offer + screen + audio_mute sinks."""
    from control_plane.provisioner import _build_meeting_sinks
    from in_meeting.meeting_connection import MeetingConnection

    offer, screen, audio_mute = _build_meeting_sinks(db=db, meeting_id=meeting_id, tenant_id="t-1")
    return MeetingConnection(
        speak=_Speak(), room=_Room(), bot_id="bot-1",
        offer=offer, screen=screen, audio_mute=audio_mute,
    )


def test_offer_stages_a_draft_returns_approve_url_and_posts_to_chat(monkeypatch: Any) -> None:
    """medium='offer' → STAGES a draft (proposed row + GCS bundle) + returns ok=True with the
    approve URL, and posts the approve link to chat — nothing pushes from the sandbox side."""
    from workroom import objectstore

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://proxy.example.com")
    sink: dict[str, Any] = {}
    put_calls: list[tuple[str, str]] = []
    real_put = objectstore.put

    def _spy_put(ref: str, content: str) -> str:
        put_calls.append((ref, content))
        return real_put(ref, content)

    monkeypatch.setattr(objectstore, "put", _spy_put)

    connection = _wired_connection(_FakeDB(sink), meeting_id="m-offer-1")

    async def _run() -> None:
        send = await connection.to_meeting("the tz.ts:88 patch\nfull body here", medium="offer")
        # (1) ok=True with the approve URL as the detail:
        assert send.ok is True
        assert send.medium == "offer"
        assert send.detail == "https://proxy.example.com/m/m-offer-1/drafts/draft-abc123/accept"
        # (2) a durable draft was STAGED: one proposed staged_drafts row + one GCS bundle:
        assert sink["inserted"]["status"] == "proposed"
        assert sink["inserted"]["kind"] == "code-change"
        assert sink["inserted"]["meeting_id"] == "m-offer-1"
        assert len(put_calls) == 1                       # exactly one persisted bundle
        assert "full body here" in put_calls[0][1]       # the artifact body was persisted verbatim
        # (3) the approve link was posted into chat so a human can click it (Law 3):
        assert any("approve:" in c and "draft-abc123/accept" in c for c in connection.room.chats)
        # (4) NOTHING pushed from the sandbox side — the only room verb used was post_chat (the
        #     approve link). There is no push/send-to-origin surface on the connection at all.
        assert not any(c.startswith("dm:") for c in connection.room.chats)

    asyncio.run(_run())


def test_screen_points_output_media_surface_and_returns_url() -> None:
    """medium='screen' with a URL → a real render frame lands on the Output-Media channel and the
    send returns ok=True with an HONEST outcome detail (showing <url>) — never a fabricated success.
    """
    from in_meeting import output_media

    url = "https://proxy.example.com/render/diff/xyz"
    connection = _wired_connection(_FakeDB({}), meeting_id="m-screen-1")

    async def _run() -> None:
        send = await connection.to_meeting(url, medium="screen")
        assert send.ok is True
        assert send.medium == "screen"
        # honest human-readable outcome, not the bare fabricated url:
        assert "showing" in send.detail.lower()
        assert url in send.detail
        # the REAL output-media channel recorded the shown surface (the honest intent):
        assert output_media.channel_for("m-screen-1").screen_url() == url

    asyncio.run(_run())
    output_media.close_channel("m-screen-1")


def test_offer_without_public_base_url_degrades_honestly(monkeypatch: Any) -> None:
    """No PUBLIC_BASE_URL ⇒ the draft is still STAGED, but the approve URL is "" (honest degrade —
    no reachable approve link), and the send is still ok=True with an empty detail. Never a crash,
    never a push."""
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    sink: dict[str, Any] = {}
    connection = _wired_connection(_FakeDB(sink), meeting_id="m-offer-2")

    async def _run() -> None:
        send = await connection.to_meeting("a change", medium="offer")
        assert send.ok is True
        assert send.detail == ""                         # no reachable approve link, honest
        assert sink["inserted"]["status"] == "proposed"  # the draft was still staged durably

    asyncio.run(_run())


def test_offer_staging_fault_is_never_a_crash() -> None:
    """A staging fault (dead DB) degrades to an honest MeetingSend, never a raise (never-throw)."""
    class _DeadDB(Database):
        def __init__(self) -> None:
            pass  # no real pool

        def acquire(self) -> Any:  # type: ignore[override]
            raise RuntimeError("substrate down")

    connection = _wired_connection(_DeadDB(), meeting_id="m-offer-3")

    async def _run() -> None:
        send = await connection.to_meeting("a change", medium="offer")
        # the connection's own never-throw returns ok=True with an empty approve URL (the sink
        # swallowed the fault and returned ""); it is never a raised exception.
        assert send.medium == "offer"
        assert send.detail == ""

    asyncio.run(_run())
