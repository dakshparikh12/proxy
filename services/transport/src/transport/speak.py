"""Speaking: text in → one calm voice out, every line also in chat (§3.3 / §3.9-4).

The real speak path, layered on the M4 turn-core. Upstream (Doc 04) hands this layer
text; this layer:

- posts the **verbatim** text copy to chat FIRST (parity recall 1.0 — the copy is free
  since we have the text before synthesis, and posting first means the copy survives even
  if the synth/Output-Media leg fails — AC-SPEAK-04/05/15, AC-CHAT-07);
- synthesizes the **exact** text with Cartesia — no headline auto-extraction or
  substitution (AC-SPEAK-01) — through the boundary-gated :class:`~transport.turn.TurnController`
  so voice starts only on a boundary and aborts instantly on barge-in (AC-SPEAK-06/07/08);
- holds the **headlines-only** envelope: a single line over the soft cap, or one that
  would breach the ~2–4k chars/meeting-hour budget, is detail — routed to chat and NOT
  spoken (AC-SPEAK-03);
- one calm voice/register across every line (the voice lives in :class:`CartesiaTTS`,
  AC-SPEAK-02).

**Ack-audible reflex** (§3.3): a direct answer can fire a ≤500ms canned "on it" line the
instant it's picked up while the real answer resolves. The ack is a fixed canned string,
independent of the eventual answer (AC-SPEAK-10), boundary-gated and barge-able on the
same uniform path (AC-TURN-17). Latency thresholds (ack p95≤500ms, TTFA ~40ms,
decision→audible <1s, first-grounded-audio) are pinned-measured at M11.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from . import config
from .turn import TurnController

#: The fixed canned audible-ack set — the ack is drawn from here and is independent of the
#: resolved answer content (AC-SPEAK-10). Never the answer itself.
CANNED_ACKS: tuple[str, ...] = ("on it", "on it — checking", "one moment")

_ONE_HOUR_S = 3600.0


@dataclass(frozen=True)
class SpeakOutcome:
    """Honest result of a speak() call — spoken vs routed-to-chat, and whether copied."""

    text: str
    spoken: bool
    chat_copy_posted: bool
    reason: str = ""


class SpeakOrchestrator:
    """The real speak path: verbatim chat copy + boundary-gated Cartesia synth + envelope.

    ``post_copy`` is the broadcast text-copy seam (wired to the chat channel by M6 / the
    harness). The synthesis + Output-Media streaming is owned by the injected
    :class:`TurnController` (M4), so barge-in / hard-mute apply to spoken lines unchanged.
    """

    def __init__(
        self,
        controller: TurnController,
        *,
        post_copy: Callable[[str], Awaitable[None]],
        now: Callable[[], float] = time.monotonic,
        headline_cap: int | None = None,
        hourly_cap: int | None = None,
    ) -> None:
        self._controller = controller
        self._post_copy = post_copy
        self._now = now
        self._headline_cap = headline_cap if headline_cap is not None else config.get_int("headline_char_soft_cap")
        self._hourly_cap = hourly_cap if hourly_cap is not None else config.get_int("max_spoken_chars_per_hour")
        self._spoken_window: deque[tuple[float, int]] = deque()

    async def speak(self, text: str) -> SpeakOutcome:
        """Speak one line: post the verbatim copy, then synthesize iff within the envelope."""
        # Text copy FIRST — free (we have the text pre-synthesis) and survives an audio
        # leg failure, so parity holds even under fault (AC-SPEAK-04/05/15).
        await self._post_copy(text)

        if not self._within_envelope(text):
            # Detail: routed to chat (already posted), never spoken (AC-SPEAK-03). No
            # extraction/substitution of the supplied text (AC-SPEAK-01).
            return SpeakOutcome(text=text, spoken=False, chat_copy_posted=True, reason="detail_routed_to_chat")

        self._account(text)
        # Boundary-gated synth of the EXACT text via the turn-core (AC-SPEAK-01/06/07/08).
        self._controller.enqueue(text)
        return SpeakOutcome(text=text, spoken=True, chat_copy_posted=True)

    async def audible_ack(self) -> SpeakOutcome:
        """Fire a canned audible ack (§3.3) — distinct from the answer (AC-SPEAK-10).

        The ack rides the same boundary-gated path as any line, so no ack audio is ever
        emitted before a boundary opens (AC-SPEAK-19); when no boundary opens in budget it
        simply never plays — the tile ACK (Doc 08 / :mod:`transport.canvas`) is the visual
        fallback, so there is no ungated audible ack (AC-SPEAK-20).
        """
        ack = CANNED_ACKS[0]  # fixed canned string, never the resolved answer
        return await self.speak(ack)

    async def deliver_detail(self, detail: str) -> None:
        """Route upstream-marked detail to chat — never spoken, never dropped (AC-SPEAK-18).

        The headline↔detail split is Doc 04's judgment; this layer's obligation is the
        plumbing: any content marked detail is posted to the broadcast channel.
        """
        await self._post_copy(detail)

    async def speak_headline_with_detail(self, headline: str, detail: str) -> SpeakOutcome:
        """Speak the headline AND post its paired detail to chat — detail never dropped."""
        outcome = await self.speak(headline)
        await self.deliver_detail(detail)
        return outcome

    def _within_envelope(self, text: str) -> bool:
        if len(text) > self._headline_cap:
            return False
        return self._spoken_chars_last_hour() + len(text) <= self._hourly_cap

    def _account(self, text: str) -> None:
        self._spoken_window.append((self._now(), len(text)))

    def _spoken_chars_last_hour(self) -> int:
        cutoff = self._now() - _ONE_HOUR_S
        while self._spoken_window and self._spoken_window[0][0] < cutoff:
            self._spoken_window.popleft()
        return sum(chars for _, chars in self._spoken_window)

    def spoken_chars_last_hour(self) -> int:
        """The aggregate synthesized chars in the trailing hour (AC-SPEAK-03 oracle read)."""
        return self._spoken_chars_last_hour()
