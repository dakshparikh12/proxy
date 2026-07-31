"""webhooks._dispatch_meeting_event — the reactive-workroom meeting spine's router.

A Recall callback drives the meeting: in_call -> launch (claim + provision), transcript.data ->
feed a final line into the reactive loop, participant_events.chat_message -> feed a chat line
(is_chat), call_ended/removed -> end the meeting. Any other event, an unresolvable bot, or a
transcript before the runtime exists is a SAFE no-op so the drain still marks the row processed
(never a poison row). These exercise the REAL dispatcher with a fake registry/runtime + a faked
bot resolve — no Recall, no DB.
"""
from __future__ import annotations

import asyncio
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


class _FakeRuntime:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, float, bool]] = []
        self.raise_on_ingest = False

    async def ingest_line(self, speaker: str, text: str, *, ts: float = 0.0,
                          is_chat: bool = False) -> None:
        if self.raise_on_ingest:
            raise RuntimeError("ingest boom")
        self.lines.append((speaker, text, ts, is_chat))


class _FakeRegistry:
    def __init__(self, runtimes: dict[str, _FakeRuntime] | None = None) -> None:
        self._runtimes = runtimes or {}
        self.ended: list[tuple[str, str]] = []

    def get(self, meeting_id: str) -> _FakeRuntime | None:
        return self._runtimes.get(meeting_id)

    async def end_meeting(self, meeting_id: str, *, reason: str = "call_ended") -> None:
        self.ended.append((meeting_id, reason))
        self._runtimes.pop(meeting_id, None)


def _patch_resolve(monkeypatch: Any, meeting_id: str | None = "m-1") -> None:
    from libs.db import repos

    async def _resolve(conn: object, bot_id: str) -> dict[str, Any] | None:
        if meeting_id is None:
            return None
        return {"id": meeting_id, "repo_id": 1, "pinned_sha": None}

    monkeypatch.setattr(repos.meetings, "get_by_bot_id", _resolve)


def test_in_call_dispatches_to_launch(monkeypatch) -> None:
    """An in_call event calls the provisioner launch callback (claim + provision) and nothing
    else — the provisioner resolves the bot itself and no-ops on a loss."""
    from control_plane.webhooks import _dispatch_meeting_event

    launched: list[dict[str, Any]] = []

    async def _launch(payload: dict[str, Any]) -> None:
        launched.append(payload)

    async def _run() -> None:
        payload = {"event": "bot.in_call", "data": {"bot_id": "b1"}}
        await _dispatch_meeting_event(
            payload, db=_FakeDB(), registry=_FakeRegistry(), launch=_launch
        )
        assert launched == [payload]

    asyncio.run(_run())


def test_transcript_data_feeds_a_final_line_into_the_loop(monkeypatch) -> None:
    """A transcript.data final line is adapted ({words, speaker, timestamp}) and fed into the
    meeting's runtime as a voice line (is_chat False)."""
    from control_plane.webhooks import _dispatch_meeting_event

    _patch_resolve(monkeypatch, "m-1")
    rt = _FakeRuntime()

    async def _run() -> None:
        payload = {
            "event": "transcript.data",
            "data": {"bot_id": "b1", "words": "proxy, where is the helper?",
                     "speaker": "Bob", "timestamp": 12.5},
        }
        await _dispatch_meeting_event(
            payload, db=_FakeDB(), registry=_FakeRegistry({"m-1": rt})
        )
        assert rt.lines == [("Bob", "proxy, where is the helper?", 12.5, False)]

    asyncio.run(_run())


def test_chat_message_feeds_as_a_chat_line(monkeypatch) -> None:
    """A participant_events.chat_message is adapted from the documented nested envelope and fed
    with is_chat=True (so the reactive loop's chat gate scans it for @proxy)."""
    from control_plane.webhooks import _dispatch_meeting_event

    _patch_resolve(monkeypatch, "m-1")
    rt = _FakeRuntime()

    async def _run() -> None:
        payload = {
            "event": "participant_events.chat_message",
            "data": {
                "bot": {"id": "b1"},
                "data": {
                    "participant": {"id": "p9", "name": "Ann"},
                    "data": {"text": "@proxy status?", "to": "everyone"},
                },
            },
        }
        await _dispatch_meeting_event(
            payload, db=_FakeDB(), registry=_FakeRegistry({"m-1": rt})
        )
        assert rt.lines == [("Ann", "@proxy status?", 0.0, True)]

    asyncio.run(_run())


def test_call_ended_ends_the_meeting_with_a_derived_reason(monkeypatch) -> None:
    """A terminal callback ends the meeting; the reason is DERIVED from the payload (data.reason
    preferred, else the event name), never a synthesized string."""
    from control_plane.webhooks import _dispatch_meeting_event

    _patch_resolve(monkeypatch, "m-1")

    async def _run() -> None:
        reg = _FakeRegistry({"m-1": _FakeRuntime()})
        payload = {"event": "bot.call_ended", "data": {"bot_id": "b1", "reason": "bot_removed"}}
        await _dispatch_meeting_event(payload, db=_FakeDB(), registry=reg)
        assert reg.ended == [("m-1", "bot_removed")]

        # no explicit reason -> fall back to the event name itself:
        reg2 = _FakeRegistry({"m-1": _FakeRuntime()})
        await _dispatch_meeting_event(
            {"event": "call_ended", "data": {"bot_id": "b1"}}, db=_FakeDB(), registry=reg2
        )
        assert reg2.ended == [("m-1", "call_ended")]

    asyncio.run(_run())


