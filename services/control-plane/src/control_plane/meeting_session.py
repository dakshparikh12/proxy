"""``meeting_session`` — the reactive loop that ties the workroom to the meeting.

This is the entire in-meeting spine, in words (SPEC §3): a transcript line arrives → a cheap
word-bounded wake gate decides whether Proxy is addressed → on a wake, ONLY the new transcript
delta since the last wake is inlined into the turn, so the whole meeting ACCUMULATES in the warm
session's cached conversation (the agent just *knows* the room; only the delta + the ask are fresh
per wake — SPEC §3). The reactive turn runs in the workroom and the agent responds through the one
meeting connection. No wake ⇒ nothing happens (idle costs nothing). ``MEETING_NOTES.md`` is written
continuously too, but ONLY as a crash/reconnect recovery record — it is NOT the primary recall path
(the resident cache is), so a woken turn never has to read it just to know what was said.

This is a **driver, not a decision** (Law 4): the only judgement in *our* code is the
cheap "is Proxy addressed?" gate (word-bounded ``proxy`` / ``@proxy`` — physics, not a
situation→action mapping). WHAT Proxy does and HOW it responds is entirely the agent's,
made live inside the workroom and carried out over the connection. A wake calls
:meth:`Workroom.run_ask`; the response is ALWAYS the agent's own ``to_meeting`` choices —
either relayed live during the turn, or replayed afterward from the agent's recorded intents
(honoring its chosen mediums). We never speak our own prose: a clean turn with zero intents is
the agent choosing silence (cross-talk), and an errored turn that delivered nothing gets ONE
honest degrade line so a needed response is never met with total silence.

A wake runs as a BACKGROUND task so the room keeps flowing while Proxy works (monitor-
while-working, §3/§6). Nothing here ever raises into the drain: a failed wake is an honest
no-op the meeting survives.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: The speaker label Proxy's own audio carries — never treated as a human (no self-wake,
#: no self-barge-in). Proxy's own lines must never wake it.
PROXY_SPEAKER = "Proxy"

#: Rolling window of recent transcript lines materialized into ``MEETING_NOTES.md`` each line.
#: That file is ONLY the crash/reconnect recovery record (a woken turn recalls from the resident
#: cache, never by reading it), so a bounded tail is all recovery needs; it also keeps the per-line
#: write O(1) bytes (a full rewrite was O(N) → O(N^2) over a long meeting).
_TRANSCRIPT_WINDOW = 400

#: The cheap always-on wake gate: a word-bounded "proxy" (voice) / "@proxy" (chat). This
#: is physics (a name being called), not a situation→action rule — WHAT to do on a wake is
#: the agent's live judgement. Word-bounded so "proxy server" said in passing is filtered
#: at least by the boundary; a bounded model confirm is the follow-up refinement.
_VOICE_ADDR = re.compile(r"\bproxy\b", re.IGNORECASE)
_CHAT_ADDR = re.compile(r"@proxy\b", re.IGNORECASE)

#: Self-echo suppression — the headphones-optional guard. Without headphones, Proxy's own voice
#: bleeds from the room speakers into a human's mic and returns on the transcript MISLABELED as that
#: human, so the speaker-name self-wake filter can't catch it. We instead match the incoming line
#: against what Proxy actually SAID (the connection's ``spoken`` log): a line that reproduces Proxy's
#: recent speech is Proxy's own echo — relabeled to Proxy so it is attributed correctly and never
#: re-wakes Proxy. The bar is deliberately strict (a min length + a high share of the incoming words
#: present in the recent spoken line) so a brief human reply that happens to reuse a word or two of
#: Proxy's is NOT swallowed; the window spans playback + STT latency.
_ECHO_WINDOW_S = 45.0
_ECHO_MIN_TOKENS = 4
_ECHO_CONTAINMENT = 0.7

#: Barge-in debounce (Law 3). A human talking over Proxy stops its speech, but STT emits stray
#: sub-onset blips — a single filler token ("um", "uh"), a partial word, a cough transcribed as one
#: syllable. Cutting Proxy mid-sentence on one of those would make it flinch at noise. So a line only
#: counts as a barge-in once it carries at least this many word tokens — a real interjection, not a
#: blip. This is physics (a floor on "someone is actually talking"), not a situation→action rule.
_BARGE_MIN_TOKENS = 2

#: ASK → ANSWER → CONTINUE. When Proxy asks the room a question (or names a blocker) and delivers no
#: completed artifact, its turn ends waiting on a human reply — but a natural reply ("yes, the second
#: one", "use Postgres") does NOT name Proxy, so the name-gate would ignore it and the task would
#: stall until someone re-addressed Proxy. Instead we latch a PENDING QUESTION: the next substantive
#: human line is treated as the answer and Proxy is woken to CONTINUE the same task with that context,
#: no name needed. This is physics-narrow (a floor on "the reply is real" + a short expiry), not a
#: situation→action rule — WHAT Proxy does with the answer is still entirely the agent's live call.
#:
#: A continuation reply must carry at least this many word tokens (a real answer, not an "um"/blip);
#: shorter noise leaves the pending question standing for the actual reply.
_CONTINUE_MIN_TOKENS = 2
#: How long a pending question stays live. After this, an unrelated later line is NOT hijacked as an
#: answer — the moment has passed and the name-gate is back in sole control (no stale continuation).
_CONTINUE_TIMEOUT_S = 180.0

#: FOLLOW-UP WINDOW (F1). Right after Proxy finishes a spoken turn — OR is barged in mid-utterance —
#: the exchange is still LIVE: the human's very next lines are almost always still to Proxy ("cool,
#: the audio was choppy last time…") but rarely re-say the name. So for a SHORT window after any turn
#: that DELIVERED, and after any barge-in (being interrupted IS mid-exchange), a substantive human
#: line is ROUTED to the model's judgment even without "proxy" — the window only routes; the model's
#: own [SILENT] verdict is the over-fire guard (it proves it stays quiet when the line isn't for it).
#: The window is SHORT and REFRESHED whenever an in-window wake itself delivers (the exchange is still
#: going); it expires SILENTLY on timeout or the instant a new explicit address starts a fresh turn.
#: This is physics-narrow (a short time gate + a real-line floor), not a situation→action rule.
_FOLLOW_UP_WINDOW_S = 15.0
#: A follow-up line must carry at least this many word tokens to route (a real line, not an "um"/blip).
_FOLLOW_UP_MIN_TOKENS = 2


def _looks_like_question(text: str) -> bool:
    """True if a room-facing delivery is Proxy ASKING the room something (a clarifying question or a
    named blocker it needs an answer to) rather than a completed result. The narrow, robust signal is
    a trailing '?': Proxy ended its turn on a question to the room. Kept deliberately simple (physics,
    not a classifier) — a false negative just means the reply must re-address Proxy by name as before,
    and a false positive is bounded by the min-token + expiry guards on the continuation itself."""
    return text.rstrip().endswith("?")


def _is_barge_in(text: str) -> bool:
    """True if ``text`` is a real human interjection worth stopping Proxy's speech for — i.e. it
    carries at least ``_BARGE_MIN_TOKENS`` word tokens. A sub-onset blip (a lone filler token or a
    fragment) is below the floor and does NOT cut, so Proxy never flinches at STT noise (Law 3)."""
    return len(_echo_tokens(text)) >= _BARGE_MIN_TOKENS


def _echo_tokens(text: str) -> list[str]:
    """Lowercase alphanumeric word tokens — the normalized form for order-insensitive echo matching
    (an echo's STT drops punctuation/casing and often captures only a partial span, so we compare
    token sets, not the exact string)."""
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _is_self_echo(text: str, spoken: Sequence[tuple[float, str]], now: float) -> bool:
    """True if ``text`` is Proxy's own voice echoing back — it reproduces a line Proxy SAID within
    the echo window. Label-independent (works when the echo returns mislabeled as a human on a
    no-headphones mic). Requires a minimum length and that a high share of the incoming tokens appear
    in the recent spoken line, so a short human interjection is never mistaken for an echo."""
    tokens = _echo_tokens(text)
    if len(tokens) < _ECHO_MIN_TOKENS:
        return False
    incoming = set(tokens)
    for said_ts, said in spoken:
        if now - said_ts > _ECHO_WINDOW_S:
            continue
        said_set = set(_echo_tokens(said))
        if said_set and len(incoming & said_set) / len(incoming) >= _ECHO_CONTAINMENT:
            return True
    return False


def is_addressed(speaker: str, text: str, *, is_chat: bool = False) -> str | None:
    """Return the ask text to wake on (the line verbatim), or ``None`` if not addressed.

    Proxy's own lines never wake it. A voice line naming ``proxy`` or a chat ``@proxy`` is
    an address; anything else is idle. The ask is the line as spoken — the surrounding context
    is already resident in the warm session (the whole meeting accumulated in its cache), so
    the wake only needs to carry the delta since the last wake, not re-fetch history.
    """
    if speaker == PROXY_SPEAKER:
        return None
    addr = _CHAT_ADDR if is_chat else _VOICE_ADDR
    return text if text and addr.search(text) else None


@dataclass(slots=True)
class MeetingSession:
    """The reactive loop for one live meeting: transcript-in → wake gate → run → respond.

    Drive it by calling :meth:`on_line` for every final transcript line (the webhook
    drain does exactly this). ``workroom`` is the per-meeting E2B sandbox (native Claude
    with the repo + the transcript file); ``connection`` is the host-side meeting driver
    the agent's result is carried out over. ``drain`` awaits in-flight wakes (meeting end).
    """

    workroom: Any                          # in_meeting.workroom.Workroom
    connection: Any                        # in_meeting.meeting_connection.MeetingConnection
    proxy_speaker: str = PROXY_SPEAKER

    _lines: list[tuple[float, str, str]] = field(default_factory=list)  # (ts, speaker, text)
    #: The index into ``_lines`` up to which the transcript has ALREADY been delivered into the warm
    #: session's cached conversation (via a prior wake's inlined delta). Each wake inlines ONLY
    #: ``_lines[_delivered_upto:]`` — the new lines since the last wake — so the whole transcript
    #: accumulates in the resident cache turn-over-turn and the agent just *knows* the room; only the
    #: delta + the ask are fresh per wake (SPEC §3). Advanced the instant a wake is dispatched.
    _delivered_upto: int = 0
    _inflight: set[asyncio.Task[None]] = field(default_factory=set)
    #: The results of each wake, in order (host-observed record for tests + audit).
    results: list[Any] = field(default_factory=list)
    #: ASK → ANSWER → CONTINUE latch: ``(question_text, ts)`` when Proxy's last turn ended asking the
    #: room a question and delivered no completed artifact — else ``None``. While set, the next
    #: substantive human line is treated as the answer and wakes Proxy to CONTINUE the same task even
    #: without a name mention. Set in :meth:`_handle`, consumed/cleared in :meth:`on_line`.
    _pending_question: tuple[str, float] | None = None
    #: FOLLOW-UP WINDOW (F1): the meeting-clock ts UNTIL which a substantive human line is routed to
    #: the model's judgment without a name mention. Opened by a delivered turn AND by a barge-in;
    #: refreshed by an in-window delivery; expires on timeout or a new explicit address. ``0.0`` =
    #: closed. Set via :meth:`_open_follow_up_window`, read/expired in :meth:`on_line`.
    _follow_up_until: float = 0.0

    async def on_line(
        self, speaker: str, text: str, *, ts: float = 0.0, is_chat: bool = False
    ) -> None:
        """Ingest ONE final transcript/chat line: append to the notes, then run the wake gate.

        ``is_chat`` selects the chat wake rule (``@proxy``) vs the voice rule (a spoken
        ``proxy``). Never blocks: the transcript sync + a wake both run so the room keeps
        flowing while Proxy works. A non-addressed line only updates the notes (the up-to-date
        transcript a later wake — or a mid-task follow-up — reads). Never raises."""
        speaker, text = str(speaker or ""), str(text or "")
        # Self-echo suppression (headphones-optional): a voice line that reproduces something Proxy
        # just SAID is Proxy's own voice echoing back — mislabeled as a human when the room has no
        # headphones. Relabel it to Proxy so it is recorded as Proxy's contribution and NEVER
        # re-wakes Proxy (the speaker==proxy_speaker gate below then filters it). Chat is text and
        # cannot echo acoustically, so only voice lines are checked (a human may legitimately quote
        # Proxy in chat). Label-independent, so it works with or without headphones.
        if (
            not is_chat
            and speaker != self.proxy_speaker
            and _is_self_echo(text, getattr(self.connection, "spoken", ()), time.time())
        ):
            speaker = self.proxy_speaker
        # Barge-in (Law 3): a HUMAN talking over Proxy stops its speech at once. The self-echo relabel
        # above already reattributed Proxy's own bleed-back to Proxy, so a line that is still a human's
        # AND is a real interjection (not a sub-onset STT blip) AND arrives while Proxy is mid-utterance
        # is a genuine barge-in. Chat is typed, not spoken over, so it never barges. Never-raise: the
        # cut is the honest stop, but a fault here must not crash the drain (§3.8).
        if not is_chat and speaker != self.proxy_speaker and _is_barge_in(text):
            try:
                if getattr(getattr(self.connection, "speak", None), "speaking", False):
                    await self.connection.barge_in()
                    # FOLLOW-UP WINDOW (F1): being interrupted IS mid-exchange. Open the window on the
                    # cut so the interrupting line — and the next lines within the window — reach the
                    # model's judgment without a name mention (the founder's own line, "wait, hold on…",
                    # is almost always still to Proxy). The [SILENT] verdict stays the over-fire guard.
                    self._open_follow_up_window(ts)
            except Exception:  # noqa: BLE001 — a barge-in fault never crashes the meeting
                logger.exception("barge-in cut failed (meeting continues)")
        self._lines.append((ts, speaker, text))
        # Continuous feed (§3): materialize the latest transcript into the workroom so a
        # woken turn — and any mid-task follow-up — sees the up-to-date room. Never-raise.
        try:
            await self.workroom.feed_transcript(self._render_transcript())
        except Exception:  # noqa: BLE001 — transcript sync never crashes the meeting
            logger.exception("meeting transcript sync failed (meeting continues)")
        ask = is_addressed(speaker, text, is_chat=is_chat)
        if ask is not None:
            # An explicit address always wins: it starts a fresh turn, so any pending question is
            # superseded (the room moved on / re-addressed Proxy directly). Clear the latch and run
            # the normal wake — the continuation branch below is only for UN-addressed replies. A new
            # explicit address also EXPIRES the follow-up window (the fresh turn owns the exchange now).
            self._pending_question = None
            self._follow_up_until = 0.0
            self._spawn(self._handle(ask, self._take_delta()))
            return
        # ASK → ANSWER → CONTINUE: no name was used, but if Proxy is WAITING on the room (its last
        # turn ended asking a question and delivered nothing), the next substantive HUMAN line is the
        # answer. Wake Proxy to CONTINUE the same task with that context — no name needed. Guard hard
        # so the normal name-gate is untouched for ordinary cross-talk: only a live (un-expired)
        # pending question, from a real human, with a substantive line, is a continuation.
        cont = self._take_continuation(speaker, text, is_chat=is_chat, now=ts)
        if cont is not None:
            self._spawn(self._handle(cont, self._take_delta()))
            return
        # FOLLOW-UP WINDOW (F1): no name, no pending question — but if we're inside the short window
        # opened by Proxy's last delivered turn (or a barge-in), the exchange is still live and this
        # substantive human line is almost certainly still to Proxy. ROUTE it to the model's judgment
        # with the NORMAL prompt (the line verbatim) — the model proves it stays [SILENT] when the
        # line isn't for it, so this only routes judgment, it never forces a response. Expires
        # silently on timeout; a delivered in-window turn re-opens it (handled in _handle).
        if self._in_follow_up_window(speaker, text, is_chat=is_chat, now=ts):
            self._spawn(self._handle(text, self._take_delta()))

    async def catch_up(
        self, lines: list[tuple[str, str, float, bool]]
    ) -> None:
        """Flush PRE-WIRE buffered lines as ONE catch-up — at most ONE wake for the batch.

        While the workroom assembled, the founder may have addressed Proxy several times
        (the live finding: 3-4 buffered attempts flushed as 3-4 independent wakes → a
        barrage of consecutive answers). A human who joins late hears everything said and
        answers ONCE — so: ingest every line into the notes, then fire a single wake for
        the LAST addressed line; the delta carries the whole batch, so the model sees all
        the attempts and responds once, with full context. Never raises (drain-path safe).
        """
        last_ask: str | None = None
        last_chat = False
        for speaker, text, ts, is_chat in lines:
            speaker, text = str(speaker or ""), str(text or "")
            self._lines.append((ts, speaker, text))
            ask = is_addressed(speaker, text, is_chat=is_chat)
            if ask is not None:
                last_ask = ask
                last_chat = is_chat
        try:
            await self.workroom.feed_transcript(self._render_transcript())
        except Exception:  # noqa: BLE001 — transcript sync never crashes the meeting
            logger.exception("catch-up transcript sync failed (meeting continues)")
        if last_ask is not None:
            _ = last_chat  # the ask text already carries the chat-strip; one wake either way
            self._pending_question = None
            self._spawn(self._handle(last_ask, self._take_delta()))

    async def on_partial(self, speaker: str, text: str, *, ts: float = 0.0) -> None:
        """A NON-FINAL (partial) transcript line — used ONLY for barge-in onset (Law 3, BUG 3).

        Recall streams ``transcript.partial_data`` as a human speaks, ~0.5-1.5s before the FINAL
        ``transcript.data`` line. On the live path a human talking over Proxy was only caught by the
        final line ~8s later (handled as a fresh wake, not a cut). Feeding partials here lets a real
        human onset CUT the active speech at once. A partial is NOT fed as transcript, NOT logged, and
        NEVER wakes/provisions — it is noisy and non-final; its ONLY job is the barge-in reflex.

        Same debounce as :meth:`on_line` (physics, not a rule): Proxy's own echo is relabeled and
        never barges; a sub-onset blip (< ``_BARGE_MIN_TOKENS``) does NOT cut; only a real human
        interjection arriving while Proxy is mid-utterance cuts. Never raises into the drain."""
        speaker, text = str(speaker or ""), str(text or "")
        if speaker == self.proxy_speaker:
            return
        # Proxy's own voice bleeding back (no headphones) can arrive as a partial too — relabel it so
        # it never self-barges (same label-independent echo test the final path uses).
        if _is_self_echo(text, getattr(self.connection, "spoken", ()), time.time()):
            return
        if not _is_barge_in(text):
            return
        try:
            if getattr(getattr(self.connection, "speak", None), "speaking", False):
                await self.connection.barge_in()
        except Exception:  # noqa: BLE001 — a barge-in fault never crashes the meeting
            logger.exception("partial barge-in cut failed (meeting continues)")

    def _spawn(self, coro: Any) -> None:
        """Fire a wake as a background task tracked in ``_inflight`` (the room keeps flowing while it
        runs; ``drain`` awaits it). Factored out so the address path and the continuation path enqueue
        identically."""
        task = asyncio.create_task(coro)
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    def _take_continuation(
        self, speaker: str, text: str, *, is_chat: bool, now: float
    ) -> str | None:
        """If a pending question is live and ``text`` is a real human answer to it, CONSUME the latch
        and return the continuation ask (the wake prompt for Proxy to resume the task with the prior
        Q + this A). Else return ``None`` and leave the latch as-is. Clears an EXPIRED latch so a much
        later unrelated line is never hijacked (the name-gate is back in sole control)."""
        pending = self._pending_question
        if pending is None:
            return None
        question, asked_at = pending
        if now - asked_at > _CONTINUE_TIMEOUT_S:
            self._pending_question = None  # the moment passed — no stale continuation
            return None
        # Proxy's own line (or its echo, already relabeled above) is never the answer to its own
        # question; and a sub-onset blip / whitespace is not a real reply — leave the latch standing.
        if speaker == self.proxy_speaker or len(_echo_tokens(text)) < _CONTINUE_MIN_TOKENS:
            return None
        self._pending_question = None  # consumed — this line is the answer; back to the name-gate
        channel = "chat" if is_chat else "voice"
        return (
            f"Earlier you asked the room: {question}\n"
            f"They just answered ({channel}) — {speaker}: {text}\n"
            "Continue the task now with this answer, and deliver the result in this turn."
        )

    def _open_follow_up_window(self, now: float | None = None) -> None:
        """Open (or refresh) the SHORT follow-up window (F1) from ``now`` (the meeting clock).

        Called when a turn DELIVERED and when a barge-in fired — both mean the exchange is live, so
        the human's next lines should reach the model's judgment name-free. ``now`` defaults to the
        last ingested line's ts (the same clock the window's expiry is measured against). Refreshing
        (each in-window delivery re-opens it) keeps a back-and-forth going without a name each turn."""
        base = now if now is not None else (self._lines[-1][0] if self._lines else 0.0)
        self._follow_up_until = base + _FOLLOW_UP_WINDOW_S

    def _in_follow_up_window(
        self, speaker: str, text: str, *, is_chat: bool, now: float
    ) -> bool:
        """True iff a follow-up window is live and ``text`` is a real human line to route (F1).

        Guarded hard so ordinary cross-talk after Proxy stops is NOT routed en masse: the window must
        be OPEN (un-expired at ``now``), the line must be from a real human (never Proxy's own / its
        relabeled echo), and it must carry at least ``_FOLLOW_UP_MIN_TOKENS`` (a real line, not a
        blip). Expires the window on timeout so a much-later line is never routed. Chat is included:
        a founder typing a follow-up mid-exchange is as valid as speaking one."""
        if self._follow_up_until <= 0.0:
            return False
        if now > self._follow_up_until:
            self._follow_up_until = 0.0  # the window passed — name-gate back in sole control
            return False
        if speaker == self.proxy_speaker or len(_echo_tokens(text)) < _FOLLOW_UP_MIN_TOKENS:
            return False
        return True

    def _note_pending_question(self, delivered: str) -> None:
        """Latch a pending question iff Proxy's last room-facing delivery ``delivered`` was a question
        (ended on '?') — so the next substantive human reply continues the task without a name mention
        (:meth:`_take_continuation`). A completed statement latches nothing. Timestamped with the last
        line's ts (the meeting clock the continuation expiry is measured against)."""
        text = (delivered or "").strip()
        if text and _looks_like_question(text):
            now = self._lines[-1][0] if self._lines else 0.0
            self._pending_question = (text, now)
        else:
            self._pending_question = None

    async def _handle(self, ask: str, delta: str) -> None:
        """One wake: run the reactive turn in the workroom, respond through the connection.

        ``delta`` is the new transcript since the last wake (captured synchronously at dispatch by
        :meth:`_take_delta`, so overlapping concurrent wakes never double- or drop-count lines). The
        warm session inlines it into the turn, where it enters the cached conversation — so the whole
        meeting accumulates resident and the agent recalls earlier lines with zero file reads (SPEC §3).

        The agent chooses WHETHER and HOW to reach the room — always its own ``to_meeting`` calls,
        never our prose. There are two delivery paths and we honor whichever one carried the turn,
        in priority order (Law 4 — the mediums are the agent's, not ours):

        * RELAY mode — the in-sandbox MCP POSTed each ``to_meeting`` call live to THIS connection and
          recorded nothing locally, so ``result.sent`` is empty: the room already heard the agent,
          nothing to replay (never double-send).
        * FILE mode (no relay) — the agent recorded ≥1 intent locally (``result.sent``): REPLAY each
          over the connection with the agent's OWN chosen medium. We key off ``result.sent``, NOT the
          shared ``connection.sent`` counter, so overlapping concurrent wakes never drop each other's
          delivery.
        * else ``result.error`` — the turn crashed/incompleted with nothing delivered. Speak ONE
          honest degrade so a task that needed a response is never met with silence.
        * else (a clean turn, zero intents) — the agent chose not to respond (e.g. it was not really
          addressed / cross-talk). STAY SILENT. We never invent a response from ``result.text``.

        A failed wake is an honest no-op the meeting survives (§3.8), never a raise."""
        try:
            # A new wake's delivery begins: lower any barge-in cut latch left up by the PREVIOUS
            # (interrupted) turn so THIS turn's spoken output flows. The latch only ever silences the
            # one turn it interrupted — relay-mode streams during run_ask, so clear before we run.
            begin = getattr(self.connection, "begin_turn", None)
            if callable(begin):
                begin()
            # Snapshot the connection's delivery count BEFORE the turn so we can tell whether RELAY
            # mode reached the room (its live to_meeting POSTs land on this connection during run_ask,
            # growing ``sent`` even though ``result.sent`` stays empty). This is the delivered-signal
            # the follow-up window (F1) re-opens on — read only for THIS turn (a concurrent wake grows
            # it too, but here we only need "did SOMETHING reach the room", not whose).
            sent_before = len(getattr(self.connection, "sent", ()) or ())
            result = await self.workroom.run_ask(ask, delta=delta)
            self.results.append(result)
            # How the turn was carried is encoded in result.sent, NOT in the shared connection.sent
            # counter: a CONCURRENT wake's replay grows connection.sent too, so keying off it makes a
            # second overlapping wake wrongly think it already delivered and DROP its own response — a
            # real bug when two people address Proxy close together. In RELAY mode the in-sandbox MCP
            # POSTs each call live to the connection and records nothing locally, so result.sent is
            # empty (the room already heard it — nothing to replay, never double-send). In FILE mode
            # (no relay) it records each intent locally, so result.sent carries them and we replay.
            recorded = list(getattr(result, "sent", None) or [])
            if recorded:
                # The no-relay/file path: replay the agent's OWN channel choices verbatim.
                for intent in recorded:
                    await self.connection.to_meeting(
                        str(intent.get("content", "") or ""),
                        medium=str(intent.get("medium", "say") or "say"),
                        to=str(intent.get("to", "") or "") or None,
                    )
                # ASK → ANSWER → CONTINUE: if the LAST thing Proxy delivered to the room was a
                # question (it ended its turn asking, e.g. a clarify or a named blocker), latch it so
                # a plain human reply — which won't name Proxy — is treated as the answer and Proxy
                # continues the same task. The agent's OWN delivered words are the signal (never our
                # prose). A completed turn that ends on a statement latches nothing (name-gate as-is).
                self._note_pending_question(str(recorded[-1].get("content", "") or ""))
                # FOLLOW-UP WINDOW (F1): a file-mode turn that replayed ≥1 intent DELIVERED — open
                # (refresh) the window so the founder's next line continues the exchange name-free.
                self._open_follow_up_window()
                return
            if getattr(result, "error", None):
                # The turn errored with NO recorded ``to_meeting`` intent (the ``recorded`` branch
                # returned above when there was one). We speak ONE bare, honest apology — NEVER the
                # agent's last assistant prose (``result.text``): that is internal scratchpad the
                # agent did NOT choose to say to the room (e.g. "on it…"), so surfacing it would
                # put words in Proxy's mouth it never picked (soft Law 2). A recorded intent is the
                # only thing the agent chose for the room, and that path is already handled above.
                await self.connection.to_meeting(
                    "Sorry — I hit a problem finishing that one and couldn't wrap it up cleanly.",
                    medium="say",
                )
                # The honest apology reached the room — a delivery. Open (refresh) the window (F1).
                self._open_follow_up_window()
                return
            # A clean turn with zero recorded intents. Two sub-cases:
            #  * RELAY mode — the agent DID reach the room live (its ``to_meeting``/spoken sentences
            #    were POSTed to the host, so nothing is recorded locally). ``result.text`` is the
            #    agent's own final summary of what it said; if that ends on a question, latch it so a
            #    plain reply continues the task. We use ``text`` ONLY as the question SIGNAL here — we
            #    never SPEAK it (that stays the agent's own delivered words).
            #  * true cross-talk — the agent chose silence. ``text`` is internal scratchpad, but the
            #    question detector only latches on a trailing '?'; a silent turn's note rarely ends
            #    that way, and even a false latch is bounded by the min-token + expiry guards.
            self._note_pending_question(str(getattr(result, "text", "") or ""))
            # FOLLOW-UP WINDOW (F1): RELAY mode delivered iff its live to_meeting POSTs grew the
            # connection's ``sent`` during this turn. Only THEN open the window — a true silent turn
            # (cross-talk, zero delivery) must NOT open it (else every incidental "proxy" mention
            # would route the next lines). This is the delivered-signal for the no-local-record path.
            if len(getattr(self.connection, "sent", ()) or ()) > sent_before:
                self._open_follow_up_window()
            # else: a clean turn with zero intents — the agent chose silence (cross-talk). Stay quiet.
        except Exception:  # noqa: BLE001 — a wake failure never crashes the meeting (§3.8)
            logger.exception("meeting wake failed (meeting continues)")

    def _render_transcript(self) -> str:
        # Window to the most recent lines (FW-2): the full rewrite is O(N) bytes per line over the
        # E2B seam → O(N^2) over a long meeting. A rolling window bounds each write to O(1) and is
        # what a person reads anyway — the woken turn always sees the recent room (the addressing
        # line is fresh), and the map + repo cover anything older. Header notes the elision.
        recent = self._lines[-_TRANSCRIPT_WINDOW:]
        elided = len(self._lines) - len(recent)
        header = "# Meeting transcript"
        if elided > 0:
            header += f"\n(… {elided} earlier line(s) elided …)"
        body = "\n".join(
            f"[{ts:.0f}] {speaker}: {text}" for (ts, speaker, text) in recent
        )
        return f"{header}\n{body}\n"

    def _take_delta(self) -> str:
        """The transcript DELTA since the last wake — the lines the resident cache hasn't seen yet —
        and advance the delivered pointer past them. Called synchronously at dispatch (before the
        background wake is spawned) so two wakes that overlap can never double-count or drop a line.

        Inlined into the wake, this delta enters the warm session's cached conversation, so the whole
        meeting accumulates resident turn-over-turn (SPEC §3): the agent already *knows* everything
        said before this wake (zero file reads), and only the delta + the ask are fresh this turn. On
        the first wake the delta is the whole meeting-so-far; on later wakes it is just what was said
        since. Empty when nothing new has arrived (a burst of wakes on one line)."""
        new = self._lines[self._delivered_upto:]
        self._delivered_upto = len(self._lines)
        return "\n".join(f"[{speaker}] {text}" for (_ts, speaker, text) in new)

    async def drain(self) -> None:
        """Await every in-flight wake (meeting end / tests). Best-effort, never raises."""
        while self._inflight:
            await asyncio.gather(*tuple(self._inflight), return_exceptions=True)


__all__ = ["MeetingSession", "PROXY_SPEAKER", "is_addressed"]  # helpers are module-private (tested via import)
