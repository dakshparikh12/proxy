"""The REAL name-hit disambiguator — ONE bounded confirm call (SPEC §2/§3.1).

The trigger's voice path fires on a mechanical ``\\bproxy\\b`` word hit; THIS
module answers the one question that scan cannot: "addressed to me, or the
common noun ('proxy server')?" — a single tiny Claude Agent SDK turn (Haiku,
``max_turns=1``, ~zero output tokens), spent ONLY on hits ("pennies, only on
hits"). It is the real implementation behind the trigger's injected async
``Disambiguate`` seam — the old brain shipped an always-true stub and the sims
used a regex heuristic; this is the model call both stood in for.

SDK discipline (the proven ``tests/eval/subscription_judge.py`` pattern):

* ``ANTHROPIC_API_KEY`` is POPPED before every real SDK call — subscription CLI
  auth only, the paid API key is never used and never logged (no secrets read).
* The SDK seam is injectable (``query_fn``): the offline unit suite drives the
  parse/fail-open physics with fake async streams; the default lazily imports
  the real ``claude_agent_sdk.query`` with the subscription option triad
  (``permission_mode="bypassPermissions"``, ``strict_mcp_config=True``,
  ``setting_sources=[]``) + ``max_turns=1``. The lazy import keeps the module
  importable (and the unit tests runnable) without a live CLI.

**FAIL-OPEN, visibly.** Any SDK fault, unparseable output, or WALL-CLOCK
timeout returns ``True`` (wake): better to wake and let the agent judge the
line than to go deaf to a human because a confirm call broke — human control
is absolute, and a false wake costs pennies while a false sleep ignores a
person. ``max_turns=1`` bounds turns, not time, so the whole confirm
round-trip additionally runs under ``asyncio.timeout(CONFIRM_TIMEOUT_S)`` — a
wedged CLI/stream can never hold the trigger consult (and with it the
sequential feed) open forever. The fault is recorded on the callable
(``last_error``) so a broken disambiguator is VISIBLE on inspection, never a
silent always-wake.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

#: The pinned confirm seat — the smallest/cheapest seat that resolves address
#: vs. common noun reliably; the call is bounded to one turn either way.
DEFAULT_DISAMBIGUATOR_MODEL = "claude-haiku-4-5"

#: The WALL-CLOCK bound on one whole confirm round-trip (open stream → collect
#: → close). ``max_turns=1`` bounds turns, not time — this bounds time: a
#: wedged CLI/stream times out and FAILS OPEN instead of hanging the trigger
#: consult (and the sequential transcript feed behind it) forever. Generous
#: for a one-turn Haiku call; injectable per instance for tests.
CONFIRM_TIMEOUT_S: float = 10.0

#: The injectable SDK seam: (prompt) -> async stream of SDK-shaped messages.
#: The default drives the real ``claude_agent_sdk.query`` with the pinned
#: subscription options.
QueryFn = Callable[[str], AsyncIterator[Any]]

#: The FIRST whole-word YES/NO token in the model text decides (case-insensitive;
#: fences/prose around it are tolerated — "YES." / "The answer is NO" both parse).
_YES_NO_RE = re.compile(r"\b(yes|no)\b", re.IGNORECASE)

_SNIPPET = 160  # max chars of model text quoted in a recorded fault

#: The confirm prompt — tight, the line verbatim, an exact-token answer contract
#: so the output is ~zero tokens.
_PROMPT_TEMPLATE = (
    "One spoken line from a live meeting transcript:\n"
    "\n"
    "{line}\n"
    "\n"
    "Is the word 'proxy' here ADDRESSING an AI meeting assistant named Proxy "
    "(a request/question directed AT it), or does it refer to something else "
    "(a proxy server, proxying, etc.)? Answer exactly YES if addressing the "
    "assistant, NO otherwise."
)


class Disambiguator:
    """The real ``Disambiguate`` hook: an async callable + a visible fault gauge.

    Instances satisfy the trigger's ``Disambiguate`` seam
    (``Callable[[str], Awaitable[bool]]``). ``last_error`` reflects the LAST
    call: ``None`` after a clean parse, the recorded fault after a fail-open
    wake — so a broken confirm path shows up on inspection instead of hiding
    behind its own fail-open ``True``.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_DISAMBIGUATOR_MODEL,
        query_fn: QueryFn | None = None,
        confirm_timeout_s: float = CONFIRM_TIMEOUT_S,
    ) -> None:
        self._model = model
        self._query_fn = query_fn
        self._confirm_timeout_s = confirm_timeout_s
        #: The last call's fault (``None`` = clean). Never contains a secret —
        #: only exception text / a short model-output snippet.
        self.last_error: str | None = None

    async def __call__(self, line: str) -> bool:
        """ONE bounded confirm call on ``line`` → addressed (True) or not (False).

        Bounded in WALL CLOCK too: the whole round-trip runs inside
        ``asyncio.timeout(confirm_timeout_s)`` — ``max_turns=1`` bounds turns,
        not time, and a wedged stream must never hold the feed open.

        FAIL-OPEN: any stream fault, a timeout, or a text with no YES/NO token
        returns ``True`` (wake — better to wake and let the agent judge than to
        ignore a human) and records the fault on :attr:`last_error`.
        """
        self.last_error = None
        try:
            async with asyncio.timeout(self._confirm_timeout_s):
                text = await self._collect_text(_PROMPT_TEMPLATE.format(line=line))
        except TimeoutError:
            # The wedged-stream case: the timeout CANCELLED the stream await,
            # which finalized the underlying generator (its cleanup ran — see
            # _collect_text), then converted to TimeoutError here. Fail OPEN.
            self.last_error = (
                f"confirm call timed out after {self._confirm_timeout_s}s (wall clock)"
            )
            return True
        except Exception as exc:  # noqa: BLE001 — every fault fails OPEN, recorded, never raised
            self.last_error = f"{type(exc).__name__}: {exc}"
            return True
        match = _YES_NO_RE.search(text)
        if match is None:
            self.last_error = f"no YES/NO token in confirm output: {text.strip()[:_SNIPPET]!r}"
            return True
        return match.group(1).lower() == "yes"

    # -- SDK plumbing (mirrors the proven subscription_judge._open_stream) --

    def _open_stream(self, prompt: str) -> AsyncIterator[Any]:
        if self._query_fn is not None:
            return self._query_fn(prompt)
        # Real path: subscription CLI auth only — pop the paid key, lazy import,
        # the proven subscription option triad + the one-turn bound.
        os.environ.pop("ANTHROPIC_API_KEY", None)
        from claude_agent_sdk import ClaudeAgentOptions, query

        options = ClaudeAgentOptions(
            model=self._model,
            max_turns=1,  # one confirm turn — no tool round-trips
            permission_mode="bypassPermissions",
            strict_mcp_config=True,
            setting_sources=[],
        )
        return query(prompt=prompt, options=options)

    async def _collect_text(self, prompt: str) -> str:
        """Drive one confirm turn and return its text.

        ``ResultMessage.result`` (the SDK's canonical final text) is preferred;
        the concatenated ``AssistantMessage`` text blocks are the fallback, so
        the two are never duplicated into one verdict. An empty stream returns
        ``""`` — the caller's no-token branch then fails open with the fault
        recorded.

        Teardown discipline (the caller's ``asyncio.timeout`` depends on it):
        the stream is an async generator driven by THIS task, so a timeout
        cancellation lands at the generator's own suspension point and
        finalizes it there — the SDK generator's cleanup (CLI subprocess
        teardown) runs as part of the cancellation itself. The explicit
        ``aclose()`` in the ``finally`` covers the remaining exit — a fault
        raised in this loop's BODY while the generator sits suspended at a
        yield — deterministically, instead of leaving the close to GC; on the
        cancellation path it is a no-op on the already-finalized generator.
        The ``finally`` still runs INSIDE the caller's timeout scope, so even
        the close is wall-clock bounded.
        """
        assistant_parts: list[str] = []
        result_text: str | None = None
        stream = self._open_stream(prompt)
        try:
            async for message in stream:
                content = getattr(message, "content", None)
                if isinstance(content, list):  # AssistantMessage-shaped
                    for block in content:
                        text = getattr(block, "text", None)
                        if isinstance(text, str) and text.strip():
                            assistant_parts.append(text)
                result = getattr(message, "result", None)
                if isinstance(result, str) and result.strip():  # ResultMessage-shaped
                    result_text = result
                # Everything else (SystemMessage, RateLimitEvent, ...) is ignored.
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()
        if result_text is not None:
            return result_text
        return "\n".join(assistant_parts)


def build_disambiguator(
    *,
    model: str = DEFAULT_DISAMBIGUATOR_MODEL,
    query_fn: QueryFn | None = None,
    confirm_timeout_s: float = CONFIRM_TIMEOUT_S,
) -> Disambiguator:
    """Build the real disambiguation hook for the trigger's ``Disambiguate`` seam.

    ``model`` pins the confirm seat (default Haiku — the call is bounded to one
    turn regardless); ``query_fn`` injects a fake SDK stream for offline tests
    (``None`` = the real subscription ``claude_agent_sdk.query`` path);
    ``confirm_timeout_s`` is the wall-clock cap on one whole confirm round-trip
    (default :data:`CONFIRM_TIMEOUT_S` — a timed-out confirm FAILS OPEN, so a
    wedged stream can never hang the feed; tests inject a tiny value). The
    returned :class:`Disambiguator` is the async callable the trigger awaits on
    each mechanical name-hit, with ``last_error`` as its visible fault gauge.
    """
    return Disambiguator(model=model, query_fn=query_fn, confirm_timeout_s=confirm_timeout_s)
