"""BehaviorRunner — the ONE generic runner for every wake-behavior and Workroom
disposition (§3.4), and the sole site where ``stream_deltas`` is applied
(AC-CMP-005).

Reads a declarative :class:`~agentkit.config.Behavior` / :class:`BehaviorConfig`,
resolves the declared inputs, mounts EXACTLY the declared curated tool subset
(``allowed_tools = config.tools`` — §10.5, never the union), computes the
SDK-isolation triad params, streams through the provider seam, applies
``stream_deltas`` exactly once (the delta-izer is applied here and nowhere else;
downstream consumers read the delta stream and MUST NOT re-wrap it — C2), feeds
the cost meter as a consumer of that same typed stream (``RESULT.metadata
["total_cost_usd"]`` → §3.13), and surfaces the pass-through ``ERROR`` chunk as a
:class:`ProviderError` at the runner boundary (where §3.5 recovery catches it).

There is NO per-behavior code branch: selecting a behavior by name IS the branch,
and the model makes that selection (D-023).
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, cast

from libs.contracts import AgentChunk

from .config import Behavior, BehaviorConfig
from .deltas import stream_deltas
from .provider import (
    Provider,
    ProviderError,
    ProviderQuery,
    compute_builtin_tools,
    pick_provider,
    thinking_policy,
)


class _NullCostMeter:
    """A no-op cost meter used when the runner is constructed without one.

    A real meter reads ``RESULT.metadata["total_cost_usd"]`` off the delta stream
    and feeds the §3.13 budget gate; the null meter keeps the runner usable in
    contexts that don't meter (and keeps ``observe`` a pure sink).
    """

    def observe(self, chunk: AgentChunk) -> None:  # noqa: D401 - sink
        return None


def render_role(behavior: Behavior | BehaviorConfig, inputs: Mapping[str, Any]) -> str:
    """Render the behavior's role + rules into the turn's system prompt.

    Rules are framed as judgment primers (NOT a checklist that bounds the model),
    consistent with §3.4's anti-hardcoding discipline. Resolved input keys are
    listed so the model knows what context it was handed, but their *values* are
    supplied as DATA on the prompt, never as instructions.
    """
    role = getattr(behavior, "role", "") or getattr(getattr(behavior, "config", None), "role", "")
    rules = tuple(getattr(behavior, "rules", ()) or ())
    parts: list[str] = []
    if role:
        parts.append(role)
    if rules:
        parts.append(
            "Examples to prime your judgment (NOT a checklist that bounds it):\n"
            + "\n".join(f"- {r}" for r in rules)
        )
    return "\n\n".join(parts)


def with_proxy_guardrails(system_prompt: str) -> str:
    """Append the standing spoken-register + one-gather-pass guardrail (§3.4)."""
    suffix = (
        "Prefer the compact artifact, cheapest tool first, one gather pass. "
        "Speak short sentences, use contractions, no enumeration, two sentences max."
    )
    return f"{system_prompt}\n\n{suffix}" if system_prompt else suffix


def render_prompt(behavior: Behavior | BehaviorConfig, inputs: Mapping[str, Any]) -> str:
    """Render the turn's user prompt: the resolved inputs handed to the model as DATA."""
    preamble = inputs.get("preamble")
    lines: list[str] = []
    if preamble:
        lines.append(str(preamble))
    for key in getattr(behavior, "inputs", ()) or ():
        if key in inputs and inputs[key] is not None:
            lines.append(f"{key}: {inputs[key]}")
    return "\n".join(lines)


