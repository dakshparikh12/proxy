"""The PRODUCT ``code_intel`` SDK MCP server — the tools the meeting path actually mounts.

The wake turn's ``answer-question`` / ``surface-risk`` behaviors and every Workroom
disposition advertise ``mcp__code_intel__*`` tools (§3.4 / §10.5), but until this module
NO product code built the server that provides them — so the names resolved to nothing and
Proxy could not answer a grounded codebase question in a meeting (its core premise). This is
the wiring that closes that gap.

:func:`build_code_intel_sdk_server` wraps the REAL
:class:`~code_intel.mcp_server.CodeIntelMCPServer` (bound to a meeting's tenant graph + pinned
clone) as an in-process ``claude_agent_sdk`` MCP server via ``create_sdk_mcp_server`` — the
SAME recipe the Workroom uses for ``propose_change`` (``workroom.drafts.make_propose_change_server``).
The tools read the real structural graph; once mounted the tool names become
``mcp__code_intel__<tool>`` (SDK convention) and the model's ``allowed_tools`` resolve to real
tools. Every handler is grounded-or-abstains (Law 1/2) and NEVER throws (Hard Rule 6 — the
never-throw boundary): a tool fault becomes an ``is_error`` result, never a raised exception.

**Isolation triad (Hard Rule 4).** The server is built PER MEETING from THAT meeting's tenant's
graph + clone — the ``CodeIntelMCPServer`` carries the ``tenant_id`` and its ``clone_path`` is
that tenant's checkout, so one meeting's server can never read another tenant's volume. A repo
that has not been indexed yet yields an empty graph (honest ``not-found``), never a cross-tenant
read and never a crash.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

from .graph_store import read_graph
from .mcp_server import CodeIntelMCPServer, make_code_intel_server
from .paths import tenant_repo_dir


@dataclass(frozen=True)
class CodeIntelContext:
    """A meeting's resolved code_intel grounding — the durable per-repo index + clone (§12.2).

    ``graph_db_path`` is the tenant's per-repo ``graph.db`` (the structural index
    ``graph_store.write_graph`` persists); ``clone_path`` is that repo's pinned ``checkout``;
    ``tenant_id`` scopes the server (isolation triad, Hard Rule 4). Both paths are on the
    tenant volume — one meeting's context can never point at another tenant's data. Built by
    :meth:`for_tenant_repo` from the identity the meeting already carries (tenant_id + repo
    name), the SAME path ``harness.webhooks._resolve_referent_corpus`` locates the index at.
    """

    graph_db_path: Path
    clone_path: Path
    tenant_id: str = ""

    @classmethod
    def for_tenant_repo(cls, *, tenant_id: str, repo_name: str) -> CodeIntelContext:
        """Resolve the durable index + clone paths for a tenant's repo (the meeting's grounding).

        Locates ``graph.db`` + ``checkout`` under the canonical per-tenant repo dir
        (:func:`code_intel.paths.tenant_repo_dir`) — the exact artifacts Doc 01's indexer wrote.
        Pure path arithmetic (no IO): the caller checks existence / builds the server, which
        fails closed when the index has not been built yet.
        """
        repo_dir = tenant_repo_dir(tenant_id, repo_name)
        return cls(
            graph_db_path=repo_dir / "graph.db",
            clone_path=repo_dir / "checkout",
            tenant_id=tenant_id,
        )

    def build_server(self, *, lsp: Any = None) -> McpSdkServerConfig | None:
        """Build this context's ``code_intel`` SDK server, or ``None`` if the repo is unindexed."""
        return build_code_intel_server_for_repo(
            graph_db_path=self.graph_db_path,
            clone_path=self.clone_path,
            tenant_id=self.tenant_id,
            lsp=lsp,
        )

#: The MCP-namespaced tool names this server exposes once mounted. Every behavior /
#: disposition that advertises a code-intel tool MUST name one of these (so ``allowed_tools``
#: resolves to a real mounted tool). The harness ``answer-question`` needs get_dependents /
#: who_writes / list_entry_points / grep / read / batch_read; ``surface-risk`` needs grep /
#: read / get_dependents; the Workroom ``MAP_TOOLS`` needs get_dependents / who_writes /
#: list_entry_points / grep / read. This set is the union — the single source of truth for the
#: mount-vs-advertise contract (the two agree by construction).
CODE_INTEL_TOOL_NAMES: tuple[str, ...] = (
    "mcp__code_intel__get_dependents",
    "mcp__code_intel__who_writes",
    "mcp__code_intel__list_entry_points",
    "mcp__code_intel__find_references",
    "mcp__code_intel__grep",
    "mcp__code_intel__read",
    "mcp__code_intel__batch_read",
)

