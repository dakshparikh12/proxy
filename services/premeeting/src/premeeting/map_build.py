"""The bounded ONE-agent map-build loop → ``index.md`` (PM-MAP-01..05).

The map is NAVIGATION, whose raw material is a bounded-depth directory skeleton + a dozen
high-yield files — which one bounded Claude agent handles at any realistic repo size. This
module owns the PLUMBING around that agent: the prompt assembly, the read-only "quick"
disposition (tool restriction via the isolation triad), the budget backstop, the bounded-read
discipline, and the terminal-text capture. It drives the ONE model seam
(``agentkit.Provider.stream`` via a :class:`~agentkit.ProviderQuery`) — never a bespoke loop —
so the fake-provider test path is identical to the proven wake / Workroom path.

**Context discipline (this is what keeps one agent safe — grounded, PM-MAP-02):** the prompt
carries a bounded-depth (``≤ MAX_SKELETON_DEPTH``) directory skeleton + a SINGLE batched read
of the high-yield files (README / manifests / CI / CONTRIBUTING). The FULL ``git ls-files`` dump
is NEVER fed into context — the agent's window grows only with what IT chooses to open, so the
overview holds regardless of repo size.

**The model seam is out of credits (D-032).** Everything here is exercised on the REAL path
with a FAKE provider returning a canned ``index.md`` + a recorded tool transcript. The
real-model map-QUALITY battery (PM-MAP-06) is BLOCKED-on-credits — never marked green here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentkit import ProviderQuery
from agentkit.provider import Provider

from .exclusions import ExclusionManager

# The map-build model id, resolved dynamically (Law 4: never a per-task model literal buried in
# code). It threads into ``ProviderQuery.model`` → ``ClaudeAgentOptions.model`` → the
# ``claude_agent_sdk`` subprocess, which validates the id against the SAME model catalog the
# native ``claude`` CLI uses. A NON-CATALOG id (the old default ``"claude-sonnet"``) is rejected
# by the SDK with "the selected model … may not exist", captured as a one-line error string, and
# then correctly rejected by ``verify`` — so EVERY connect ended ``not_ready`` with an empty map
# and no ``repos`` bind, 404-ing every ``POST /meetings``. The resolver reads the deployment's
# configured seat (``PROXY_MODEL_MAP`` first, then the shared ``PROXY_MODEL_ANSWER`` the .env
# already sets to ``claude-sonnet-4-6``) and falls back to a VALID catalog id — never the bare
# family alias, which is not a Claude Code model id.
_DEFAULT_MAP_MODEL = "claude-sonnet-4-6"


def _default_map_model() -> str:
    """The map-build model id from config, or a valid catalog fallback (never a bare alias)."""
    return (
        os.environ.get("PROXY_MODEL_MAP")
        or os.environ.get("PROXY_MODEL_ANSWER")
        or _DEFAULT_MAP_MODEL
    ).strip() or _DEFAULT_MAP_MODEL

# The bounded skeleton depth (PM-MAP-02): a ``tree -L 3``-style listing — deep enough to name
# the major areas, shallow enough that even a 100k-file monorepo yields tens-of-k tokens, never
# the ~10-tokens/file full-list blowup. A physics floor (Law 4).
MAX_SKELETON_DEPTH = 3
# Never feed a single tool/context block larger than this many pure path lines (PM-MAP-02): the
# full-list ingestion guard. The skeleton is pruned to stay under it on a monster repo.
MAX_SKELETON_LINES = 2000

# The six required index.md sections (DESIGN.md structure). The agent is instructed to emit all
# six; ``verify`` re-checks them deterministically.
REQUIRED_SECTIONS: tuple[str, ...] = (
    "What this is",
    "Where things live",
    "Entry points",
    "Key models",
    "Conventions",
    "Notes",
)

# The high-yield files a fresh reader opens first — read in ONE batch (PM-MAP-03).
HIGH_YIELD_FILES: tuple[str, ...] = (
    "README.md",
    "README.rst",
    "README",
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "requirements.txt",
    "setup.py",
    "Makefile",
    "CONTRIBUTING.md",
    ".github/workflows",  # CI dir — sampled, not fully read
    "docker-compose.yml",
    "Dockerfile",
)

# The map-build "quick" read-only disposition (PM-MAP-04): only read/grep/glob/batch_read are
# advertised; every mutation tool is blocked. These are the ``code_intel`` live-search tool names
# the toolbelt server mounts, so the model can open more on demand within the read-only triad.
MAP_BUILD_READ_TOOLS: tuple[str, ...] = (
    "mcp__code_intel__read",
    "mcp__code_intel__batch_read",
    "mcp__code_intel__grep",
    "mcp__code_intel__glob",
)
# Every mutating tool the read-only disposition must be blocked from reaching (PM-MAP-04). The
# isolation triad's ``allowed_tools`` does not filter MCP tools, so the block rides disallowed.
MAP_BUILD_BLOCKED_TOOLS: tuple[str, ...] = (
    "mcp__code__write_file",
    "mcp__code__edit_file",
    "mcp__code__run_command",
    "mcp__code__ast_grep",
    "Write",
    "Edit",
    "Bash",
)

# Budget backstop (PM-MAP-05): a bounded max_turns + an output-token clamp so a monster repo
# stops instead of hanging or emitting a truncated fragment.
DEFAULT_MAX_TURNS = 12
DEFAULT_MAX_OUTPUT_TOKENS = 8000
_DEGRADE_NOTE = (
    "> This map degraded to a top-level overview under the map-build budget on a very large "
    "repository; use live search (grep/read) for depth within each area."
)


@dataclass
class MapBuildResult:
    """The map-build outcome — the captured ``index.md`` + provenance for verify/store."""

    index_md: str
    degraded: bool = False
    turns: int = 0
    # The ordered tool NAMES the agent invoked (for the PM-MAP-02/03/04 transcript oracles).
    tool_log: list[str] = field(default_factory=list)


def build_skeleton(clone_path: Path, *, max_depth: int = MAX_SKELETON_DEPTH,
                   exclusions: ExclusionManager | None = None) -> str:
    """A bounded-depth ``tree -L``-style skeleton of the checkout (NOT the full file list).

    Walks directories to ``max_depth`` and lists their immediate entries, skipping ``.git`` and
    any excluded/secret path. Pruned to ``MAX_SKELETON_LINES`` so it can never become the
    full-list blowup PM-MAP-02 forbids — beyond the cap it emits a "(truncated — use live
    search)" marker."""
    lines: list[str] = []
    root = clone_path.resolve()

    def _walk(d: Path, depth: int) -> None:
        if depth > max_depth or len(lines) >= MAX_SKELETON_LINES:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            return
        for p in entries:
            if p.name == ".git":
                continue
            rel = str(p.relative_to(root))
            if exclusions is not None and p.is_file() and exclusions.is_excluded(rel):
                continue
            if len(lines) >= MAX_SKELETON_LINES:
                lines.append("  … (skeleton truncated — use live search for depth)")
                return
            indent = "  " * (depth - 1)
            lines.append(f"{indent}{p.name}{'/' if p.is_dir() else ''}")
            if p.is_dir():
                _walk(p, depth + 1)

    _walk(root, 1)
    return "\n".join(lines)