def test_transcript_before_the_runtime_exists_is_a_safe_no_op(monkeypatch) -> None:
    """A transcript that arrives before in_call provisioned the runtime is a fail-closed no-op —
    it never raises and never feeds a phantom runtime (the row still drains)."""
    from control_plane.webhooks import _dispatch_meeting_event

    _patch_resolve(monkeypatch, "m-1")

    async def _run() -> None:
        reg = _FakeRegistry({})  # no runtime yet
        payload = {
            "event": "transcript.data",
            "data": {"bot_id": "b1", "words": "proxy hi", "speaker": "Bob", "timestamp": 1},
        }
        # must not raise:
        await _dispatch_meeting_event(payload, db=_FakeDB(), registry=reg)
        assert reg.ended == []  # nothing happened

    asyncio.run(_run())


def test_unknown_bot_and_unknown_event_are_safe_no_ops(monkeypatch) -> None:
    """An unresolvable bot never feeds/ends a runtime; an unrecognized event returns before any
    resolve. Both are safe no-ops so the drain still marks the row processed."""
    from control_plane.webhooks import _dispatch_meeting_event

    async def _run() -> None:
        # unrecognized event: returns immediately (resolve never consulted):
        rt = _FakeRuntime()
        reg = _FakeRegistry({"m-1": rt})
        await _dispatch_meeting_event(
            {"event": "bot.speaking", "data": {"bot_id": "b1"}}, db=_FakeDB(), registry=reg
        )
        assert rt.lines == [] and reg.ended == []

        # a transcript with an UNRESOLVABLE bot -> fail closed, no feed:
        _patch_resolve(monkeypatch, None)
        await _dispatch_meeting_event(
            {"event": "transcript.data",
             "data": {"bot_id": "ghost", "words": "hi", "speaker": "X", "timestamp": 0}},
            db=_FakeDB(), registry=reg,
        )
        assert rt.lines == []

    asyncio.run(_run())


def test_empty_transcript_and_empty_chat_are_no_ops(monkeypatch) -> None:
    """A transcript body with no words, and a chat with no text, adapt to None -> nothing is fed
    (a safe no-op, never a blank line into the loop)."""
    from control_plane.webhooks import _dispatch_meeting_event

    _patch_resolve(monkeypatch, "m-1")
    rt = _FakeRuntime()

    async def _run() -> None:
        reg = _FakeRegistry({"m-1": rt})
        await _dispatch_meeting_event(
            {"event": "transcript.data", "data": {"bot_id": "b1", "words": "   ", "speaker": "B"}},
            db=_FakeDB(), registry=reg,
        )
        await _dispatch_meeting_event(
            {"event": "participant_events.chat_message",
             "data": {"bot": {"id": "b1"}, "data": {"participant": {"name": "A"}, "data": {}}}},
            db=_FakeDB(), registry=reg,
        )
        assert rt.lines == []

    asyncio.run(_run())


def test_ingest_never_raise_boundary_is_held(monkeypatch) -> None:
    """The transcript/chat feed is a never-raise boundary: an ingest_line that RAISES is caught
    and logged so the row still drains (never a poison row) — the dispatch itself does not raise."""
    from control_plane.webhooks import _dispatch_meeting_event

    _patch_resolve(monkeypatch, "m-1")
    rt = _FakeRuntime()
    rt.raise_on_ingest = True

    async def _run() -> None:
        reg = _FakeRegistry({"m-1": rt})
        # a raising ingest must be swallowed — the dispatch returns normally:
        await _dispatch_meeting_event(
            {"event": "transcript.data",
             "data": {"bot_id": "b1", "words": "proxy hi", "speaker": "B", "timestamp": 1}},
            db=_FakeDB(), registry=reg,
        )
        # and likewise for chat:
        await _dispatch_meeting_event(
            {"event": "participant_events.chat_message",
             "data": {"bot": {"id": "b1"},
                      "data": {"participant": {"name": "A"}, "data": {"text": "@proxy x"}}}},
            db=_FakeDB(), registry=reg,
        )

    asyncio.run(_run())


def test_in_call_without_a_launch_callback_is_a_pure_drain_no_op(monkeypatch) -> None:
    """launch=None keeps the pure-drain behaviour: an in_call is a no-op (durability accounting
    only), starting no meeting."""
    from control_plane.webhooks import _dispatch_meeting_event

    async def _run() -> None:
        reg = _FakeRegistry({})
        # must not raise and must not end/feed anything:
        await _dispatch_meeting_event(
            {"event": "in_call", "data": {"bot_id": "b1"}},
            db=_FakeDB(), registry=reg, launch=None,
        )
        assert reg.ended == []

    asyncio.run(_run())


def test_meeting_end_reason_and_bot_id_helpers() -> None:
    """The payload helpers: bot id resolves from the flat form AND the participant-events bot
    object; the end reason prefers data.reason then the event name."""
    from control_plane.webhooks import _bot_id, _meeting_end_reason

    assert _bot_id({"bot_id": "flat"}) == "flat"
    assert _bot_id({"data": {"bot_id": "nested"}}) == "nested"
    assert _bot_id({"data": {"bot": {"id": "obj"}}}) == "obj"
    assert _bot_id({"data": {}}) is None

    assert _meeting_end_reason({"data": {"reason": "kicked"}}) == "kicked"
    assert _meeting_end_reason({"event": "bot.removed", "data": {}}) == "bot.removed"
