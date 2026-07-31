"""The in-meeting grounding: (clone + loaded map) → the live-search toolbelt (PM-DOWN-01/02/03).

Every downstream consumer (the wake turn, the Workroom code-task agent) needs two things from
the pre-meeting system: the MAP (orientation, mounted as a cached prompt prefix) and the LIVE
toolbelt (grep/read/glob/batch_read over the clone, for grounded citations). This module resolves
both from the durable substrate — the tenant's clone on disk + the ``index.md`` loaded from
Postgres — REPLACING ``graph.db`` as the tool-serving substrate for these live-search tools.

:class:`RepoContext` carries ``map_text`` (the prompt prefix) and ``clone_path`` (the tool
substrate). ``build_server(*, lsp=None) -> McpSdkServerConfig | None`` mounts the toolbelt under
the ``code_intel`` server name so existing ``mcp__code_intel__{grep,read,batch_read,glob}``
``allowed_tools`` resolve to real handlers. Every handler is grounded-or-abstains (Law 1/2) and
NEVER throws (Hard Rule 6 — a fault is an ``is_error`` result). Fail-closed (PM-DOWN-03): with no
clone AND no map, ``build_server`` returns ``None`` — the consumer mounts nothing and Proxy stays
functional (it wakes; it just has no codebase tools this meeting), never a crash.

Isolation triad (Hard Rule 4): a context is built PER MEETING from THAT tenant's clone, so one
meeting's toolbelt can never read another tenant's volume — the ``clone_path`` is tenant-rooted
(:func:`premeeting.paths.tenant_repo_dir`).
"""
from __future__ import annotations

import json
import subprocess  # noqa: S404 - ripgrep only, argv list, no shell
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

from .exclusions import ExclusionManager

# The server name the wake/Workroom ``allowed_tools`` already resolve against
# (``mcp__code_intel__*``). Kept identical so mounting the premeeting toolbelt needs no behavior
# edit — the toolbelt is reoriented to the clone, the advertised names are unchanged.
SERVER_NAME = "code_intel"

# The live-search tool basenames premeeting serves off the CLONE (the reoriented substrate). A
# curated subset — grep/read/batch_read/glob — never the graph-derived set (those stay on the
# graph toolbelt until the graph is retired).
TOOL_BASENAMES: tuple[str, ...] = ("grep", "read", "batch_read", "glob")

_MAX_BATCH_FILES = 20
_MAX_GREP_HITS = 200


