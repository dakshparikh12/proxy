"""Abort registry — the single home for cooperative abortion (AC-CMP-010, §11.9).

This is the ONE definition site for the ``AbortController`` + ``AbortRegistry``
(CANONICAL §11.9): Docs 04 §3.11 and 05 §3.11 **import** these, neither redefines
them. Two abort disciplines share this home, and they are orthogonal:

  * **The model-loop abort (§3.11).** Every wake turn and every dispatched Workroom
    run is threaded with an :class:`AbortController` held in a
    ``dict[meeting_id|task_id, AbortController]``. Aborting halts the *model loop*
    itself — the provider seam breaks its ``async for message`` loop — not merely
    ignoring the result (the SDK would otherwise run to ``maxTurns=1000``, burning
    budget). **Abort is FINAL, never retried** — this is what makes §3.5's
    stale-session/JSON retry safe: a build the user killed can't be resurrected.
    Wired to three triggers: meeting-end (:meth:`AbortRegistry.cancel_meeting` kills
    everything), whisper-"stop"/"Proxy, quiet" (:meth:`AbortRegistry.cancel` on the
    addressed task), and a hard per-task timeout. A *new* judgment-moment for a live
    key preempts the stale one — :meth:`AbortRegistry.make` cancels the prior first.

  * **The TTS barge-in stop (§3.6, transport.turn).** ``TurnController`` reuses this
    same registry to mark an in-flight utterance id as cancelled so the synth loop
    stops mid-word. That is a set-membership check keyed by an utterance id, kept here
    as :meth:`abort` / :meth:`clear` / :meth:`is_aborted` so transport imports the ONE
    registry (§11.9) rather than defining a second primitive.
"""
from __future__ import annotations

import asyncio

#: The delimiter between a meeting id and a task id in a registry key
#: (``meeting_id|task_id``, §3.11). ``cancel_meeting`` splits on it so a meeting id
#: that is a prefix of another (``meetingA`` vs ``meetingA2``) never false-matches.
KEY_SEP = "|"


class AbortController:
    """A single cooperative abort handle (§3.11) wrapping an :class:`asyncio.Event`.

    ``aborted`` is the flag the provider seam polls to break the model loop and the
    §3.5 recovery reads to short-circuit; ``wait()`` lets an awaiter block until the
    abort fires. Aborting is idempotent and final — there is no ``un-abort``.
    """

    __slots__ = ("_evt",)

    def __init__(self) -> None:
        self._evt = asyncio.Event()

    @property
    def aborted(self) -> bool:
        """True once :meth:`abort` has fired (the flag the model loop polls)."""
        return self._evt.is_set()

    def abort(self) -> None:
        """Fire the abort — final and idempotent; the model loop halts, never retries."""
        self._evt.set()

    async def wait(self) -> None:
        """Block until this controller is aborted (returns immediately if already)."""
        await self._evt.wait()


class AbortRegistry:
    """The one registry both abort disciplines share (§11.9).

    * **Controllers** (§3.11): :meth:`make` mints a per-key :class:`AbortController`,
      preempting any prior one for that key; :meth:`cancel` aborts + drops a key;
      :meth:`cancel_meeting` aborts every controller of one meeting.
    * **Utterance-id set** (§3.6): :meth:`abort` / :meth:`clear` / :meth:`is_aborted`
      mark a TTS utterance id cancelled so the synth loop stops mid-word.
    """

    def __init__(self) -> None:
        # The model-loop controllers, keyed ``meeting_id|task_id`` (§3.11).
        self._controllers: dict[str, AbortController] = {}
        # The TTS-barge-in cancelled utterance ids (§3.6, transport.turn).
        self._aborted: set[str] = set()

    # -- model-loop controllers (§3.11) ---------------------------------------

    def make(self, key: str) -> AbortController:
        """Mint a fresh :class:`AbortController` for ``key``, preempting any prior one.

        A new judgment-moment for a task that already has a live controller CANCELS
        the stale one first (stale-judgment preemption) — the just-superseded turn is
        aborted so it stops burning budget, and the fresh controller is returned.
        """
        self.cancel(key)                       # a new judgment-moment preempts a stale one
        controller = AbortController()
        self._controllers[key] = controller
        return controller

    def cancel(self, key: str) -> None:
        """Abort + drop the controller for ``key`` (no-op if absent). Idempotent."""
        controller = self._controllers.pop(key, None)
        if controller is not None:
            controller.abort()

    def cancel_meeting(self, meeting_id: str) -> None:
        """On meeting-end: abort every controller for this meeting, and only this one.

        A key is ``meeting_id|task_id``; matching splits on :data:`KEY_SEP` so a
        meeting id that is a text prefix of another (``meetingA`` / ``meetingA2``)
        never false-cancels the other meeting's tasks (isolation).
        """
        doomed = [k for k in self._controllers if k.split(KEY_SEP, 1)[0] == meeting_id]
        for key in doomed:
            self.cancel(key)

    def get(self, key: str) -> AbortController | None:
        """The live controller for ``key`` (``None`` if none / already cancelled)."""
        return self._controllers.get(key)

    def active_keys(self) -> frozenset[str]:
        """The keys with a live (un-cancelled) controller — for reconcile/telemetry."""
        return frozenset(self._controllers)

    # -- TTS barge-in utterance-id set (§3.6, transport.turn) -----------------

    def abort(self, task_id: str) -> None:
        """Mark a TTS utterance id cancelled (transport barge-in / quiet / preempt)."""
        self._aborted.add(task_id)

    def clear(self, task_id: str) -> None:
        """Un-mark an utterance id (a fresh utterance reuses the id cleanly)."""
        self._aborted.discard(task_id)

    def is_aborted(self, task_id: str) -> bool:
        """True iff this utterance id was marked cancelled (the synth loop polls it)."""
        return task_id in self._aborted


__all__ = ["AbortController", "AbortRegistry", "KEY_SEP"]
