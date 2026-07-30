"""The two-directional chat surface (§3.4, AC-CHAT-01..16).

Chat is a first-class socket, not a lesser sibling of voice. Inbound, every platform
chat line arrives ONLY through the Recall transport seam (``chat_events``) and is
re-emitted upstream as a ``chat(message, sender, dm?)`` signal; a line addressed to
Proxy is forwarded to the injected ask_sink (Doc 04) as an ask **identical in shape**
to a spoken ask, differing solely in its recorded ``socket`` field — chat vs voice
(AC-CHAT-01..05/16). A line addressed to nobody is reported as a signal but never
enters the ask path (AC-CHAT-04).

Outbound, this layer is pure plumbing over three per-meeting behaviors (AC-CHAT-15):

* **broadcast** — detail/links/receipts/notices and every spoken line's verbatim text
  copy go to the public channel via ``post_chat`` exactly once, content preserved
  (AC-CHAT-06/07). :class:`~transport.speak.SpeakOrchestrator` wires its ``post_copy``
  seam straight to :meth:`ChatChannel.broadcast`.
* **DM-where-supported** — a private line goes to EXACTLY one recipient via ``send_dm``
  and never leaks to the broadcast channel (AC-CHAT-08/09).
* **channel-report** — ``dm_available`` is read from the REAL platform capability
  (``transport.channel_report``), never a hard-coded constant (AC-CHAT-12/13).

On a broadcast-only platform (``dm_available == False``) a DM request MECHANICALLY
degrades — it is handed to the injected upstream decision hook (Doc 04/06), which owns
the broadcast-vs-hold choice. This layer only reports and delegates; it encodes no
situational judgment and drops nothing (AC-CHAT-10/11).

``chat`` and ``channel-report`` are internal 02->03/04 events, never client
``ProxyMessage`` types — ``assert_registry_closed()`` stays disjoint from this surface
(AC-CHAT-14); this module touches no contracts registry.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from contracts.channels import ChannelReport

from libs.contracts import DraftCard, NoteLine

from .carrier import SignalCarrier
from .seams import TransportProvider
from .signals import ChatMessage

if TYPE_CHECKING:
    from scribe.schema import NoteDelta

    from libs.contracts import Envelope

    from .failure import Gap

#: The two ask sockets. An ask carries which socket birthed it; NOTHING else about the
#: ask shape differs between them (AC-CHAT-03).
Socket = Literal["chat", "voice"]
CHAT_SOCKET: Socket = "chat"
VOICE_SOCKET: Socket = "voice"

#: The literal address mention this layer owns unconditionally. The broader "any addressed
#: message" recognizer is Doc 04's (§3.6) and is injected — see :class:`ChatChannel`.
_PROXY_MENTION = "@proxy"


@dataclass(frozen=True)
class Ask:
    """A first-class ask handed to the ask_sink — the SAME shape for chat and voice.

    ``socket`` is the only field that records the origin; a chat ask is never tagged as
    a lesser/secondary class (AC-CHAT-02/03). ``from_chat`` / ``from_voice`` build the
    two parity-equal forms so a shape comparison reduces to ``.socket`` alone.
    """

    content: str
    sender: str
    socket: Socket

    @classmethod
    def from_chat(cls, content: str, sender: str) -> Ask:
        """Build the chat-socket ask (this layer's product)."""
        return cls(content=content, sender=sender, socket=CHAT_SOCKET)

    @classmethod
    def from_voice(cls, content: str, sender: str) -> Ask:
        """Build the voice-socket ask — the spoken-ask parity counterpart."""
        return cls(content=content, sender=sender, socket=VOICE_SOCKET)


@dataclass(frozen=True)
class InboundOutcome:
    """Honest record of one inbound line: the signal always fires; the ask is optional.

    ``forwarded`` is the ask handed to the sink, or ``None`` when the line was not
    addressed to Proxy (the AC-CHAT-04 reject frontier).
    """

    signalled: bool
    forwarded: Ask | None


@dataclass(frozen=True)
class DegradeRequest:
    """A DM that could not go private, handed upstream for the broadcast-or-hold choice.

    This layer constructs it and delegates; it does NOT decide broadcast vs hold
    (AC-CHAT-10/11).
    """

    participant_id: str
    message: str


@dataclass(frozen=True)
class DmResult:
    """Outcome of a private-send request — private-delivered XOR degraded, never dropped.

    ``recipient`` is the single private recipient when delivered (cardinality 1); when
    the platform is broadcast-only ``recipient`` is ``None`` and ``degraded`` is True,
    meaning the request was handed to the upstream decision hook (AC-CHAT-08/10).
    """

    recipient: str | None
    degraded: bool


def has_proxy_token(message: str) -> bool:
    """True iff the message carries the literal ``@proxy`` address token (case-insensitive).

    This is ONE accept path this layer owns outright; it is deliberately NOT the only
    one — keying strictly on this token would drop an un-prefixed addressed line and
    fail AC-CHAT-16.
    """
    return _PROXY_MENTION in message.casefold()


class ChatChannel:
    """The per-meeting two-directional chat surface for one Recall bot.

    All external round-trips go through the injected :class:`TransportProvider` seam
    (itself behind ``call_external``); no raw provider client and no network live here.
    ``ask_sink`` is Doc 04's ask entry point; ``degrade_hook`` is Doc 04/06's
    broadcast-or-hold decision hook; ``recognizer`` is Doc 04's addressing recognizer
    for non-token forms (§3.6) — defaulting to token-only when not supplied.
    """

    def __init__(
        self,
        transport: TransportProvider,
        carrier: SignalCarrier,
        bot_id: str,
        *,
        ask_sink: Callable[[Ask], Any],
        degrade_hook: Callable[[DegradeRequest], Any],
        recognizer: Callable[[str], bool] | None = None,
    ) -> None:
        self._transport = transport
        self._carrier = carrier
        self._bot_id = bot_id
        self._ask_sink = ask_sink
        self._degrade_hook = degrade_hook
        # Default recognizer: token-only. Doc 04 injects the real "any addressed form"
        # recognizer; the OR below keeps the @proxy token always-accepted regardless.
        self._recognizer = recognizer if recognizer is not None else _never
        self._closed = False

    # ── inbound: platform chat -> chat() signal (+ addressed -> ask) ──────────────
    def is_addressed(self, message: str) -> bool:
        """Whether a line is addressed to Proxy: the ``@proxy`` token OR Doc 04's recognizer.

        The accept set is {``@proxy``-prefixed, any other addressed form}; everything
        else is the reject frontier (AC-CHAT-04/16). The *recognition* of a non-token
        addressed form is Doc 04's — this layer owns only the *forwarding* contract.
        """
        return has_proxy_token(message) or self._recognizer(message)

    async def inbound(self, msg: ChatMessage) -> InboundOutcome:
        """Alias for dispatch_inbound (AC-CHAT-01)."""
        return await self.dispatch_inbound(msg)

    async def dispatch_inbound(self, msg: ChatMessage) -> InboundOutcome:
        """Report one inbound line as a chat() signal; forward it as an ask iff addressed."""
        # Every inbound line surfaces upstream as a chat(message, sender, dm?) signal
        # (AC-CHAT-01/05). The signal fires whether or not the line is an ask.
        await self._carrier.emit(msg)

        if not self.is_addressed(msg.message):
            # Not addressed to Proxy: reported, but NOT an ask (AC-CHAT-04).
            return InboundOutcome(signalled=True, forwarded=None)

        # Addressed: forward as a first-class ask, identical in shape to a spoken ask,
        # differing only in socket (AC-CHAT-02/03/16).
        ask = Ask.from_chat(content=msg.message, sender=msg.sender)
        result = self._ask_sink(ask)
        if asyncio.isfuture(result) or asyncio.iscoroutine(result):
            await result
        return InboundOutcome(signalled=True, forwarded=ask)

    async def pump_inbound(self) -> None:
        """Consume the Recall chat stream — the ONLY inbound chat path (AC-CHAT-01)."""
        async for msg in self._transport.chat_events(self._bot_id):
            await self.dispatch_inbound(msg)

    # ── outbound: broadcast / DM / channel-report ─────────────────────────────────
    async def broadcast(self, text: str, *, pinned: bool = False) -> None:
        """Post content to the public channel exactly once, verbatim (AC-CHAT-06/07).

        This is the seam :class:`~transport.speak.SpeakOrchestrator` wires its spoken-line
        text copy into, and the sink for detail/links/receipts/notices.
        """
        await self._transport.post_chat(self._bot_id, text, pinned=pinned)

    async def send_dm(self, message: str = "", participant_id: str = "") -> DmResult:
        """Deliver a private DM; accepts positional or keyword args (AC-CHAT-08/09)."""
        return await self.send_direct(participant_id, message)

    async def send_direct(self, participant_id: str, message: str) -> DmResult:
        """Deliver a private line to EXACTLY one recipient, or mechanically degrade.

        The branch is purely mechanical on the REAL platform capability: DM-supported
        -> one ``send_dm`` (never broadcast, AC-CHAT-08/09); broadcast-only -> hand to
        the upstream decision hook, never dropped (AC-CHAT-10/11).
        """
        report = self.channel_report()
        if report.dm_available:
            # One private send, one recipient — the DM body never touches post_chat.
            await self._transport.send_dm(self._bot_id, message, participant_id)
            return DmResult(recipient=participant_id, degraded=False)
        # Broadcast-only: report + delegate. No broadcast-vs-hold judgment here.
        result = self._degrade_hook(DegradeRequest(participant_id=participant_id, message=message))
        if asyncio.isfuture(result) or asyncio.iscoroutine(result):
            await result
        return DmResult(recipient=None, degraded=True)

    def channel_report(self) -> ChannelReport:
        """The REAL per-meeting capability report (``dm_available``), read from transport.

        The value derives from the platform via the seam, never a hard-coded constant —
        both the true and false branches are reachable (AC-CHAT-12/13).
        """
        return self._transport.channel_report(self._bot_id)

    async def emit_channel_report(self) -> ChannelReport:
        """Compute and emit the channel-report signal upstream (internal, not registered)."""
        report = self.channel_report()
        await self._carrier.emit(report)
        return report


def _never(_message: str) -> bool:
    """The default addressing recognizer: token-only (no non-token form recognized)."""
    return False


# ── the DETERMINISTIC chat formatters (§2.4 #3/#4/#8/#14, §2.3) ────────────────────
# One template set of deterministic formatters, each riding existing exhaust — NOT a
# wake, NEVER model-generated (so free, race-free, cannot hallucinate a decision,
# CANONICAL §12.12). Each emits a **registered** ``ProxyMessage`` render frame
# (``NoteLine`` / ``DraftCard`` from ``libs.contracts``); NOTHING here calls a provider
# or ``stream_deltas``. Every string is in the §2.1 copy voice (no internal names, no
# filler, no exclamation marks) — the naming lint + copy guide enforce that.

#: The one home the draft-card link points at — the authenticated per-meeting home
#: (§2.8), NEVER a raw GCS URI. A relative path so the surface app owns the origin.
_MEETING_HOME = "/m/{meeting_id}"


def _stances_clause(leans: dict[str, str]) -> str:
    """Render a decision's ``leans`` as the deterministic '(Priya, Sam agreed)' clause.

    Only the *for* stances are surfaced ("agreed") — a decision line marks who backed
    it, not who abstained. Deterministic: names are emitted in ``leans`` insertion
    order (the scribe schema preserves it), never a set/hash order. Empty ⇒ no clause.
    """
    agreed = [name for name, stance in leans.items() if stance == "for"]
    if not agreed:
        return ""
    return f" ({', '.join(agreed)} agreed)"


def _decision_line(entry: Any) -> str:
    """The §2.4 #3 decision note-line — '— noted: decision — ship Friday (Priya, Sam agreed)'."""
    return f"— noted: decision — {entry.text}{_stances_clause(dict(entry.leans))}"


def _action_line(entry: Any) -> str:
    """The §2.4 #3 action note-line — '— action: Sam → fix the retry test (by Fri)'.

    The owner prefix and the due suffix are each present only when the scribe captured
    them; a bare action still renders a clean, deterministic line.
    """
    owner = f"{entry.owner} → " if entry.owner else ""
    due = f" ({entry.due})" if entry.due else ""
    return f"— action: {owner}{entry.text}{due}"


def format_note_deltas(
    delta: NoteDelta,
    *,
    committed: bool,
    notes_enabled: bool = True,
) -> Iterator[NoteLine]:
    """Emit the §2.4 #3/#4 chat lines for one COMMITTED note-delta — deterministic.

    Keyed on a **committed** ``NoteDelta`` (CANONICAL §12.12): an uncommitted delta
    (``committed=False``) emits NOTHING — a line never rides uncommitted state. When
    the session disabled note-posting (§2.4 #9, ``notes_enabled=False``) the decision/
    action lines are suppressed. Never spoken, never a wake, never model-generated.

    * an ``AddOp`` carrying a ``Decision`` → the decision note-line,
    * an ``AddOp`` carrying an ``ActionItem`` → the action note-line,
    * a ``PatchOp`` → the correction ack ('— corrected: ship Friday', §2.4 #4).

    Every other op (a Claim/Context add, a ``CloseOp``) is outside §2.4's scoped chat
    set and emits nothing. The correction ack honors the SAME session disable.
    """
    if not committed or not notes_enabled:
        return
    for op in delta.ops:
        text: str | None = None
        if op.op == "add":
            entry = op.entry
            if entry.kind == "decision":
                text = _decision_line(entry)
            elif entry.kind == "action":
                text = _action_line(entry)
        elif op.op == "patch":
            new_text = op.changes.get("text")
            if isinstance(new_text, str) and new_text:
                text = f"— corrected: {new_text}"
        if text is not None:
            yield NoteLine(text=text)


def _meeting_link(meeting_id: UUID) -> str:
    """The authenticated ``/m/{meeting_id}`` home link — NEVER a raw GCS URI (§2.4 #8/§2.8)."""
    return _MEETING_HOME.format(meeting_id=meeting_id)


def format_draft_card(envelope: Envelope, *, meeting_id: UUID) -> DraftCard:
    """Render the §2.4 #8 draft card — what → change → the ``/m/`` link → 'your click'.

    Deterministic, keyed on Doc 05's staged-draft ``Envelope``. Carries the persisted
    ``Envelope.draft_id`` (CANONICAL §11.5) so the card render and the ``/m/`` accept
    route read the SAME typed field. The link ALWAYS points at the authenticated
    ``/m/{meeting_id}`` home (§2.8), **never** a raw GCS URI — even when the envelope's
    artifact carries a ``gs://`` ref. A draft_id-less envelope is a wiring error (a card
    that points a click at nothing), so it raises rather than render.
    """
    if envelope.draft_id is None:
        raise ValueError("a draft card requires a persisted Envelope.draft_id (§2.4 #8, CANONICAL §11.5)")
    what = envelope.headline
    change = envelope.detail or envelope.headline
    link = _meeting_link(meeting_id)
    # what-it-is → one-line change → ─ divider → the /m/ link → 'approve = your click'.
    summary = f"{what}\n{change}\n─\n{link}\napprove = your click"
    return DraftCard(draft_id=envelope.draft_id, summary=summary)


def format_reconciliation_card(
    *,
    left_value: str,
    left_source: str,
    left_as_of: str | None,
    right_value: str,
    right_source: str,
    right_as_of: str | None,
) -> NoteLine:
    """Render the §2.4 #14 reconciliation card — two as-of-sourced values side by side.

    A render VARIANT of the card format: two contested figures, each with its source
    and as-of date — '— deck: $2.4M · Q3 filing: $2.19M, as of Q3 close.' It surfaces a
    disagreement as a sourced comparison the room resolves for itself, NOT a verdict
    Proxy delivers at someone (no 'you're wrong'). Deterministic: field order is fixed.
    """
    left = f"{left_source}: {left_value}"
    if left_as_of:
        left = f"{left} ({left_as_of})"
    right = f"{right_source}: {right_value}"
    if right_as_of:
        right = f"{right}, {right_as_of}"
    return NoteLine(text=f"— {left} · {right}.")


def format_reconnect_summary(gap: Gap) -> NoteLine:
    """Render the §2.3 reconnect summary — the honest disconnect gap, as a NoteLine.

    Rides the existing ``failure.Gap`` wording (ONE source for the gap line, no
    re-wording) and surfaces it as a registered render frame: the announced interval
    equals the real disconnect window, the record is never presented as continuous
    across the drop. Deterministic — it is the same string ``Gap.line()`` produces.
    """
    return NoteLine(text=gap.line())
