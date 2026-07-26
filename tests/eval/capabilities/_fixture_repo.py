"""A tiny REAL fixture repo + a REAL code_intel graph + a REAL SDK MCP server.

This is the grounding substrate the orchestrator capability battery drives the
real ``WakeTurn`` over. Nothing here is faked:

* :func:`write_fixture_repo` writes a handful of Python files with *clear, known*
  call/data-flow relationships (``login`` called by ``handle_request``;
  ``save_user`` defined in ``db.py``; ``save_user`` writes the ``users`` table).
* :func:`build_real_graph` runs those files through the REAL
  :class:`code_intel.graph_builder.GraphBuilder` — the same structural indexer the
  product uses — so the graph, its ``calls``/``writes`` edges and PageRank are the
  product's own, not a hand-authored spec.
* :func:`make_code_intel_sdk_server` wraps the REAL
  :class:`code_intel.mcp_server.CodeIntelMCPServer` (bound to that graph + clone)
  as an in-process ``claude_agent_sdk`` MCP server — the SAME
  ``create_sdk_mcp_server`` recipe the Workroom uses for ``propose_change`` — so a
  live model turn can ACTUALLY call ``mcp__code_intel__who_calls`` /
  ``find_references`` / ``get_dependents`` and ground on the real result.

The last piece is the wiring the product itself is missing (see the report): no
product code mounts a ``code_intel`` SDK server onto any query. This module builds
one so the battery can measure whether, *when the seam is wired*, the model
genuinely answers grounded — isolating the "can the brain answer?" question from
the "is the brain plugged in?" question.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from code_intel.graph import Graph
from code_intel.graph_builder import GraphBuilder
from code_intel.mcp_server import CodeIntelMCPServer, make_code_intel_server

# ── The fixture repo: known, verifiable relationships (the golden facts) ──────
# Each file's content is chosen so the ground truth is unambiguous:
#   * ``login`` is called by exactly ``handle_request`` (auth.py) — the "who calls
#     login?" golden.
#   * ``save_user`` is DEFINED in db.py (the "where is save_user defined?" golden).
#   * ``save_user`` WRITES the ``users`` table (the who_writes / data-flow golden).
#   * ``charge_card`` exists nowhere (the out-of-scope negative golden).
_FILES: dict[str, str] = {
    "auth.py": (
        "from db import save_user, load_user\n"
        "\n"
        "\n"
        "def login(username, password):\n"
        "    \"\"\"Authenticate a user and return a session token.\"\"\"\n"
        "    user = load_user(username)\n"
        "    if user and user.check_password(password):\n"
        "        return make_token(user)\n"
        "    return None\n"
        "\n"
        "\n"
        "def make_token(user):\n"
        "    return f\"tok-{user.id}\"\n"
        "\n"
        "\n"
        "def handle_request(request):\n"
        "    \"\"\"Top-level request entry point — calls login.\"\"\"\n"
        "    token = login(request.username, request.password)\n"
        "    if token is None:\n"
        "        return respond_401()\n"
        "    return respond_ok(token)\n"
        "\n"
        "\n"
        "def respond_401():\n"
        "    return {\"status\": 401}\n"
        "\n"
        "\n"
        "def respond_ok(token):\n"
        "    return {\"status\": 200, \"token\": token}\n"
    ),
    "db.py": (
        "from models import User\n"
        "\n"
        "\n"
        "def save_user(user):\n"
        "    \"\"\"Persist a user row into the users table.\"\"\"\n"
        "    row = User(name=user.name, email=user.email)\n"
        "    row.save()\n"
        "    return row.id\n"
        "\n"
        "\n"
        "def load_user(username):\n"
        "    \"\"\"Read a user row from the users table.\"\"\"\n"
        "    return User.query.filter_by(name=username).first()\n"
        "\n"
        "\n"
        "def delete_user(user_id):\n"
        "    row = User.query.get(user_id)\n"
        "    row.delete()\n"
    ),
    "models.py": (
        "class User:\n"
        "    \"\"\"The users table model.\"\"\"\n"
        "\n"
        "    __tablename__ = \"users\"\n"
        "\n"
        "    def __init__(self, name, email):\n"
        "        self.name = name\n"
        "        self.email = email\n"
        "\n"
        "    def check_password(self, password):\n"
        "        return True\n"
        "\n"
        "    def save(self):\n"
        "        return True\n"
    ),
    "billing.py": (
        "from db import save_user\n"
        "\n"
        "\n"
        "def onboard(user):\n"
        "    \"\"\"Onboard a new user — also calls save_user.\"\"\"\n"
        "    return save_user(user)\n"
    ),
}


# ── Golden ground truth (used by the battery as the retrieved-context anchor) ──
GOLDEN = {
    "who_calls_login": {
        "symbol": "login",
        "callers": ["handle_request"],
        "caller_file": "auth.py",
    },
    "save_user_defined": {
        "symbol": "save_user",
        "file": "db.py",
        "def_line": 4,
    },
    "users_table_writers": {
        "table": "users",
        "writers": ["save_user"],
    },
    "out_of_scope": {
        "symbol": "charge_card",  # exists nowhere in the repo
    },
}


@dataclass
class Fixture:
    clone_path: Path
    graph: Graph
    server: CodeIntelMCPServer


def write_fixture_repo(root: Path) -> Path:
    """Write the fixture repo files under ``root`` and return the clone path."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in _FILES.items():
        (root / rel).write_text(content, encoding="utf-8")
    return root