def collect_high_yield(clone_path: Path, *, exclusions: ExclusionManager | None = None) -> list[str]:
    """The subset of :data:`HIGH_YIELD_FILES` that actually exist in the clone (relative paths).

    These are read in ONE batch by the agent's first tool call (PM-MAP-03). An excluded/secret
    path is never offered."""
    present: list[str] = []
    root = clone_path.resolve()
    for name in HIGH_YIELD_FILES:
        p = root / name
        if not p.exists():
            continue
        rel = str(p.relative_to(root))
        if exclusions is not None and p.is_file() and exclusions.is_excluded(rel):
            continue
        present.append(rel)
    return present


def _build_prompt(*, repo_name: str, sha: str, skeleton: str, high_yield: list[str]) -> str:
    """Assemble the map-build prompt: the mission + the bounded skeleton + the batch-read plan.

    The prompt names the six required sections and the bounded-read discipline explicitly, and
    hands the agent the skeleton + the high-yield list (to be read in ONE ``batch_read``) — it
    NEVER embeds the full file list."""
    sections = "\n".join(f"## {s}" for s in REQUIRED_SECTIONS)
    hy = ", ".join(high_yield) if high_yield else "(none found — infer from the skeleton)"
    return (
        f"Build a dense, bounded repo map (index.md) for `{repo_name}` @ {sha}.\n\n"
        "The map orients an AI teammate at meeting speed: it gives the mental model + where to "
        "look; exact locations come from live search later. Emit EXACTLY these sections, in "
        f"order, as markdown headers:\n{sections}\n\n"
        "Discipline: read the high-yield files in ONE batch first, then open only what you need "
        "on demand. NEVER ask for the full file list — the directory skeleton below is your map "
        "of the territory. On a very large repo, keep it a COMPLETE navigation map (every major "
        "area, one line each) and leave depth to live search.\n\n"
        f"High-yield files to batch-read first: {hy}\n\n"
        f"Directory skeleton (bounded depth):\n```\n{skeleton}\n```\n\n"
        "Return ONLY the finished index.md as your final message."
    )


