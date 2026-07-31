"""Structured output on a named seat — the ``generateStructured`` surface.

``libs.llm`` previously exposed only :func:`llm.client.call_model`, which returns free
text. Doc 07 §3.1 requires tiering to be *"one structured call on the ``sonnet`` seat"*,
and Doc 06 §3.1 requires the same of the proactive judge on the Haiku seat, so the surface
belongs here — on the module that owns the seat table — rather than being re-derived by
each caller.

**This is not a second model path.** It is the same realization the close pass already
ships (``services/scribe/src/scribe/close.py``): a forced-tool call on the Anthropic
Messages API whose ``input_schema`` IS the ``output_schema``, so the returned ``tool_use``
input is the structured payload — exactly ``outputFormat:{type:'json_schema'}`` semantics.
The close pass grew its own copy before this module existed; this is the general form, and
it is deliberately identical in shape so the two cannot drift in behaviour.

Every call goes through ``libs.http.call_external`` (retry + cost telemetry live in the
seam, Hard Rule: *every external call wrapped with retry + cost telemetry*). The sealed
``vendor:anthropic`` mock_boundary for Doc 07 says a test may mock the HTTP response body
via a vcrpy cassette at that layer but MUST NOT replace the seam, the request construction,
or the client object — so callers pass ``call_external`` in rather than importing a client.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from .routing import max_output_tokens_for, model_for

#: Cost telemetry key for the structured seam.
_SERVICE = "anthropic.structured"


class StructuredOutputError(Exception):
    """The vendor failed, or returned a body that is not the requested structure.

    One error type for "you cannot trust this result". Callers degrade honestly on it;
    nothing downstream is permitted to proceed on a partial or malformed structure.
    """


@dataclass(frozen=True)
class StructuredResult:
    """The validated payload plus what the round-trip actually cost."""

    data: dict[str, Any]
    total_cost_usd: float | None = None


class StructuredCaller(Protocol):
    """One structured round-trip. Injected so the vendor edge stays a recordable seam."""

    async def __call__(
        self, *, model: str, prompt: str, output_schema: dict[str, Any], tool_name: str
    ) -> StructuredResult: ...


class CallExternal(Protocol):
    """``libs.http.call_external`` — passed in, never imported at a call site."""

    async def __call__(
        self, op: Callable[[], Awaitable[Any]], *, service: str, **kwargs: Any
    ) -> Any: ...


# Published Anthropic per-token rates for the Sonnet class, used only to attribute a real
# cost to the call. The authoritative spend ledger stays `meeting_cost`.
_USD_PER_INPUT_TOKEN = 3.0 / 1_000_000
_USD_PER_OUTPUT_TOKEN = 15.0 / 1_000_000


def _lazy_anthropic_client(**kwargs: Any) -> Any:
    from libs.http.src.http.external import anthropic_client  # deferred: vendor SDK only here

    return anthropic_client(**kwargs)


def anthropic_structured_caller(client: Any | None = None) -> StructuredCaller:
    """The concrete structured surface, native on the Anthropic Messages API.

    A forced ``tool_choice`` guarantees the model answers *in the schema* rather than in
    prose that happens to contain JSON. The absence of the expected ``tool_use`` block is
    an error, never an empty result — a caller that treated it as empty would silently
    drop the model's answer.
    """

    async def _call(
        *, model: str, prompt: str, output_schema: dict[str, Any], tool_name: str
    ) -> StructuredResult:
        c = client if client is not None else _lazy_anthropic_client()
        tool = {
            "name": tool_name,
            "description": "Emit the answer as ONE structured object matching the schema.",
            "input_schema": output_schema,
        }
        resp = await c.messages.create(
            model=model,
            max_tokens=max_output_tokens_for(model),
            messages=[{"role": "user", "content": prompt}],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},  # force json_schema output
        )
        block = next(
            (
                b
                for b in resp.content
                if getattr(b, "type", None) == "tool_use"
                and getattr(b, "name", None) == tool_name
            ),
            None,
        )
        if block is None:
            raise StructuredOutputError(
                f"structured call returned no {tool_name!r} tool_use block"
            )
        usage = getattr(resp, "usage", None)
        cost: float | None = None
        if usage is not None:
            cost = (getattr(usage, "input_tokens", 0) or 0) * _USD_PER_INPUT_TOKEN + (
                getattr(usage, "output_tokens", 0) or 0
            ) * _USD_PER_OUTPUT_TOKEN
        return StructuredResult(data=dict(block.input), total_cost_usd=cost)

    return _call


async def generate_structured(
    *,
    seat: str,
    prompt: str,
    output_schema: dict[str, Any],
    caller: StructuredCaller,
    call_external: CallExternal,
    tool_name: str = "emit_result",
) -> StructuredResult:
    """Issue ONE structured call on ``seat`` through the real ``call_external`` seam.

    The model is resolved from the seat table (:func:`llm.routing.model_for`) so a caller
    names a role, never a model id.

    Every vendor fault — 5xx, timeout, transport error, a missing tool_use block — surfaces
    as :class:`StructuredOutputError`. Nothing is returned partially and nothing is
    defaulted: the caller decides how to degrade, and Doc 07's callers degrade by dropping
    a tier rather than by guessing (AC-PME-01-NEG, AC-PME-06-NEG).
    """
    model = model_for(seat)

    async def _op() -> StructuredResult:
        return await caller(
            model=model, prompt=prompt, output_schema=output_schema, tool_name=tool_name
        )

    try:
        outcome = await call_external(_op, service=_SERVICE)
    except StructuredOutputError:
        raise
    except Exception as exc:  # 5xx / timeout / transport — honest surface, no proceed
        raise StructuredOutputError(f"structured call on seat {seat!r} failed: {exc}") from exc

    result = getattr(outcome, "value", outcome)
    if not isinstance(result, StructuredResult):
        raise StructuredOutputError(
            f"structured seam returned {type(result).__name__}, not StructuredResult"
        )
    if not isinstance(result.data, dict):
        raise StructuredOutputError("structured call returned a non-object payload")
    return result