def _text_result(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def _error_result(msg: str) -> dict[str, Any]:
    """The never-throw boundary (Hard Rule 6): a tool fault returns an ``is_error`` result."""
    return {"is_error": True, "content": [{"type": "text", "text": msg}]}


@dataclass(frozen=True)
class RepoContext:
    """A meeting's resolved grounding — the durable map (prefix) + the clone (tool substrate).

    ``map_text`` is the ``index.md`` loaded from Postgres (the orientation prefix); ``clone_path``
    is the tenant's checkout (the live-search substrate); ``tenant_id`` scopes it (isolation).
    Resolved by the db-backed consumers from the meeting's tenant + repo (the SAME identity the
    old ``CodeIntelContext`` used), so wiring is a substrate swap, not a signature change."""

    clone_path: Path
    map_text: str | None = None
    tenant_id: str = ""

    def has_map(self) -> bool:
        return bool(self.map_text and self.map_text.strip())

    def build_server(self, *, lsp: Any = None) -> McpSdkServerConfig | None:
        """Build this context's live-search SDK server, or ``None`` when nothing is grounded.

        Fail-closed (PM-DOWN-03 / Rule 6): returns ``None`` when the clone does not exist AND
        there is no map — the consumer mounts nothing and Proxy degrades honestly. ``lsp`` is
        accepted (keyword-optional) to match the old ``CodeIntelContext.build_server`` signature
        the wake/Workroom sites call; the clone-backed toolbelt does not need it."""
        _ = lsp
        clone = Path(self.clone_path)
        if not clone.exists() and not self.has_map():
            return None
        if not clone.exists():
            # A map with no clone: still nothing to serve tools over. Fail-closed to None; the
            # map still rides as a prompt prefix via ``map_text`` (the consumer mounts that).
            return None
        try:
            return build_repo_context_server(clone, exclusions=_scanned_exclusions(clone))
        except Exception:  # noqa: BLE001 - a build fault degrades to no-mount, never crashes
            return None


def _scanned_exclusions(clone: Path) -> ExclusionManager:
    """A fresh ExclusionManager scanned over the clone so the toolbelt strips secrets on read."""
    em = ExclusionManager()
    if clone.exists():
        em.scan_after_clone(clone)
    return em


def build_repo_context_server(
    clone_path: Path, *, exclusions: ExclusionManager | None = None
) -> McpSdkServerConfig:
    """Build the in-process live-search SDK server over a tenant's clone (grep/read/batch_read/glob).

    The SAME ``create_sdk_mcp_server`` recipe the graph toolbelt uses; the difference is only that
    these tools read the CLONE directly (ripgrep + bounded file reads) rather than a graph.db.
    Every handler is grounded (real file:line) or abstains, redacts secrets, confines reads to the
    clone (no ``..`` escape), and NEVER throws."""
    clone = Path(clone_path)
    em = exclusions if exclusions is not None else _scanned_exclusions(clone)

    def _read_body(path_str: str, max_lines: int | None) -> dict[str, Any]:
        if not isinstance(path_str, str):
            return {"path": str(path_str), "content": None, "error": "invalid path"}
        candidate = Path(path_str) if Path(path_str).is_absolute() else clone / path_str
        resolved = candidate.resolve()
        root = clone.resolve()
        if resolved != root and root not in resolved.parents:
            return {"path": path_str, "content": None, "error": "path outside tenant volume"}
        rel = str(resolved.relative_to(root))
        if em.is_excluded(rel):
            return {"path": path_str, "content": None, "error": "excluded path"}
        if not resolved.is_file():
            return {"path": path_str, "content": None, "error": "not found"}
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"path": path_str, "content": None, "error": str(exc)}
        if max_lines is not None:
            text = "\n".join(text.splitlines()[:max_lines])
        return {"path": path_str, "content": em.redact(text), "error": None}

    def _batch(paths: Any) -> dict[str, Any]:
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list):
            paths = []
        files = [_read_body(str(p), None) for p in paths[:_MAX_BATCH_FILES]]
        return {"files": files, "truncated": len(paths) > _MAX_BATCH_FILES}

    _read_desc = "Read one or more files from the repo at the current clone (returns content)."
    _batch_desc = "Read a batch of files from the repo (each file's content or a per-file error)."
    _grep_desc = "Ripgrep the repo for a word/symbol; returns file:line reference sites over the clone."

    @tool("read", _read_desc, {"paths": list})
    async def read(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _text_result(_batch(args.get("paths") or args.get("path") or []))
        except Exception as exc:  # noqa: BLE001 - never-throw boundary
            return _error_result(f"read error: {exc}")

    @tool("batch_read", _batch_desc, {"paths": list})
    async def batch_read(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _text_result(_batch(args.get("paths") or args.get("path") or []))
        except Exception as exc:  # noqa: BLE001 - never-throw boundary
            return _error_result(f"batch_read error: {exc}")

    @tool("grep", _grep_desc, {"query": str})
    async def grep(args: dict[str, Any]) -> dict[str, Any]:
        try:
            query = str(args.get("query") or args.get("symbol") or "")
            return _text_result({"query": query, "references": _grep_clone(clone, query, em)})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary
            return _error_result(f"grep error: {exc}")

    @tool("glob", "List repo files matching a glob pattern (over the current clone).", {"pattern": str})
    async def glob_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            pattern = str(args.get("pattern") or "*")
            root = clone.resolve()
            hits = []
            for p in sorted(root.rglob(pattern)):
                if not p.is_file() or ".git" in p.parts:
                    continue
                rel = str(p.relative_to(root))
                if em.is_excluded(rel):
                    continue
                hits.append(rel)
                if len(hits) >= _MAX_GREP_HITS:
                    break
            return _text_result({"pattern": pattern, "files": hits})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary
            return _error_result(f"glob error: {exc}")

    handlers = {"read": read, "batch_read": batch_read, "grep": grep, "glob": glob_tool}
    return create_sdk_mcp_server(
        name=SERVER_NAME, version="1.0.0", tools=[handlers[n] for n in TOOL_BASENAMES]
    )


def _grep_clone(clone: Path, query: str, em: ExclusionManager) -> list[dict[str, Any]]:
    """Ripgrep the clone for ``query`` → redacted ``file:line`` hits, excluded paths dropped."""
    if not query.strip() or not clone.exists():
        return []
    proc = subprocess.run(  # noqa: S603,S607 - fixed rg binary, argv list, no shell
        ["rg", "-n", "--no-heading", "-w", query, "."],
        cwd=str(clone), capture_output=True, text=True, check=False,
    )
    hits: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        rel, lineno, ctx = parts
        if rel.startswith("./"):
            rel = rel[2:]
        if em.is_excluded(rel):
            continue
        try:
            hits.append({"file": rel, "line": int(lineno), "context": (em.redact(ctx) or "").strip()})
        except ValueError:
            continue
        if len(hits) >= _MAX_GREP_HITS:
            break
    return hits


__all__ = ["SERVER_NAME", "TOOL_BASENAMES", "RepoContext", "build_repo_context_server"]