def _capture_terminal_text(chunks: list[Any]) -> str:
    """The agent's TERMINAL text is the map: the last non-empty ``TEXT``/``RESULT`` body.

    Prefers a ``RESULT``'s ``structured_output`` if present, else the last ``TEXT`` chunk's
    body — matching how the wake/Workroom driver folds a terminal artifact."""
    text = ""
    for ch in chunks:
        ctype = getattr(ch, "type", "")
        if ctype == "TEXT" and getattr(ch, "text", None):
            text = str(ch.text)
        elif ctype == "RESULT":
            structured = (getattr(ch, "metadata", {}) or {}).get("structured_output")
            if isinstance(structured, str) and structured.strip():
                text = structured
    return text


def _record_tool_log(chunks: list[Any]) -> list[str]:
    """The ordered tool NAMES the agent invoked (from ``TOOL_USE`` chunks) — the transcript."""
    log: list[str] = []
    for ch in chunks:
        if getattr(ch, "type", "") == "TOOL_USE":
            name = (getattr(ch, "metadata", {}) or {}).get("name")
            if name:
                log.append(str(name))
    return log


#: Substrings marking a model response that FAILED to build a map — an API/SDK error captured AS
#: text, not a real map: a content-filter block ("Output blocked by content filtering policy"), a
#: bad model id ("… may not exist"), a rate-limit/overload/quota. The SDK surfaces these as a
#: one-line body; returning that AS the map fails verify with a MISLEADING "missing sections" and
#: hard-blocks onboarding. Found by the repo-diversity sim: gin (Go) tripped the output filter.
_BUILD_FAILURE_MARKERS = (
    "api error", "output blocked", "content filtering", "content policy",
    "may not exist", "rate limit", "overloaded", "quota exceeded", "credit balance",
)


def _is_failed_build(index_md: str) -> bool:
    """True iff the captured body is NOT a real map: empty, a known API/SDK error string, or too
    short to be a map AND missing every required section. Such a body must degrade to the skeleton
    map, never be returned AS the map (which fails verify with a misleading reason)."""
    text = (index_md or "").strip()
    if not text:
        return True
    low = text.lower()
    if any(m in low for m in _BUILD_FAILURE_MARKERS):
        return True
    if len(text) < 400 and not any(f"## {s}" in index_md for s in REQUIRED_SECTIONS):
        return True
    return False


