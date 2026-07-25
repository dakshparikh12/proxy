"""Acceptance tests for node ``orchestrator.wake-turn`` (04 §3.2).

The wake turn is the whole agent, precisely: ONE persistent Claude SDK session
per meeting — a wake = (event + a compacted state-digest) in → tool calls out,
streaming through the provider seam via :class:`~agentkit.BehaviorRunner` with a
mounted behavior. This suite drives the REAL wake-turn node end to end:

    harness.wake_turn.WakeTurn.wake(event, ...)
        → agentkit.resume_with_fallback(runner, behavior, inputs, resume_id, …)
        → agentkit.BehaviorRunner.run(...)  (mounts the behavior, isolation triad)
        → stream_deltas(provider.stream(...))  ← the injected FAKE provider seam

Only the provider is scripted (its ``stream`` is the injected seam, exactly like
``test_session_durability.py`` / ``test_runner_resume_fallback.py``) — no live CLI
call. Everything above the seam is the real product path.

The node's load-bearing contracts, each a test below:

* **One persistent session, resumed each wake (§3.2 / §3.5 Tier-1).** The FIRST
  wake carries no resume; its ``INIT`` chunk's ``session_id`` is captured and
  persisted, and EVERY later wake resumes that same ``session_id`` — one session
  lives for the whole meeting.
* **Event + compacted state-digest in (§3.2).** A wake is primed by the compact
  state digest (tasks in flight / mouth free-or-busy / component health), never
  raw session history — the digest string reaches the model on the prompt as
  DATA, and the wake event rides after it.
* **Tool calls out through the seam (§3.4).** The model's reply is TOOL_USE
  chunks routed through the seam; the wake surfaces them (a ``speak`` call is the
  turn's product) — nothing is hard-coded between the event and the tool calls.
* **State-digest compaction cadence (CANONICAL §10.2).** The digest is
  regenerated **every ~15 wakes OR on a material-state change**, and is otherwise
  reused — so a sporadic wake pays only the event, not a fresh digest.
* **Notes are the durable memory read via ``GET /internal/notes`` (CANONICAL
  §11.4).** The turn carries ``notes_ref = meeting_id`` and reads live notes on
  demand through the internal reader — the notes object is NEVER carried inline in
  the session history (so a compaction can never drop meeting state).
"""
from __future__ import annotations

import pytest

from libs.contracts import AgentChunk

from harness.wake_turn import (
    DEFAULT_COMPACTION_EVERY,
    StateDigest,
    WakeEvent,
    WakeTurn,
)


# ── the injected provider seam (scripted; records what each wake saw) ─────────


class ScriptedProvider:
    """Yields a per-wake scripted ``AgentChunk`` stream and records what each wake
    saw (the resume pointer + the rendered prompt), so a test can assert the
    persistent-session resume flow and the event+digest priming."""

    def __init__(self, per_wake):
        self._per_wake = list(per_wake)
        self.calls = 0
        self.seen_resume: list = []
        self.seen_prompts: list[str] = []

    def stream(self, prompt, query):
        idx = self.calls
        self.calls += 1
        self.seen_resume.append(query.resume)
        self.seen_prompts.append(prompt)
        chunks = self._per_wake[idx] if idx < len(self._per_wake) else []

        async def gen():
            for ch in chunks:
                yield ch

        return gen()


def _wake_chunks(session_id: str, msg_id: str, said: str):
    """A normal wake: INIT (carries session_id), one TOOL_USE (speak), RESULT."""
    return [
        AgentChunk(type="INIT", metadata={"session_id": session_id}),
        AgentChunk(
            type="TOOL_USE",
            metadata={"name": "speak", "input": {"text": said}, "id": msg_id},
        ),
        AgentChunk(
            type="RESULT",
            metadata={"total_cost_usd": 0.01, "num_turns": 1, "session_id": session_id},
        ),
    ]


async def _drain(agen):
    out = []
    async for ch in agen:
        out.append(ch)
    return out


# ── Notes reader double: proves the durable read path, never carried inline ───


class RecordingNotesReader:
    """Stands in for ``GET /internal/notes/{meeting_id}`` (CANONICAL §11.4).

    Records every ``notes_ref`` it was asked to resolve and returns the folded
    notes text. The wake turn reads notes THROUGH this on demand — it never
    embeds the notes object in the session history."""

    def __init__(self, notes_text: str = "DECISION: ship Friday behind the migration."):
        self._text = notes_text
        self.reads: list[str] = []

    async def __call__(self, notes_ref: str) -> str:
        self.reads.append(str(notes_ref))
        return self._text


