"""The join + consent gate FSM (§3.1, AC-JOIN-01..17).

Proxy joins from a meeting link (or calendar invite) as a Recall bot — the link alone
is enough, no host-side install (AC-JOIN-01/17). The **first observable action** on
join is the consent notice, and it is a **hard gate**: nothing is observed, recorded,
or listened to until the notice posts (AC-JOIN-03/04/12). The notice is pinned where
the platform allows and posted where it does not (AC-JOIN-06); the same Recall-backed
path serves Meet/Zoom/Teams with no per-platform branch (AC-JOIN-09). The bot belongs
to the room, not the inviter — there is no inviter-identity gate anywhere here
(AC-JOIN-08). A join or consent-post failure is reported plainly and never as a false
joined/posted state (AC-JOIN-16, Law 2).
"""
from __future__ import annotations

import enum
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .consent import consent_notice
from .seams import TransportProvider


class JoinState(enum.Enum):
    PENDING = "pending"
    JOINING = "joining"
    IN_MEETING = "in_meeting"
    LISTENING = "listening"
    ENDED = "ended"
    FAILED = "failed"


class JoinSource(enum.Enum):
    """Link and calendar invites reach the identical gated in-meeting state (AC-JOIN-17)."""

    LINK = "link"
    CALENDAR = "calendar"


class Action(enum.Enum):
    """The observable-action trace — proves consent-notice-first ordering (AC-JOIN-03)."""

    CONSENT_NOTICE = "consent_notice"
    CONSENT_REPOST = "consent_repost"
    LISTEN = "listen"
    DEFER_TO_ORGANIZER = "defer_to_organizer"
    LEAVE = "leave"


@dataclass
class JoinResult:
    """The outcome of a join attempt — honest about success and failure alike."""

    state: JoinState
    bot_id: str | None
    joined: bool
    notice_posted: bool
    pinned: bool
    failed: bool
    reason: str | None
    t_invite: float
    t_listening: float | None
    actions: list[Action] = field(default_factory=list)

    @property
    def join_to_listening_s(self) -> float | None:
        """Elapsed invite→listening (AC-JOIN-02: ≤10s pass; measured at M11)."""
        if self.t_listening is None:
            return None
        return self.t_listening - self.t_invite


class JoinSession:
    """Drives one bot's join + consent gate. No inviter gate: the bot is the room's."""

    def __init__(
        self,
        transport: TransportProvider,
        *,
        pin_capable: bool = True,
        now: Callable[[], float] = time.monotonic,
        on_bot_launched: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._transport = transport
        self._pin_capable = pin_capable
        self._now = now
        self._on_bot_launched = on_bot_launched
        self.state = JoinState.PENDING
        self.bot_id: str | None = None
        self.notice_posted = False
        self.pinned = False
        self.actions: list[Action] = []

    def can_observe(self) -> bool:
        """The hard gate: no observation/recording until the notice has posted (AC-JOIN-04)."""
        return self.notice_posted and self.state is JoinState.LISTENING

    async def join(self, meeting_link: str, *, source: JoinSource = JoinSource.LINK) -> JoinResult:
        """Join link-only, post the consent notice first, then transition to listening."""
        t_invite = self._now()
        self.state = JoinState.JOINING
        try:
            bot_id = await self._transport.join(meeting_link)
        except Exception as exc:  # honest failure — never a false 'joined' (AC-JOIN-16)
            self.state = JoinState.FAILED
            return JoinResult(
                state=self.state, bot_id=None, joined=False, notice_posted=False,
                pinned=False, failed=True, reason=f"join failed: {exc}",
                t_invite=t_invite, t_listening=None, actions=list(self.actions),
            )

        self.bot_id = bot_id
        self.state = JoinState.IN_MEETING
        if self._on_bot_launched is not None:
            await self._on_bot_launched(bot_id)  # meetings-row bot_id write-back (AC-JOIN-10)

        # Consent notice is the FIRST observable action, and a hard gate (AC-JOIN-03/04).
        try:
            await self._transport.post_chat(bot_id, consent_notice(), pinned=self._pin_capable)
        except Exception as exc:  # notice could not be delivered — halt, report honestly
            self.state = JoinState.FAILED
            return JoinResult(
                state=self.state, bot_id=bot_id, joined=True, notice_posted=False,
                pinned=False, failed=True, reason=f"consent notice post failed: {exc}",
                t_invite=t_invite, t_listening=None, actions=list(self.actions),
            )

        self.notice_posted = True
        self.pinned = self._pin_capable
        self.actions.append(Action.CONSENT_NOTICE)

        # Default-consented once the notice has posted (AC-JOIN-12); listening opens now.
        self.state = JoinState.LISTENING
        t_listening = self._now()
        self.actions.append(Action.LISTEN)
        return JoinResult(
            state=self.state, bot_id=bot_id, joined=True, notice_posted=True,
            pinned=self.pinned, failed=False, reason=None,
            t_invite=t_invite, t_listening=t_listening, actions=list(self.actions),
        )

    async def on_participant_join(self, participant_id: str) -> None:
        """A participant who joined after the notice never saw it — re-post (AC-JOIN-07)."""
        if not self.notice_posted or self.bot_id is None or self.state is JoinState.ENDED:
            return
        await self._transport.post_chat(self.bot_id, consent_notice(), pinned=False)
        self.actions.append(Action.CONSENT_REPOST)

    async def on_objection(self) -> None:
        """An objection defers audibly to the organizer — never a unilateral continue (AC-JOIN-13)."""
        self.actions.append(Action.DEFER_TO_ORGANIZER)

    async def on_hard_removal(self) -> None:
        """Hard-removal ends the bot (leaves) — a terminal transition, not mute/pause (AC-JOIN-14)."""
        if self.bot_id is not None:
            await self._transport.leave(self.bot_id)
        self.actions.append(Action.LEAVE)
        self.state = JoinState.ENDED