#: The SDK server name; tools mount under ``mcp__code_intel__*``.
CODE_INTEL_SERVER_NAME = "code_intel"


def _text_result(payload: Any) -> dict[str, Any]:
    """Shape a grounded result as the SDK's text content block (real graph read, JSON body)."""
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def _error_result(msg: str) -> dict[str, Any]:
    """The never-throw boundary (Hard Rule 6): a tool fault returns an ``is_error`` result."""
    return {"is_error": True, "content": [{"type": "text", "text": msg}]}


def _leaf(node_id: str) -> str:
    """The trailing symbol of a canonical id (``pkg/mod.py::func`` -> ``func``)."""
    return node_id.rsplit("::", 1)[-1]


def build_code_intel_sdk_server(
    server: CodeIntelMCPServer, *, tool_log: list[str] | None = None
) -> McpSdkServerConfig:
    """Build the in-process ``code_intel`` SDK MCP server over a bound :class:`CodeIntelMCPServer`.

    ``server`` is the REAL tenant-scoped code_intel server (its graph + pinned clone). Each
    tool below is a thin, never-throwing adapter over a real ``server`` method — the graph read
    is real; only the JSON shaping lives here. This is the exact ``create_sdk_mcp_server`` recipe
    ``workroom.drafts`` uses for ``propose_change``; the difference is only that these tools READ
    the code graph rather than staging a draft.

    ``tool_log`` (when supplied) records every tool NAME the model actually invoked — used by the
    capability battery to assert a real tool call fired. It is never wired on the live path.
    """

    def _record(name: str) -> None:
        if tool_log is not None:
            tool_log.append(name)

    @tool(
        "get_dependents",
        "Return everything that transitively depends on the given symbol (blast radius), with "
        "file:line citations drawn from the real code graph. Confidence is 'resolved' for an "
        "exact-referent dependent, 'lower-bound' for a heuristic (attr) edge.",
        {"symbol": str},
    )
    async def get_dependents(args: dict[str, Any]) -> dict[str, Any]:
        _record("get_dependents")
        try:
            symbol = str(args.get("symbol", ""))
            res = server.get_dependents(symbol)
            deps = [
                {"symbol": _leaf(r.id), "file": r.file, "line": r.line, "confidence": r.confidence}
                for r in res.results
            ]
            return _text_result({"symbol": symbol, "dependents": deps, "status": res.status})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary (Hard Rule 6)
            return _error_result(f"get_dependents error: {exc}")

    @tool(
        "who_writes",
        "Return the functions that WRITE the given database table, with file:line citations "
        "from the real code graph.",
        {"table": str},
    )
    async def who_writes(args: dict[str, Any]) -> dict[str, Any]:
        _record("who_writes")
        try:
            table = str(args.get("table", ""))
            res = server.who_writes(table)
            writers = [
                {"symbol": _leaf(w.id), "file": w.file, "line": w.line, "confidence": w.confidence}
                for w in res.writers
            ]
            return _text_result({"table": table, "writers": writers, "status": res.status})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary (Hard Rule 6)
            return _error_result(f"who_writes error: {exc}")

    @tool(
        "list_entry_points",
        "Return the repo's entry points (routes / top-level handlers), with file:line citations.",
        {},
    )
    async def list_entry_points(_args: dict[str, Any]) -> dict[str, Any]:
        _record("list_entry_points")
        try:
            res = server.list_entry_points()
            eps = [
                {"symbol": _leaf(r.id), "file": r.file, "line": r.line, "confidence": r.confidence}
                for r in res.results
            ]
            return _text_result({"entry_points": eps, "status": res.status})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary (Hard Rule 6)
            return _error_result(f"list_entry_points error: {exc}")

    @tool(
        "find_references",
        "Find every reference (definition + call sites) of a symbol across the repo, with "
        "file:line citations. Use this to answer 'who calls X' / 'where is X used'.",
        {"symbol": str},
    )
    async def find_references(args: dict[str, Any]) -> dict[str, Any]:
        _record("find_references")
        try:
            symbol = str(args.get("symbol", ""))
            res = server.find_references(symbol)
            refs = [
                {"file": r.file, "line": r.line, "confidence": r.confidence, "context": (r.context or "").strip()}
                for r in res.results
            ]
            return _text_result({"symbol": symbol, "references": refs, "status": res.status})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary (Hard Rule 6)
            return _error_result(f"find_references error: {exc}")

    @tool(
        "grep",
        "Grep the repo for a symbol/word and return the reference sites (definition + uses) "
        "with file:line citations — the ripgrep-backed reference scan over the pinned clone.",
        {"symbol": str},
    )
    async def grep(args: dict[str, Any]) -> dict[str, Any]:
        _record("grep")
        try:
            # ``grep`` is the ripgrep reference scan — the SAME real read ``find_references``
            # drives (§12.2). Accept ``symbol`` or ``query`` (the model may name either).
            symbol = str(args.get("symbol") or args.get("query") or "")
            res = server.find_references(symbol)
            refs = [
                {"file": r.file, "line": r.line, "confidence": r.confidence, "context": (r.context or "").strip()}
                for r in res.results
            ]
            return _text_result({"symbol": symbol, "references": refs, "status": res.status})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary (Hard Rule 6)
            return _error_result(f"grep error: {exc}")

    @tool(
        "read",
        "Read one or more files from the repo at the pinned commit (returns file content). "
        "Pass a single path string or a list of paths.",
        {"paths": list},
    )
    async def read(args: dict[str, Any]) -> dict[str, Any]:
        _record("read")
        return _batch_read(args)

    @tool(
        "batch_read",
        "Read a batch of files from the repo at the pinned commit (returns each file's content "
        "or a per-file error). Pass a list of paths.",
        {"paths": list},
    )
    async def batch_read(args: dict[str, Any]) -> dict[str, Any]:
        _record("batch_read")
        return _batch_read(args)

    def _batch_read(args: dict[str, Any]) -> dict[str, Any]:
        try:
            paths = args.get("paths") or args.get("path") or []
            if isinstance(paths, str):
                paths = [paths]
            res = server.batch_read([str(p) for p in paths])
            files = [{"path": f.path, "content": f.content, "error": f.error} for f in res.files]
            return _text_result({"files": files, "truncated": res.truncated})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary (Hard Rule 6)
            return _error_result(f"read error: {exc}")

    return create_sdk_mcp_server(
        name=CODE_INTEL_SERVER_NAME,
        version="1.0.0",
        tools=[get_dependents, who_writes, list_entry_points, find_references, grep, read, batch_read],
    )