# ---------------------------------------------------------------------------
# One persistent session, resumed each wake (§3.2 / §3.5 Tier-1)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_one_persistent_session_captured_then_resumed_every_wake():
    """The FIRST wake carries no resume; its INIT session_id is persisted and
    EVERY later wake resumes that same id — one session for the whole meeting."""
    provider = ScriptedProvider(
        per_wake=[
            _wake_chunks("sdk-sess-1", "m1", "on it"),
            _wake_chunks("sdk-sess-1", "m2", "the retry logic lives in checkout.py"),
            _wake_chunks("sdk-sess-1", "m3", "done"),
        ]
    )
    turn = WakeTurn(meeting_id="mtg-1", provider=provider)

    for text in ("Proxy, ack?", "where's the retry logic?", "thanks"):
        await _drain(turn.wake(WakeEvent(text=text, speaker="Alice")))

    # Wake 1 had NO resume (no session yet); wakes 2 and 3 resumed the captured id.
    assert provider.seen_resume == [None, "sdk-sess-1", "sdk-sess-1"]
    # The node persisted exactly one session id across the meeting.
    assert turn.session_id == "sdk-sess-1"
    assert provider.calls == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_id_persisted_from_init_chunk_only():
    """The session id is captured from the ``INIT`` chunk (§3.5 Tier-1) — before
    the first wake there is no session to resume."""
    provider = ScriptedProvider(per_wake=[_wake_chunks("sess-abc", "m1", "hi")])
    turn = WakeTurn(meeting_id="mtg-2", provider=provider)

    assert turn.session_id is None  # nothing captured before the first wake
    await _drain(turn.wake(WakeEvent(text="Proxy?", speaker="Bob")))
    assert turn.session_id == "sess-abc"  # captured from INIT.metadata.session_id


# ---------------------------------------------------------------------------
# Event + compacted state-digest in (§3.2)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_wake_primes_the_model_with_event_and_compact_state_digest():
    """A wake is primed by the compact state digest (tasks in flight / mouth
    free-or-busy / component health) AND the wake event — both reach the model as
    DATA on the prompt, never as instructions."""
    provider = ScriptedProvider(per_wake=[_wake_chunks("s1", "m1", "on it")])
    digest = StateDigest(
        tasks_in_flight=("build the export report",),
        mouth_busy=False,
        component_health="all green",
    )
    turn = WakeTurn(meeting_id="mtg-3", provider=provider, digest=digest)

    await _drain(turn.wake(WakeEvent(text="Proxy, run the export?", speaker="Cara")))

    prompt = provider.seen_prompts[0]
    # The compact digest reached the model (tasks in flight + mouth state + health).
    assert "build the export report" in prompt
    assert "component health" in prompt.lower() or "all green" in prompt
    # The wake event rode along after the digest.
    assert "Proxy, run the export?" in prompt
    assert "Cara" in prompt


@pytest.mark.integration
@pytest.mark.asyncio
async def test_digest_is_compact_never_raw_session_history():
    """The turn is primed by the COMPACT digest, not raw history (§3.2): the digest
    render is a small bounded summary, and no per-turn transcript blob is inlined."""
    digest = StateDigest(
        tasks_in_flight=("t1", "t2"),
        mouth_busy=True,
        component_health="scribe degraded",
    )
    rendered = digest.render()
    # Compact: a handful of lines, not a raw transcript dump.
    assert rendered.count("\n") < 10
    assert "t1" in rendered and "t2" in rendered
    assert "busy" in rendered.lower()  # mouth free/busy is in the digest
    assert "scribe degraded" in rendered


# ---------------------------------------------------------------------------
# Tool calls out through the seam (§3.4)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_wake_surfaces_tool_calls_from_the_seam_nothing_hardcoded():
    """The model's reply is TOOL_USE chunks routed through the seam; the wake
    surfaces them (a ``speak`` call is the turn's product). Nothing is hard-coded
    between the event and the tool call — the digest→model→tool path is the whole
    mechanism (§3.4 / Law 4)."""
    provider = ScriptedProvider(
        per_wake=[_wake_chunks("s1", "m1", "the retry logic is in checkout.py:88")]
    )
    turn = WakeTurn(meeting_id="mtg-4", provider=provider)

    out = await _drain(turn.wake(WakeEvent(text="where's the retry logic?", speaker="Dee")))

    tool_uses = [c for c in out if c.type == "TOOL_USE"]
    assert [c.metadata["name"] for c in tool_uses] == ["speak"]
    assert tool_uses[0].metadata["input"]["text"] == "the retry logic is in checkout.py:88"
    # The turn also exposes the tool calls as a convenience list.
    said = turn.last_tool_calls
    assert said and said[0].name == "speak"