def build_real_graph(clone_path: Path) -> Graph:
    """Run the fixture repo through the REAL code_intel structural indexer."""
    result = GraphBuilder().build(Path(clone_path))
    graph = result.graph
    graph.compute_pagerank()
    return graph


def build_fixture(root: Path) -> Fixture:
    """Write the repo, build the real graph, and bind a real queryable server."""
    clone_path = write_fixture_repo(root)
    graph = build_real_graph(clone_path)
    server = make_code_intel_server(graph=graph, clone_path=clone_path)
    return Fixture(clone_path=clone_path, graph=graph, server=server)


# ── The REAL in-process SDK MCP server over the REAL CodeIntelMCPServer ────────
# The tool names become ``mcp__code_intel__<tool>`` once mounted (SDK convention).
# Each handler is a thin, never-throwing adapter over a real CodeIntelMCPServer
# method — the graph read is real; only the JSON shaping lives here.


def _text_result(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def make_code_intel_sdk_server(server: CodeIntelMCPServer, *, tool_log: list[str] | None = None) -> Any:
    """Build an in-process SDK MCP server exposing the real code_intel tools.

    ``tool_log`` (when supplied) records every tool NAME the model actually
    invoked — so a scenario can assert a real tool call happened (scenario 7).
    This is the exact ``create_sdk_mcp_server`` recipe ``workroom.drafts`` uses
    for ``propose_change``; the difference is only that the tools here read the
    real code_intel graph rather than staging a draft.
    """

    def _record(name: str) -> None:
        if tool_log is not None:
            tool_log.append(name)

    @tool(
        "who_calls",
        "Return the functions that call the given symbol, with file:line citations "
        "drawn from the real code graph.",
        {"symbol": str},
    )
    async def who_calls(args: dict[str, Any]) -> dict[str, Any]:
        _record("who_calls")
        try:
            symbol = str(args.get("symbol", ""))
            res = server.get_dependents(symbol)
            callers = [
                {"symbol": r.id.rsplit("::", 1)[-1], "file": r.file, "line": r.line, "confidence": r.confidence}
                for r in res.results
            ]
            return _text_result({"symbol": symbol, "callers": callers, "status": res.status})
        except Exception as exc:  # noqa: BLE001 - never-throw boundary (Hard Rule 6)
            return {"is_error": True, "content": [{"type": "text", "text": f"who_calls error: {exc}"}]}

    @tool(
        "get_dependents",
        "Return everything that transitively depends on the given symbol (blast radius), "
        "with file:line citations from the real code graph.",
        {"symbol": str},
    )
    async def get_dependents(args: dict[str, Any]) -> dict[str, Any]:
        _record("get_dependents")
        try:
            symbol = str(args.get("symbol", ""))
            res = server.get_dependents(symbol)
            deps = [
                {"symbol": r.id.rsplit("::", 1)[-1], "file": r.file, "line": r.line, "confidence": r.confidence}
                for r in res.results
            ]
            return _text_result({"symbol": symbol, "dependents": deps, "status": res.status})
        except Exception as exc:  # noqa: BLE001
            return {"is_error": True, "content": [{"type": "text", "text": f"get_dependents error: {exc}"}]}

    @tool(
        "find_references",
        "Find every reference (definition + call sites) of a symbol across the repo, "
        "with file:line citations.",
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
        except Exception as exc:  # noqa: BLE001
            return {"is_error": True, "content": [{"type": "text", "text": f"find_references error: {exc}"}]}

    @tool(
        "who_writes",
        "Return the functions that WRITE the given database table, with file:line citations.",
        {"table": str},
    )
    async def who_writes(args: dict[str, Any]) -> dict[str, Any]:
        _record("who_writes")
        try:
            table = str(args.get("table", ""))
            res = server.who_writes(table)
            writers = [
                {"symbol": w.id.rsplit("::", 1)[-1], "file": w.file, "line": w.line, "confidence": w.confidence}
                for w in res.writers
            ]
            return _text_result({"table": table, "writers": writers, "status": res.status})
        except Exception as exc:  # noqa: BLE001
            return {"is_error": True, "content": [{"type": "text", "text": f"who_writes error: {exc}"}]}

    @tool(
        "read",
        "Read one or more files from the repo at the pinned commit (returns file content).",
        {"paths": list},
    )
    async def read(args: dict[str, Any]) -> dict[str, Any]:
        _record("read")
        try:
            paths = args.get("paths") or []
            if isinstance(paths, str):
                paths = [paths]
            res = server.batch_read([str(p) for p in paths])
            files = [{"path": f.path, "content": f.content, "error": f.error} for f in res.files]
            return _text_result({"files": files})
        except Exception as exc:  # noqa: BLE001
            return {"is_error": True, "content": [{"type": "text", "text": f"read error: {exc}"}]}

    return create_sdk_mcp_server(
        name="code_intel",
        version="1.0.0",
        tools=[who_calls, get_dependents, find_references, who_writes, read],
    )


# The MCP-namespaced tool names the mounted code_intel server exposes.
CODE_INTEL_TOOLS: tuple[str, ...] = (
    "mcp__code_intel__who_calls",
    "mcp__code_intel__get_dependents",
    "mcp__code_intel__find_references",
    "mcp__code_intel__who_writes",
    "mcp__code_intel__read",
)
