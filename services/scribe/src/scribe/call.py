"""``scribe/call.py`` — the per-window micro-call (the ONE LLM call, exactly).

The Scribe is a **single extraction, not an agent** (§3.2): there is literally no
loop — one ``messages.create``, one forced tool call, one delta out. This module
issues exactly that: a bare ``anthropic.AsyncAnthropic().messages.create`` routed
through the single ``libs.http.call_external`` seam (retry + cost telemetry, §14),
with the cache breakpoints placed by hand via :func:`scribe.prefix.build_scribe_prefix`
and the output tool-forced + schema-validated via :mod:`scribe.parse`.

Two deliberate structural choices, each pinned by a criterion:

* **No Agent SDK loop scaffold** (AC-SCRIBE-01): we do not call ``query()`` /
  ``generateStructured``; there is no loop scheduler on the call stack. The request
  is a plain ``messages.create`` and the forced ``tool_choice`` guarantees one
  emit_notes_delta call.
* **The model seat is read from ``PROXY_MODEL_SCRIBE``** (AC-SCRIBE-08) via the ONE
  canonical seat table (``libs.llm`` ``routing.model_for``), which defaults to
  ``claude-haiku-4-5`` when the env var is unset. No model string is hard-coded here.

The raw Anthropic client is **never held** as module state and its SDK is imported
only lazily inside :func:`_anthropic_client` — so the pure request-construction and
seat-resolution logic is unit-testable without the vendor SDK installed, while the
reality tier still exercises real client construction through the ``call_external``
seam.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

from libs.llm.src.llm.routing import model_for

from .parse import parse_scribe_result
from .prefix import MeetingHeader, build_scribe_prefix, render_window
from .schema import NoteDelta
from .tool import SCRIBE_TOOL_CHOICE, SCRIBE_TOOLS

T = TypeVar("T")

# The Scribe model seat (§3.2 / AC-SCRIBE-08). Resolved from PROXY_MODEL_SCRIBE via
# the ONE seat table; defaults to claude-haiku-4-5. Never hard-code a model here.
_SCRIBE_SEAT = "SCRIBE"

# Per-call output ceiling (§3.2). A physics constant, not a per-call variable.
SCRIBE_MAX_TOKENS: int = 1500

# Telemetry service label handed to the cost seam.
_SERVICE = "anthropic"


def scribe_model() -> str:
    """The current Scribe model id — ``PROXY_MODEL_SCRIBE`` or the seat default."""
    model: str = model_for(_SCRIBE_SEAT)
    return model


class CallExternal(Protocol):
    """Structural type of ``libs.http.call_external`` — the sole external-call seam.

    The concrete funnel returns an ``ExternalCallOutcome`` (value + attempts +
    total_cost_usd); the caller here reads only its ``value`` (duck-typed).
    """

    async def __call__(
        self,
        op: Callable[[], Awaitable[T]],
        *,
        service: str,
        unit_cost_usd: float = 0.0,
    ) -> Any: ...


def build_scribe_request(
    meeting: MeetingHeader,
    rolling_summary: str,
    window: Any,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """Assemble the exact ``messages.create`` params for one window (pure, no I/O).

    The cached region (Segments A + B) goes in ``system`` via ``build_scribe_prefix``;
    the newest window is the ONLY uncached bytes, rendered + fenced into a single
    user message. The tool is forced so output is schema-shaped, never free text.

    ``model`` defaults to the resolved ``PROXY_MODEL_SCRIBE`` seat — passing it
    explicitly is only for tests that want to pin a value without touching env.
    """
    return {
        "model": model if model is not None else scribe_model(),
        "max_tokens": SCRIBE_MAX_TOKENS,
        "system": build_scribe_prefix(meeting, rolling_summary),  # cached region (A + B)
        "messages": [{"role": "user", "content": render_window(window)}],  # uncached tail
        "tools": SCRIBE_TOOLS,
        "tool_choice": SCRIBE_TOOL_CHOICE,  # force schema-shaped output, no free text
    }


def _anthropic_client(**kwargs: Any) -> Any:
    """Construct the raw Anthropic client via the single libs.http construction site.

    Imported lazily so the pure request/seat logic imports cleanly without the
    vendor SDK; the reality tier drives real construction through this path.
    """
    from libs.http.src.http.external import anthropic_client  # deferred: vendor SDK only here

    return anthropic_client(**kwargs)


async def scribe_call(
    meeting: MeetingHeader,
    rolling_summary: str,
    window: Any,
    *,
    call_external: CallExternal,
    client: Any | None = None,
    model: str | None = None,
) -> NoteDelta:
    """Issue EXACTLY ONE ``messages.create`` for one window and return the parsed delta.

    Exactly one round-trip is made (no retry loop, no agent scheduler): the single
    ``client.messages.create`` closure is handed to the injected ``call_external``
    seam (retry + cost telemetry live in the seam, §14), and its response is
    tool-extracted + Pydantic-re-validated by ``parse_scribe_result``. A vendor
    error surfaces through the seam; a truncated/malformed turn surfaces as the
    typed ``ScribeMaxTokensError`` / ``ScribeNoDeltaError`` — never a silent drop
    or a partial delta (AC-SCRIBE-01-NEG / AC-SCRIBE-02-NEG).
    """
    if client is None:
        client = _anthropic_client()
    request = build_scribe_request(meeting, rolling_summary, window, model=model)

    async def _op() -> Any:
        # The ONE call — a bare messages.create, no loop scaffold on the stack.
        return await client.messages.create(**request)

    outcome = await call_external(_op, service=_SERVICE)
    resp = getattr(outcome, "value", outcome)  # seam returns ExternalCallOutcome
    return parse_scribe_result(resp)


__all__ = [
    "SCRIBE_MAX_TOKENS",
    "CallExternal",
    "scribe_model",
    "build_scribe_request",
    "scribe_call",
]