def build_code_intel_server_for_repo(
    *, graph_db_path: Path | str, clone_path: Path | str, tenant_id: str = "", lsp: Any = None
) -> McpSdkServerConfig | None:
    """Build the meeting's ``code_intel`` SDK server from a tenant's persisted graph + clone.

    The meeting path's ONE product entry point: load the tenant's durable structural index from
    its per-repo ``graph.db`` (:func:`code_intel.graph_store.read_graph`), bind it + the pinned
    ``checkout`` clone into a tenant-scoped :class:`CodeIntelMCPServer` (isolation triad — Hard
    Rule 4), and wrap it as the in-process SDK server.

    Fail-closed (never raises, Rule 6): returns ``None`` when the repo has no built graph AND no
    clone on disk (the index has not been built yet) — the caller then mounts NO code_intel
    server and Proxy degrades honestly (it wakes, it just has no codebase tools this meeting)
    rather than mounting an empty shell or crashing the model loop.
    """
    graph_db = Path(graph_db_path)
    clone = Path(clone_path)
    if not graph_db.exists() and not clone.exists():
        # Nothing indexed for this repo yet — honest degradation, mount nothing.
        return None
    try:
        graph = read_graph(graph_db)
        server = make_code_intel_server(
            graph=graph, clone_path=clone, tenant_id=tenant_id, lsp=lsp
        )
        return build_code_intel_sdk_server(server)
    except Exception:  # noqa: BLE001 - a build fault degrades to no-mount, never crashes the meeting
        return None


__all__ = [
    "CODE_INTEL_SERVER_NAME",
    "CODE_INTEL_TOOL_NAMES",
    "CodeIntelContext",
    "build_code_intel_sdk_server",
    "build_code_intel_server_for_repo",
]
