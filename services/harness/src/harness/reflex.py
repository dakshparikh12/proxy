"""The reflex layer — code-not-agent physics (§2 / §3.17 step 4).

This is the ONLY place in Doc 04 where behaviour is decided by code rather than by a
Proxy wake turn, and it is deliberately tiny: the three things §2 names as *physics,
not decisions*, where "a model in the loop would be malpractice".

  1. The canned **"on it" ack** fires within **0.5s** of a CONFIRMED address (dead air
     is the enemy). It is **FIFO-honest** when Proxy is mid-answer — a second ask acks
     "on it — right after Sam's", never falsely implying immediate service (§3.15 /
     R-doc04-BOOK-15). The ack is a fixed **canned string, never the answer** (Law 2):
     the reflex fires it BEFORE the wake turn, so the ask is already acknowledged by the
     time the ~1s wake completes.
  2. Voice may start **only into a turn boundary** (§3.6): the ack is streamed through
     the boundary-gated :class:`~transport.turn.TurnController`, so no audio reaches the
     room until an ``end_of_turn`` boundary opens.
  3. **Barge-in** kills speech in **<200ms** (Law 3 — human control is absolute): a human
     speech onset aborts the in-flight ack mid-word and flushes both the Output-Media
     buffer and the pending ack queue, so Proxy never speaks over a human.

**No model, ever.** This module constructs no SDK client and imports no provider seam —
the ack text is drawn from a fixed canned set (:data:`CANNED_ACK`) and the FIFO qualifier
is pure string composition over an in-process queue. The one external collaborator is the
transport turn-core (boundary gating + barge-in abort), reused verbatim — the reflex adds
no second stop path. That keeps the reflex genuinely sub-second and pre-agent, which the
0.5s / 200ms SLOs (CANONICAL §12.8 / §4) depend on.

Self-contained on purpose: the reflex consumes a minimal :class:`ConfirmedAddress`
(``text`` + ``speaker``) — structurally the confirmed-address payload the name-gate's
``AddressingVerdict`` carries — so this node builds and proves in isolation of the
sibling ``orchestrator.name-gate`` node. :meth:`AckReflex.fire_if_confirmed` accepts any
verdict-shaped object (a ``.wake`` flag + ``.text``/``.speaker``) so the harness can hand
the name-gate's own verdict straight through with no adapter.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from transport.turn import TurnController, VadFrame

#: The fixed canned ack register (§3.3 / Law 2). The ack is drawn from here and is
#: independent of the resolved answer — it is NEVER the answer itself. A single phrase
#: is enough: variety in the ack is not a product goal, honesty and speed are.
CANNED_ACK: str = "on it"


@dataclass(frozen=True)
class ConfirmedAddress:
    """A confirmed address to Proxy — the reflex layer's entry payload.

    ``text`` is the verbatim ask (spoken words or chat line) and ``speaker`` is who
    addressed Proxy. This is the minimal confirmed-address shape the name-gate produces;
    the reflex never inspects ``text`` for content (the ack is canned), only carries the
    ``speaker`` forward so a FIFO-honest ack can name whose ask is ahead.
    """

    text: str
    speaker: str


class _ConfirmedVerdict(Protocol):
    """The structural shape of the name-gate's ``AddressingVerdict`` the reflex reads.

    Only ``wake`` gates the reflex; ``text``/``speaker`` carry the confirmed ask forward.
    Declared as a Protocol so the reflex never imports the name-gate module (self-contained).
    """

    wake: bool
    text: str
    speaker: str


def ack_text(addr: ConfirmedAddress, *, ahead: str | None = None) -> str:
    """Return the canned ack for a confirmed address — FIFO-honest when ``ahead`` is set.

    With the mouth free the ack is the bare canned :data:`CANNED_ACK` ("on it"). When
    another speaker's ask is already in flight (``ahead``), the ack is honestly qualified
    — "on it — right after Sam's" (§3.15 / R-doc04-BOOK-15) — so Proxy never implies it
    will answer this ask before the one already ahead of it. The phrase is composed
    purely from the canned register + the prior speaker's name; NO content from ``addr``
    leaks in, so the ack can never be mistaken for the answer (Law 2).
    """
    if ahead:
        return f"{CANNED_ACK} — right after {ahead}'s"
    return CANNED_ACK


class AckReflex:
    """The 0.5s canned-ack reflex with a FIFO-honest, mouth-busy queue (§2 / §3.15).

    The reflex keeps a FIFO of the speakers whose confirmed asks are in flight (mouth
    busy). The FIRST ask (mouth free) acks the bare "on it"; a later ask while busy acks
    "on it — right after <head-of-line speaker>'s". :meth:`complete_current` frees the
    mouth (the head-of-line ask has been delivered), so the next ask acks bare again.

    ``speak`` is the canned-emit seam — a plain async callable wired by the harness to the
    boundary-gated speak path. It defaults to a no-op so the FIFO logic and the deadline
    can be proven in isolation without an audio leg. The reflex NEVER routes through a
    model: it awaits only ``speak`` and touches no provider.
    """

    def __init__(self, speak: Callable[[str], Awaitable[None]] | None = None) -> None:
        self._speak = speak
        # FIFO of in-flight asks' speakers (head = the ask currently being served).
        self._in_flight: deque[str] = deque()

    def ack_for(self, addr: ConfirmedAddress) -> str:
        """Compute (and record) the FIFO-honest ack for a newly confirmed address.

        Returns the canned ack string; if Proxy is mid-answer the ack names the
        head-of-line speaker. The new ask is appended to the in-flight FIFO (mouth now
        busy at least through this ask). This is pure, synchronous bookkeeping — the
        latency-critical spoken emit is :meth:`fire`.
        """
        ahead = self._in_flight[0] if self._in_flight else None
        phrase = ack_text(addr, ahead=ahead)
        self._in_flight.append(addr.speaker)
        return phrase

    def complete_current(self) -> None:
        """Mark the head-of-line ask delivered (mouth frees) — drains one FIFO slot."""
        if self._in_flight:
            self._in_flight.popleft()

    async def fire(self, addr: ConfirmedAddress) -> str:
        """Emit the canned ack for a confirmed address — the latency-critical reflex path.

        Computes the FIFO-honest ack and speaks it via the injected canned seam, BEFORE
        any wake turn runs (§3.17 step 4). Returns the ack string spoken. No model is
        touched; the only await is the canned ``speak`` seam.
        """
        phrase = self.ack_for(addr)
        if self._speak is not None:
            await self._speak(phrase)
        return phrase

    async def fire_if_confirmed(self, verdict: _ConfirmedVerdict) -> str | None:
        """Fire the ack ONLY when the address is confirmed (``verdict.wake`` is True).

        An unconfirmed verdict (a name-hit the disambiguator rejected — "the proxy server
        config") never reaches the ack path (AC-GATE-004-NEG). Returns the spoken ack
        string, or ``None`` when nothing was confirmed.
        """
        if not getattr(verdict, "wake", False):
            return None
        return await self.fire(ConfirmedAddress(text=verdict.text, speaker=verdict.speaker))


class BoundaryGatedAck:
    """The ack delivered through the boundary-gated turn-core, with a <200ms barge-in stop.

    Voice may start only into a turn boundary (§3.6): :meth:`fire` enqueues the canned ack
    on the :class:`~transport.turn.TurnController` — it is spoken only when
    :meth:`TurnController.on_boundary` releases it. :meth:`barge_in` drives a human speech
    onset through the turn-core's single stop path (abort in-flight TTS mid-word + flush
    the Output-Media buffer + flush the pending ack queue), so Proxy stops within 200ms
    and never speaks over the human, even if the ack was still queued.

    This class owns NO second stop mechanism — barge-in reuses the transport turn-core's
    abort exactly, keeping one honest cut path (the small-chunk Output-Media buffer keeps
    the cut mid-word).
    """

    def __init__(self, controller: TurnController) -> None:
        self._controller = controller
        self._reflex = AckReflex()

    async def fire(self, addr: ConfirmedAddress) -> str:
        """Queue the canned ack for boundary-gated release (never speaks immediately)."""
        phrase = self._reflex.ack_for(addr)
        self._controller.enqueue(phrase)  # released only on the next boundary
        return phrase

    async def fire_if_confirmed(self, verdict: _ConfirmedVerdict) -> str | None:
        """Boundary-gated twin of :meth:`AckReflex.fire_if_confirmed`."""
        if not getattr(verdict, "wake", False):
            return None
        return await self.fire(ConfirmedAddress(text=verdict.text, speaker=verdict.speaker))

    async def barge_in(self, frame: VadFrame) -> None:
        """A human speech onset: stop the ack mid-word + flush queue (<200ms, Law 3).

        Reuses the turn-core barge-in — it aborts the in-flight utterance, flushes the
        Output-Media sink (≤1 small chunk survives), and clears the pending ack queue.
        ``frame`` carries the VAD onset instant; the stop is the transport's single
        cooperative abort path, so no second buffer defeats the sub-200ms cut.
        """
        await self._controller.barge_in()

    async def quiet(self, task_key: str | None = None) -> None:
        """"Proxy, quiet" (and meeting-end barge-in): cut speech AND halt the model loop.

        The speech cut is the same sub-200ms turn-core path (never replaced); this ADDS
        the §3.11 model-loop kill — the addressed in-flight wake's controller (keyed
        ``meeting_id|ask_id`` in ``task_key``, from the run loop's in-flight bookkeeping)
        is cancelled on the shared :class:`~agentkit.abort.AbortRegistry`, so the model
        loop halts, not just the mouth (fault F-CTRL-QUIET-IGNORED). Reuses the turn-core's
        :meth:`~transport.turn.TurnController.quiet` — the reflex owns no second stop path.
        """
        await self._controller.quiet(task_key)