class BehaviorRunner:
    """ONE runner for every wake-behavior and Workroom disposition (§3.4).

    Two construction shapes are supported:

      * ``BehaviorRunner(registry=..., provider=..., cost_meter=...)`` — the §3.4
        shape: :meth:`run` is called by *behavior name* and looks the behavior up
        in the registry; the model selecting a name IS the branch.
      * ``BehaviorRunner(config)`` — a single-behavior convenience; :meth:`run` may
        be called with just resolved inputs.

    The provider defaults to the seam registry (:func:`pick_provider` by
    ``config.model``); an explicit ``provider`` overrides it (test injection).
    """

    def __init__(
        self,
        config: BehaviorConfig | None = None,
        *,
        registry: Mapping[str, Behavior | BehaviorConfig] | None = None,
        provider: Provider | None = None,
        cost_meter: Any | None = None,
    ) -> None:
        self._config = config
        self._registry: dict[str, Behavior | BehaviorConfig] = dict(registry or {})
        self._provider = provider
        self._cost = cost_meter if cost_meter is not None else _NullCostMeter()

    @property
    def config(self) -> BehaviorConfig | None:
        return self._config

    def _resolve(
        self, behavior_or_name: Behavior | BehaviorConfig | str | None
    ) -> tuple[Behavior | BehaviorConfig, BehaviorConfig]:
        """Resolve the (behavior, config) pair from a name, a Behavior, or a config."""
        target: Behavior | BehaviorConfig | None
        if behavior_or_name is None:
            target = self._config
        elif isinstance(behavior_or_name, str):
            target = self._registry.get(behavior_or_name)
            if target is None:
                raise KeyError(f"no behavior registered under name {behavior_or_name!r}")
        else:
            target = behavior_or_name
        if target is None:
            raise ValueError("no behavior to run (pass a name, a Behavior, or construct with a config)")
        # Duck-type, don't identity-check: a Behavior is "a thing carrying a typed
        # ``.config``". Under the src-layout workspace, ``agentkit.Behavior`` (product
        # code: ``from agentkit import Behavior``) and ``libs.agentkit.Behavior`` (the
        # facade the tests import) are DISTINCT class objects, so ``isinstance(target,
        # Behavior)`` is False across that boundary and would mis-treat a Behavior as a
        # BehaviorConfig — and ``isinstance(inner, BehaviorConfig)`` fails the same
        # way. A Behavior carries a ``.config`` that exposes ``.mounted_tools``; a
        # BehaviorConfig exposes ``.mounted_tools`` itself. Resolving on that shape is
        # boundary-proof and keeps selecting-by-name the only branch (D-023).
        inner = getattr(target, "config", None)
        config = inner if hasattr(inner, "mounted_tools") else target
        # ``config`` is structurally a BehaviorConfig (it exposes ``.mounted_tools`` /
        # ``.model`` / ``.max_turns``); the cast keeps the annotation honest across the
        # workspace's twin-module boundary where nominal ``isinstance`` cannot narrow.
        return target, cast(BehaviorConfig, config)

    def build_query(
        self,
        behavior_or_name: Behavior | BehaviorConfig | str | None = None,
        resolved_inputs: Mapping[str, Any] | None = None,
        *,
        abort: Any = None,
    ) -> ProviderQuery:
        """Compute the :class:`ProviderQuery` for a turn — the isolation triad, the
        curated tool mount, and the targeted thinking budget — WITHOUT streaming.

        Exposed so the params are unit-testable directly (curated subset, triad,
        thinking policy) without a live provider.
        """
        behavior, config = self._resolve(behavior_or_name)
        inputs = dict(resolved_inputs or {})
        curated = tuple(config.mounted_tools)  # allowed_tools = config.tools (§10.5), never the union
        role_name = getattr(behavior, "role", "") or config.role
        thinking_on, budget = thinking_policy(config.model, role_name)
        system_prompt = with_proxy_guardrails(render_role(behavior, inputs))
        return ProviderQuery(
            model=config.model,
            allowed_tools=curated,
            system_prompt=system_prompt,
            max_turns=config.max_turns,
            tools=compute_builtin_tools(curated),   # [] in sandbox mode
            strict_mcp_config=True,                  # isolation triad
            setting_sources=(),                      # isolation triad ([])
            thinking_enabled=thinking_on,
            thinking_budget_tokens=budget,
            resume=inputs.get("resume"),
            preamble=inputs.get("preamble"),
            abort=abort,
        )

    async def run(
        self,
        behavior_or_name: Behavior | BehaviorConfig | str | None = None,
        resolved_inputs: Mapping[str, Any] | None = None,
        abort: Any = None,
    ) -> AsyncIterator[AgentChunk]:
        """Run one turn: mount the curated subset, compute the isolation params,
        stream through the provider seam, apply ``stream_deltas`` ONCE, meter the
        RESULT cost off that same delta stream, and raise :class:`ProviderError` on
        a pass-through ``ERROR`` chunk (where §3.5 recovery catches it).
        """
        behavior, config = self._resolve(behavior_or_name)
        inputs = dict(resolved_inputs or {})
        query = self.build_query(behavior_or_name, inputs, abort=abort)
        provider = self._provider if self._provider is not None else pick_provider(config.model)

        raw = provider.stream(render_prompt(behavior, inputs), query)
        # The one and only application of the delta-izer in the whole tree (AC-CMP-005).
        # The cost meter is a CONSUMER of this same typed stream — it never lives
        # inside the delta computer, and no second stream_deltas wraps this.
        async for chunk in stream_deltas(raw):
            self._cost.observe(chunk)          # RESULT.metadata["total_cost_usd"] → §3.13 gate
            if chunk.type == "ERROR":          # surface the pass-through ERROR as the exception
                raise ProviderError(chunk)     # §3.5 stale-session / retry recovery catches this
            yield chunk
