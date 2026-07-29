"""The subscription deepeval judge — ``DeepEvalBaseLLM`` over the Claude Agent SDK.

deepeval's default judge (``AnthropicModel``) bills the paid API via ``ANTHROPIC_API_KEY``.
This adapter drives ``claude_agent_sdk.query`` on the **Claude Max subscription** (CLI auth,
~$0) instead, so every deepeval metric (G-Eval, ConversationSimulator, the scenario
batteries) scores on the subscription. PROCESS.md step 4; unblocks all ``[judge]`` ACs.

Design points:
- ``ANTHROPIC_API_KEY`` is POPPED on construction and again before each real SDK call —
  subscription CLI auth only, never the paid API. No secrets are read or logged.
- The SDK seam is injectable (``query_fn``): the offline unit suite drives the judge with a
  fake async-generator; the default lazily imports the real ``claude_agent_sdk.query`` with
  the proven subscription options (``max_turns=1``, ``permission_mode="bypassPermissions"``,
  ``strict_mcp_config=True``, ``setting_sources=[]`` — the sdk_smoke pattern). The lazy
  import keeps this module importable in environments without the SDK.
- ``schema`` kwarg (deepeval's ``generate_with_schema`` contract): when a pydantic model
  class is passed, the judge extracts the first JSON object from the model text (markdown
  fences / surrounding prose tolerated) and returns ``schema.model_validate(...)``; when
  ``None``, the raw text is returned.
- Fault policy: a broken judge is VISIBLE, never a silent 0 — any SDK/stream/parse fault
  raises ``SubscriptionJudgeError``. It deliberately never leaks ``TypeError``, which
  deepeval's ``generate_with_schema`` would swallow as "provider rejects the schema kwarg"
  and silently retry schemaless.
- Text collection prefers the final ``ResultMessage.result`` (the SDK's canonical final
  text) and falls back to the concatenated ``AssistantMessage`` ``TextBlock.text`` parts,
  so the two are never duplicated into one verdict.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
from collections.abc import AsyncIterator, Coroutine
from typing import Any, Callable, TypeVar

from deepeval.models.base_model import DeepEvalBaseLLM
from pydantic import BaseModel

#: The pinned judge seat (Sonnet — matches tests/eval/deepeval_config.JUDGE_MODEL).
DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"

#: The injectable SDK seam: (prompt) -> async stream of SDK-shaped messages. The default
#: drives the real ``claude_agent_sdk.query`` with the pinned subscription options.
QueryFn = Callable[[str], AsyncIterator[Any]]

_T = TypeVar("_T")
_BM = TypeVar("_BM", bound=BaseModel)

_SNIPPET = 160  # max chars of model text quoted in an error message


class SubscriptionJudgeError(RuntimeError):
    """A judge-side fault (SDK stream, empty output, or schema parse) — surfaced, never silent."""


def _run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run ``coro`` from a sync caller, safe whether or not a loop is already running.

    deepeval invokes the sync ``generate`` from plain-sync AND from async contexts. When a
    loop is already running in this thread, ``asyncio.run`` would raise — so the coroutine
    runs on a fresh loop in a worker thread instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from model text (fences/prose before or after tolerated)."""
    decoder = json.JSONDecoder()
    candidate = text.strip()
    idx = candidate.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(candidate[idx:])
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            return obj
        idx = candidate.find("{", idx + 1)
    raise SubscriptionJudgeError(f"judge output contains no JSON object: {candidate[:_SNIPPET]!r}")


def _parse_schema(text: str, schema: type[_BM]) -> _BM:
    """Parse the judge's text into an instance of the pydantic ``schema``."""
    data = _extract_json_object(text)
    try:
        return schema.model_validate(data)
    except Exception as exc:
        raise SubscriptionJudgeError(
            f"judge output did not match schema {schema.__name__}: "
            f"{type(exc).__name__} on {text.strip()[:_SNIPPET]!r}"
        ) from exc


# The base's untyped __init_subclass__ (deepeval tracing hook) fires at this class statement.
class SubscriptionJudge(DeepEvalBaseLLM):  # type: ignore[no-untyped-call]
    """A deepeval judge model that scores on the Claude Max subscription (no API key)."""

    def __init__(self, model: str = DEFAULT_JUDGE_MODEL, query_fn: QueryFn | None = None) -> None:
        # Subscription CLI auth only — the paid API key must never be visible to the SDK.
        os.environ.pop("ANTHROPIC_API_KEY", None)
        self._model_id: str = model
        self._query_fn: QueryFn | None = query_fn
        super().__init__(model)

    # -- DeepEvalBaseLLM abstract surface (installed 4.x: load_model / generate / a_generate /
    # -- get_model_name; the base __init__ sets ``self.name`` and calls ``load_model``) --

    def load_model(self, *args: Any, **kwargs: Any) -> "SubscriptionJudge":
        return self  # the SDK needs no load step

    def get_model_name(self, *args: Any, **kwargs: Any) -> str:
        return self._model_id

    # The base declares ``-> str`` but deepeval's schema contract expects the pydantic
    # instance back (its own AnthropicModel returns non-str too); hence ignore[override].
    def generate(  # type: ignore[override]
        self, prompt: str, *args: Any, schema: type[BaseModel] | None = None, **kwargs: Any
    ) -> str | BaseModel:
        return _run_sync(self.a_generate(prompt, *args, schema=schema, **kwargs))

    async def a_generate(  # type: ignore[override]
        self, prompt: str, *args: Any, schema: type[BaseModel] | None = None, **kwargs: Any
    ) -> str | BaseModel:
        text = await self._collect_text(prompt)
        if schema is None:
            return text
        return _parse_schema(text, schema)

    # -- SDK plumbing --

    def _open_stream(self, prompt: str) -> AsyncIterator[Any]:
        if self._query_fn is not None:
            return self._query_fn(prompt)
        # Real path: lazy import + the proven subscription options (sdk_smoke pattern).
        os.environ.pop("ANTHROPIC_API_KEY", None)
        from claude_agent_sdk import ClaudeAgentOptions, query

        options = ClaudeAgentOptions(
            model=self._model_id,
            max_turns=1,  # one read-only judge turn — no tool round-trips
            permission_mode="bypassPermissions",
            strict_mcp_config=True,
            setting_sources=[],
        )
        return query(prompt=prompt, options=options)

    async def _collect_text(self, prompt: str) -> str:
        """Drive one judge turn and return its text (ResultMessage preferred, see module doc)."""
        assistant_parts: list[str] = []
        result_text: str | None = None
        try:
            async for message in self._open_stream(prompt):
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
        except SubscriptionJudgeError:
            raise
        except Exception as exc:
            # Wrap EVERY stream fault (TypeError included — generate_with_schema would
            # swallow a leaked TypeError and silently retry without the schema).
            raise SubscriptionJudgeError(
                f"claude_agent_sdk stream failed for judge model {self._model_id}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if result_text is not None:
            return result_text
        if assistant_parts:
            return "\n".join(assistant_parts)
        raise SubscriptionJudgeError(
            f"judge model {self._model_id} produced no text for the prompt "
            "(no ResultMessage.result, no AssistantMessage text blocks)"
        )


def subscription_judge(model: str = DEFAULT_JUDGE_MODEL, query_fn: QueryFn | None = None) -> SubscriptionJudge:
    """Build the subscription judge (pass ``model=...`` to override the pinned Sonnet seat)."""
    return SubscriptionJudge(model=model, query_fn=query_fn)
