"""The pre-meeting map-build entrypoint → ``index.md`` (PM-MAP-01..05).

**The stored/seeded artifact is the ONE dense understanding = Part 1 + Part 2.**
:func:`build_understanding_map` is the full builder both callers (:mod:`premeeting.pipeline`,
:mod:`premeeting.refresh`) reach for. Part 1 (:func:`build_map` →
:func:`premeeting.symbol_map.build_symbol_map`) is a ranked, GROUNDABLE tree-sitter map carrying
real ``file:line`` (Law 1: grounded or silent), built with NO model call (never credit-blocked,
D-032) and no hallucination. Part 2 (:func:`premeeting.comprehension.build_comprehension`) is a
bounded native-Claude holistic comprehension pass — what the system is, how it works, the key flows
— VERIFIED against the real repo (every ``file:line`` checked; the ungrounded ones dropped). They
are combined (:func:`premeeting.understanding.build_understanding`) into the artifact the meeting
path seeds as the resident understanding (``in_meeting.workroom.compose_resident_prime`` →
``UNDERSTANDING_HEADER``), so what pre-meeting stores in Postgres ``repo_maps`` is what a meeting
cites. When Part 2 can't run (no E2B/token, a fault, an unverified body) the artifact degrades
cleanly to Part 1 alone — still complete + groundable.

**ADOPTION NOTE (resident symbol map — DONE).** :func:`build_map` stores
:func:`premeeting.symbol_map.build_symbol_map` as the ``map_text`` (deterministic, groundable, not
credit-blocked, and exactly what the meeting path seeds + cites). ``provider`` / ``model`` are
retained on the signature as the legacy model seam the existing callers pass; the deterministic
build ignores them (Part 2's native-Claude pass, not this Part-1 layer, is where a model runs).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentkit.provider import Provider

from .exclusions import ExclusionManager
from .symbol_map import build_symbol_map


@dataclass
class MapBuildResult:
    """The map-build outcome — the captured ``index.md`` + provenance for verify/store."""

    index_md: str
    degraded: bool = False
    turns: int = 0
    # The ordered tool NAMES the agent invoked (for the PM-MAP-02/03/04 transcript oracles).
    tool_log: list[str] = field(default_factory=list)


#: The ~10k-token click budget the resident understanding is sized for (see
#: :func:`premeeting.symbol_map.build_symbol_map`): small enough to stay resident in a warm
#: cached session, big enough to carry the ranked, groundable signatures a meeting cites.
SYMBOL_MAP_BUDGET_TOKENS = 11000


async def build_map(
    *,
    provider: Provider,
    clone_path: Path,
    repo_name: str,
    sha: str,
    model: str | None = None,
    exclusions: ExclusionManager | None = None,
) -> MapBuildResult:
    """Build the durable repo map that gets stored + seeded → :class:`MapBuildResult`.

    This is the deterministic Part-1 layer alone: the ranked, real-``file:line`` tree-sitter symbol
    map (:func:`premeeting.symbol_map.build_symbol_map`) — built WITHOUT a model call, so it is not
    credit-blocked (D-032) and never hallucinates a path (Law 1: grounded or silent). It is the
    complete FALLBACK artifact + what the pure-offline path stores.

    The full pre-meeting artifact is Part 1 + Part 2 (the verified holistic comprehension) combined
    by :func:`build_understanding_map`; this function is what that orchestrator (and every store-less
    test) uses for the deterministic base. ``provider`` / ``model`` are retained for signature
    compatibility with the existing callers (the legacy model seam); the deterministic build ignores
    them.
    """
    _ = (provider, model)  # retained for signature only (legacy model seam)
    index_md = build_symbol_map(str(clone_path), budget_tokens=SYMBOL_MAP_BUDGET_TOKENS)
    return MapBuildResult(index_md=index_md, degraded=False, turns=0, tool_log=[])


async def build_understanding_map(
    *,
    clone_path: Path,
    repo_name: str,
    sha: str,
    call: Any | None = None,
    token: str = "",
    model: str | None = None,
    exclusions: ExclusionManager | None = None,
    sandbox_class: Any = None,
    repo_url: str | None = None,
    github_token: str | None = None,
) -> MapBuildResult:
    """Build the FULL pre-meeting artifact: Part 1 (symbol map) + Part 2 (verified comprehension).

    Part 1 is always built (deterministic, groundable). Part 2 — the bounded native-Claude holistic
    comprehension pass, verified against the real repo (:mod:`premeeting.comprehension`) — is run ONLY
    when the E2B ``call`` seam AND a subscription ``token`` are provided (the credit-working path); it
    is then combined with Part 1 into ONE dense document (:func:`premeeting.understanding.build_understanding`).

    ``github_token`` is the freshly-minted GitHub installation token threaded down to the in-sandbox
    ``git clone`` so a PRIVATE customer repo clones successfully (else Part 2 sees an empty repo and
    produces nothing) — the SAME token the Part-1 clone authenticates with.

    Honest degrade (Law 2): no ``call``/``token``, or a comprehension fault / an unverified body, and
    the artifact is the symbol map ALONE — still complete + groundable. Part 2 only ever ADDS verified
    knowledge; it never blocks onboarding. NEVER raises.
    """
    from .symbol_map import build_navigation_map
    from .understanding import build_understanding

    base = await build_map(
        provider=None,  # deterministic build ignores the provider (retained for signature only)
        clone_path=clone_path, repo_name=repo_name, sha=sha, exclusions=exclusions,
    )
    symbol_map = base.index_md  # the full deterministic index — the degrade FALLBACK (Part 1 alone)

    if call is None or not token.strip():
        return base  # deterministic-only path (offline / no subscription) — Part 1 stands

    from .comprehension import build_comprehension

    # The comprehension pass still navigates via a coarse map; give it the COMPACT navigation aid
    # (area map + entry points) rather than the giant ranked-signatures dump — its real work is
    # reading the domain code + docs, not digesting a symbol index.
    navigation = build_navigation_map(str(clone_path))

    comp = await build_comprehension(
        call=call, token=token, clone_path=clone_path, repo_name=repo_name,
        symbol_map=navigation, model=model, sandbox_class=sandbox_class,
        exclusions=exclusions, repo_url=repo_url, github_token=github_token,
    )
    # COMPREHENSION-FIRST composition: the qualitative mental model on top, the COMPACT navigation
    # aid beneath (NOT the ranked-signatures dump). When the comprehension did not land, degrade to
    # the full deterministic symbol map alone (still complete + groundable).
    if comp.ok and comp.text.strip():
        combined = build_understanding(comprehension=comp.text, navigation=navigation)
        return MapBuildResult(index_md=combined or symbol_map, degraded=False, turns=0, tool_log=[])
    return MapBuildResult(index_md=symbol_map, degraded=True, turns=0, tool_log=[])


__all__ = [
    "SYMBOL_MAP_BUDGET_TOKENS",
    "MapBuildResult",
    "build_map",
    "build_understanding_map",
]