async def build_map(
    *,
    provider: Provider,
    clone_path: Path,
    repo_name: str,
    sha: str,
    model: str | None = None,
    exclusions: ExclusionManager | None = None,
    mcp_servers: object | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> MapBuildResult:
    """Run the bounded one-agent map-build → :class:`MapBuildResult` (the captured index.md).

    Drives the ONE model seam (``provider.stream(prompt, ProviderQuery)``) with the read-only
    "quick" disposition (read/grep/glob/batch_read only; every mutation blocked, PM-MAP-04), a
    bounded ``max_turns`` + output clamp (PM-MAP-05), and a prompt carrying the bounded skeleton
    + the high-yield batch plan (never the full file list, PM-MAP-02/03). Captures the agent's
    terminal text as the map. On budget exhaustion (``RESULT`` reached ``max_turns`` / an empty
    body) it degrades to a top-level map + a "depth via live search" note — never a hang, never a
    truncated fragment.

    Fake ONLY at ``provider`` (the model seam, D-032). PM-MAP-06 (real-model quality) is BLOCKED.
    """
    resolved_model = (model or "").strip() or _default_map_model()
    skeleton = build_skeleton(clone_path, exclusions=exclusions)
    high_yield = collect_high_yield(clone_path, exclusions=exclusions)
    prompt = _build_prompt(repo_name=repo_name, sha=sha, skeleton=skeleton, high_yield=high_yield)

    query = ProviderQuery(
        model=resolved_model,
        allowed_tools=MAP_BUILD_READ_TOOLS,
        system_prompt=(
            "You are Proxy, building a navigation map of a company's codebase. Read only what "
            "you need; never write, edit, or run anything; cite where things live from the "
            "clone. Produce a complete, bounded map — never a truncated fragment."
        ),
        max_turns=max_turns,
        disallowed_tools=MAP_BUILD_BLOCKED_TOOLS,
        thinking_enabled=False,
        mcp_servers=mcp_servers,
        env={"MAX_OUTPUT_TOKENS": str(max_output_tokens)},
    )

    # Run the bounded build; RETRY a few times if the model returns an error-shaped / non-map body.
    # An API/content-filter block is often PROBABILISTIC (the model's OUTPUT is filtered, which varies
    # run to run), so a retry frequently succeeds; a persistent failure degrades to the skeleton map
    # (below) rather than returning the error string AS the map. Attempts bounded (each re-explores).
    attempts = max(1, int(os.environ.get("PROXY_MAP_BUILD_ATTEMPTS", "3") or "3"))
    tool_log: list[Any] = []
    index_md = ""
    turns = 0
    hit_cap = False
    for _attempt in range(attempts):
        chunks = [chunk async for chunk in provider.stream(prompt, query)]
        tool_log = _record_tool_log(chunks)
        index_md = _capture_terminal_text(chunks)
        turns = 0
        hit_cap = False
        for ch in chunks:
            if getattr(ch, "type", "") == "RESULT":
                meta = getattr(ch, "metadata", {}) or {}
                turns = int(meta.get("num_turns", 0) or 0)
                hit_cap = turns >= max_turns
        if not _is_failed_build(index_md):
            break  # got a real map — stop retrying

    # Degradation backstop (PM-MAP-05): a budget cap, an empty body, OR an error-shaped/non-map
    # response (content-filter / API error / bad model id) → emit a COMPLETE top-level navigation
    # map from the skeleton + the depth-via-live-search note. Never a hang, a truncated fragment, or
    # an error string returned AS the map (which fails verify with a misleading "missing sections").
    degraded = False
    if _is_failed_build(index_md) or hit_cap:
        degraded = True
        index_md = _degraded_map(repo_name=repo_name, sha=sha, skeleton=skeleton)

    return MapBuildResult(index_md=index_md, degraded=degraded, turns=turns, tool_log=tool_log)


def _degraded_map(*, repo_name: str, sha: str, skeleton: str) -> str:
    """A COMPLETE top-level navigation map from the skeleton + the depth-via-live-search note.

    The graceful-degrade artifact (PM-MAP-05): every top-level area named (one line each) so the
    map is complete, with depth deferred to live search — never a crash, never a fragment."""
    top = [ln for ln in skeleton.splitlines() if ln and not ln.startswith("  ")]
    where = "\n".join(f"- {ln}" for ln in top) or "- (empty repository)"
    return (
        f"# Repo Map — {repo_name} @ {sha}\n\n"
        "## What this is\nA repository mapped at top-level under the map-build budget.\n\n"
        f"## Where things live\n{where}\n\n"
        "## Entry points\nUse live search (grep) to locate servers/routes/CLI entry points.\n\n"
        "## Key models / domain\nUse live search to locate core types/tables.\n\n"
        "## Conventions\nUse live search to read the build/test/lint config.\n\n"
        f"## Notes\n{_DEGRADE_NOTE}\n"
    )


__all__ = [
    "MAP_BUILD_BLOCKED_TOOLS",
    "MAP_BUILD_READ_TOOLS",
    "MAX_SKELETON_DEPTH",
    "REQUIRED_SECTIONS",
    "MapBuildResult",
    "build_map",
    "build_skeleton",
    "collect_high_yield",
]