# ---------------------------------------------------------------------------
# State-digest compaction cadence (CANONICAL §10.2)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_digest_regenerated_every_n_wakes():
    """The digest is regenerated every ~15 wakes (CANONICAL §10.2) and reused in
    between — a sporadic wake pays only the event, not a fresh digest."""
    n = DEFAULT_COMPACTION_EVERY
    provider = ScriptedProvider(
        per_wake=[_wake_chunks("s1", f"m{i}", "ok") for i in range(n + 1)]
    )
    regen_calls = {"n": 0}

    def regenerate() -> StateDigest:
        regen_calls["n"] += 1
        return StateDigest(tasks_in_flight=(f"regen-{regen_calls['n']}",))

    turn = WakeTurn(meeting_id="mtg-5", provider=provider, regenerate_digest=regenerate)

    for i in range(n + 1):
        await _drain(turn.wake(WakeEvent(text=f"wake {i}", speaker="Eve")))

    # First wake builds the digest once; it is REUSED for wakes 2..N, then
    # regenerated on wake N+1 (the ~15-wake cadence) — exactly 2 regenerations.
    assert regen_calls["n"] == 2
    assert turn.wake_count == n + 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_digest_regenerated_on_material_state_change():
    """A material-state change forces an immediate digest regeneration (CANONICAL
    §10.2) — it does not wait for the ~15-wake cadence."""
    provider = ScriptedProvider(
        per_wake=[_wake_chunks("s1", f"m{i}", "ok") for i in range(4)]
    )
    regen_calls = {"n": 0}

    def regenerate() -> StateDigest:
        regen_calls["n"] += 1
        return StateDigest(tasks_in_flight=(f"regen-{regen_calls['n']}",))

    turn = WakeTurn(meeting_id="mtg-6", provider=provider, regenerate_digest=regenerate)

    await _drain(turn.wake(WakeEvent(text="w0", speaker="Fay")))  # builds digest (1)
    await _drain(turn.wake(WakeEvent(text="w1", speaker="Fay")))  # reuses it
    turn.mark_material_change()  # a task completed / component health flipped
    await _drain(turn.wake(WakeEvent(text="w2", speaker="Fay")))  # forces regen (2)
    await _drain(turn.wake(WakeEvent(text="w3", speaker="Fay")))  # reuses it

    assert regen_calls["n"] == 2  # initial build + one on the material change
    # The freshly regenerated digest primed the post-change wake.
    assert "regen-2" in provider.seen_prompts[2]


# ---------------------------------------------------------------------------
# Notes = durable memory, read via GET /internal/notes (CANONICAL §11.4)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_notes_read_on_demand_via_internal_reader_not_carried_inline():
    """The turn reads live notes THROUGH the internal reader (``notes_ref =
    meeting_id``) on demand — the notes object is never embedded in the session
    history, so a compaction can never drop meeting state (CANONICAL §11.4)."""
    provider = ScriptedProvider(per_wake=[_wake_chunks("s1", "m1", "ok")])
    reader = RecordingNotesReader(notes_text="DECISION: migration lands before Friday.")
    turn = WakeTurn(meeting_id="mtg-7", provider=provider, notes_reader=reader)

    await _drain(
        turn.wake(WakeEvent(text="what did we decide?", speaker="Gus"), read_notes=True)
    )

    # The internal reader was hit with notes_ref = meeting_id.
    assert reader.reads == ["mtg-7"]
    # The folded notes reached the model on this turn's prompt (durable memory).
    assert "migration lands before Friday" in provider.seen_prompts[0]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_notes_ref_is_the_meeting_id_a_handle_not_the_object():
    """``notes_ref`` handed to the wake behavior is the meeting_id (a HANDLE), never
    a materialised notes object (CANONICAL §1.3 / §11.4)."""
    provider = ScriptedProvider(per_wake=[_wake_chunks("s1", "m1", "ok")])
    turn = WakeTurn(meeting_id="mtg-8", provider=provider)

    await _drain(turn.wake(WakeEvent(text="status?", speaker="Hal")))

    # The behavior's notes_ref input is the meeting_id handle on the prompt.
    assert "mtg-8" in provider.seen_prompts[0]


# ---------------------------------------------------------------------------
# Durability is inherited: a recycle mid-meeting replays from the transcript plane
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stale_session_after_recycle_replays_and_notifies():
    """A wake that lands after a recycle: resume fails stale, the wake node's
    inherited §3.5 fallback rebuilds from the transcript-plane history_fn, emits
    the restored notice, and retries WITHOUT resume — the meeting is not forgotten."""
    from libs.agentkit.resume import RESTORED_NOTICE

    provider = ScriptedProvider(
        per_wake=[
            # Wake 1: establishes the session.
            _wake_chunks("sess-1", "m1", "on it"),
            # Wake 2: the resumed session is gone (recycle landed us elsewhere).
            [AgentChunk(type="ERROR", metadata={"message": "no conversation found with session id sess-1"})],
            # Retry WITHOUT resume, context rebuilt from history → a coherent answer.
            [
                AgentChunk(type="TEXT", text="Right — the migration lands first.", metadata={"msg_id": "m2"}),
                AgentChunk(type="RESULT", metadata={"total_cost_usd": 0.02, "session_id": "sess-2"}),
            ],
        ]
    )

    async def history():
        return "Alice: ship Friday.\nBob: migration must land first."

    turn = WakeTurn(meeting_id="mtg-9", provider=provider, history_fn=history)

    await _drain(turn.wake(WakeEvent(text="hi", speaker="Ann")))  # session established
    out = await _drain(turn.wake(WakeEvent(text="what's the plan?", speaker="Ann")))

    texts = [c.text for c in out if c.type == "TEXT"]
    assert texts and texts[0] == RESTORED_NOTICE  # transparent recovery, not silent
    assert any("migration lands first" in t for t in texts)
    # The recovery re-established the session pointer to the new session id.
    assert turn.session_id == "sess-2"
