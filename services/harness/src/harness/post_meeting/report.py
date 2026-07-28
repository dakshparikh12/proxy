"""B7 — report. Say what happened, on a channel that exists, without rounding up.

Criteria: **AC-PME-13** (``needs_clarification`` is a question, never a failure),
**AC-PME-14** (``partial``/``failed`` are reported plainly and confidence never rounds up)
and **AC-PME-16, AC-PME-16-NEG** (channel discipline, recipients, and the four-event
cadence).

Doc 07 §3.6. Three things this module refuses to do:

**It never invents a channel.** Channel selection reuses Doc 02's ``channel-report``
exactly as in-meeting delivery does. The bot has left, so platform chat is gone; what
remains is the draft card (Doc 08), which is BUILT. Slack would appear in
``channel-report`` like any other channel if P6 ever lands — it is not special-cased here,
and this module defines no channel of its own.

**It never rounds up.** :data:`CONFIDENCE_BY_STATUS` maps each envelope status to a
signal, and :func:`confidence_rank` orders them so a test can assert the mapping is
monotone: no report is ever more favourable than the envelope it came from (Law 2).
``needs_clarification`` maps to *blocked-on-you* and is a QUESTION, not a failure — being
asked something is not the system failing.

**It is quiet.** Reports go out on completion, question, failure and cost ask, and on
nothing else. §3.6: *"Not on every step. Silence means it is running."*
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional, Sequence

log = logging.getLogger(__name__)


class ReportKind(str, Enum):
    """What a report IS. ``QUESTION`` is deliberately distinct from ``FAILURE``."""

    COMPLETION = "completion"
    QUESTION = "question"
    FAILURE = "failure"
    COST_ASK = "cost-ask"


#: The ONLY events that produce a report (§3.6). Anything else is silence.
REPORTABLE_EVENTS: frozenset[ReportKind] = frozenset(ReportKind)


class Confidence(str, Enum):
    """The signal on a report. Ordered least → most favourable by :func:`confidence_rank`."""

    FAILED = "failed"
    BLOCKED_ON_YOU = "blocked-on-you"
    NEEDS_ATTENTION = "needs-attention"
    AWAITING_REVIEW = "awaiting-review"
    CONFIDENT = "confident"


_CONFIDENCE_ORDER: tuple[Confidence, ...] = (
    Confidence.FAILED,
    Confidence.BLOCKED_ON_YOU,
    Confidence.NEEDS_ATTENTION,
    Confidence.AWAITING_REVIEW,
    Confidence.CONFIDENT,
)

#: Envelope status → confidence signal (§3.6, read directly off the envelope status).
CONFIDENCE_BY_STATUS: dict[str, Confidence] = {
    "done": Confidence.CONFIDENT,
    "partial": Confidence.NEEDS_ATTENTION,
    "needs_clarification": Confidence.BLOCKED_ON_YOU,
    "needs_review": Confidence.AWAITING_REVIEW,
    "failed": Confidence.FAILED,
}

#: Envelope status → report kind. needs_clarification is a QUESTION (AC-PME-13).
KIND_BY_STATUS: dict[str, ReportKind] = {
    "done": ReportKind.COMPLETION,
    "partial": ReportKind.COMPLETION,
    "needs_review": ReportKind.COMPLETION,
    "needs_clarification": ReportKind.QUESTION,
    "failed": ReportKind.FAILURE,
}

#: The ceiling a status may claim. Used to prove the mapping never rounds up.
_STATUS_CEILING: dict[str, Confidence] = dict(CONFIDENCE_BY_STATUS)


def confidence_rank(value: Any) -> int:
    """Position in the least→most favourable order. Unknown ⇒ lowest (fails safe)."""
    if isinstance(value, Confidence):
        return _CONFIDENCE_ORDER.index(value)
    if isinstance(value, str):
        for i, c in enumerate(_CONFIDENCE_ORDER):
            if c.value == value:
                return i
    return 0


def status_rank(status: Any) -> int:
    """The ceiling rank a given envelope status is allowed to produce."""
    ceiling = _STATUS_CEILING.get(str(status))
    return confidence_rank(ceiling) if ceiling is not None else 0


@dataclass(frozen=True)
class Report:
    """One report. Carries the headline, the receipts, the draft link and the signal."""

    task_id: Any
    kind: ReportKind
    confidence: Confidence
    headline: str
    detail: str = ""
    receipts: tuple[str, ...] = ()
    draft_id: Any = None
    question: Optional[str] = None
    recipients: tuple[str, ...] = ()
    channel: Optional[str] = None

    @property
    def is_failure(self) -> bool:
        return self.kind is ReportKind.FAILURE


@dataclass
class DeliveryResult:
    delivered: list[Report] = field(default_factory=list)
    #: Reports with nowhere to go — they surface on the draft card rather than vanishing.
    surfaced_on_card: list[Report] = field(default_factory=list)
    attempted_channels: list[str] = field(default_factory=list)
    errors: list[BaseException] = field(default_factory=list)


def _envelope_field(envelope: Any, name: str, default: Any = None) -> Any:
    if isinstance(envelope, dict):
        return envelope.get(name, default)
    return getattr(envelope, name, default)


def build_report(
    envelope: Any,
    *,
    task_id: Any,
    owner: str,
    named_recipients: Sequence[str] = (),
) -> Report:
    """Turn a Workroom envelope into a report. Never upgrades the signal.

    An unknown status is treated as a FAILURE at the lowest confidence rather than being
    optimistically mapped — an unrecognised envelope is not a success.
    """
    status = str(_envelope_field(envelope, "status", "failed"))
    kind = KIND_BY_STATUS.get(status, ReportKind.FAILURE)
    confidence = CONFIDENCE_BY_STATUS.get(status, Confidence.FAILED)

    headline = str(_envelope_field(envelope, "headline", "") or "")
    detail = str(_envelope_field(envelope, "detail", "") or "")
    reason = _envelope_field(envelope, "error") or _envelope_field(envelope, "reason")
    if kind is ReportKind.FAILURE and reason:
        # Law 2: the reason travels with the failure; it is not summarised away.
        detail = f"{detail}\n{reason}".strip()
    raw_receipts = _envelope_field(envelope, "receipts", ()) or ()
    if isinstance(raw_receipts, (str, bytes)) or not isinstance(raw_receipts, Iterable):
        # A bare string is one receipt's text, not an iterable of receipts; treating it as
        # iterable would explode it into per-character "receipts".
        receipts: tuple[str, ...] = ()
    else:
        receipts = tuple(str(r) for r in raw_receipts)

    # dict.fromkeys preserves order while de-duplicating: the owner leads, and a named
    # recipient who is also the owner is not messaged twice.
    if owner:
        recipients = tuple(dict.fromkeys([owner, *named_recipients]))
    else:
        recipients = tuple(dict.fromkeys(named_recipients))

    return Report(
        task_id=task_id,
        kind=kind,
        confidence=confidence,
        headline=headline or status,
        detail=detail,
        receipts=receipts,
        draft_id=_envelope_field(envelope, "draft_id"),
        question=_envelope_field(envelope, "question") if kind is ReportKind.QUESTION else None,
        recipients=recipients,
    )


#: The one channel that outlives the bot today: Doc 08's draft card. Named here because
#: it is not in ``ChannelReport`` — that contract reports the MEETING's channels, and by
#: the time post-meeting execution runs the bot has left and platform chat is gone
#: (Doc 07 §3.6). Slack would join this set via P6, which stayed deferred because the card
#: exists; nothing here special-cases either one.
DRAFT_CARD = "draft_card"


def select_channel(
    channel_report: Any, *, card_available: bool = True
) -> Optional[str]:
    """Pick a delivery channel, or ``None``.

    Takes Doc 02's real ``contracts.channels.ChannelReport`` — the same typed contract
    ``transport/chat.py`` gates DM delivery on — rather than a parallel list-of-strings
    shape. ``dm_available`` is its only field; a ``ChannelReport`` with
    ``dm_available=False`` means the platform offers no private route.

    Post-meeting, ``dm_available`` alone is not enough: the bot has left, so a platform DM
    is not reachable even when the meeting reported one. That is why the draft card is the
    channel this returns — Doc 07 §3.6's *"what remains is an out-of-meeting channel only
    if one exists"*. ``None`` means nowhere to send, and the caller surfaces the report on
    the card record instead of inventing a route (AC-PME-16-NEG).
    """
    if card_available:
        return DRAFT_CARD
    return None


def channels_in(channel_report: Any) -> frozenset[str]:
    """The channel names a report makes available, for the discipline assertions.

    Accepts a real ``ChannelReport`` (or anything exposing ``dm_available``). The draft
    card is always in the set post-meeting; a platform DM is in it only if the meeting
    reported one — and even then it is not *reachable* after teardown, which is why
    :func:`select_channel` does not choose it.
    """
    available = {DRAFT_CARD}
    if bool(getattr(channel_report, "dm_available", False)):
        available.add("platform_dm")
    return frozenset(available)


def build_draft_card(report: "Report", *, meeting_id: Any) -> Any:
    """Render the report's draft card through Doc 08's ``format_draft_card``.

    Doc 07 defines no card shape of its own. This builds the ``Envelope`` the transport
    formatter already consumes and hands it over, so the card render and the ``/m/``
    accept route keep reading the SAME typed ``draft_id`` field (CANONICAL §11.5) and a
    change to the card's wording lands in one place.
    """
    from contracts import Envelope
    from transport.chat import format_draft_card

    envelope = Envelope(
        headline=report.headline,
        detail=report.detail or report.headline,
        receipts=list(report.receipts),
        status="needs_review",
        task_id=str(report.task_id),
        draft_id=report.draft_id,
    )
    return format_draft_card(envelope, meeting_id=meeting_id)


async def deliver(
    reports: Sequence[Report],
    *,
    channel_report: Any,
    send: Any,
    already_delivered: Optional[set[Any]] = None,
    card_available: bool = True,
) -> DeliveryResult:
    """Deliver reports on a channel-report channel. Idempotent per (task, kind).

    ``channel_report`` is Doc 02's real ``contracts.channels.ChannelReport``.
    ``already_delivered`` carries the ``(task_id, kind)`` pairs a previous pass sent, so a
    retry after a send that actually succeeded does not duplicate (AC-PME-16-NEG).
    """
    result = DeliveryResult()
    seen = already_delivered if already_delivered is not None else set()
    channel = select_channel(channel_report, card_available=card_available)

    for report in reports:
        if report.kind not in REPORTABLE_EVENTS:
            continue  # silence is the default; nothing else produces a report
        key = (report.task_id, report.kind)
        if key in seen:
            continue
        if channel is None:
            # Nowhere to send: surface on the draft card rather than dropping it.
            result.surfaced_on_card.append(report)
            seen.add(key)
            continue
        result.attempted_channels.append(channel)
        try:
            await send(channel=channel, report=report)
        except Exception as exc:  # noqa: BLE001 - retry stays on listed channels only
            log.exception("report delivery failed for task %s", report.task_id)
            result.errors.append(exc)
            # An undeliverable report surfaces on the card; it never vanishes and it
            # never causes a reach for an unlisted channel.
            result.surfaced_on_card.append(report)
            seen.add(key)
            continue
        seen.add(key)
        result.delivered.append(
            Report(**{**report.__dict__, "channel": channel})
        )
    return result
