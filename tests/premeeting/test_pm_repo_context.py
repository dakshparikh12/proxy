"""repo_context.py — the clone-backed live-search toolbelt + map carry (PM-DOWN-03 + toolbelt).

Real clone; the SDK server is built for real and its handlers are invoked directly (the same
never-throw handlers the wake/Workroom mount). PM-DOWN-01/02 (map reaches the real wake/Workroom
system_prompt) are proven in the wiring tests; here we prove the substrate: the toolbelt greps/
reads the real clone, redacts secrets, and fail-closes to None.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from premeeting import repo_context
from premeeting.cloner import Cloner
from premeeting.exclusions import ExclusionManager
from premeeting.repo_context import RepoContext, build_repo_context_server


def _clone(make_git_repo: Any) -> tuple[Path, ExclusionManager]:
    src, _sha = make_git_repo(
        {
            "src/server.py": "def handle_login():\n    return 1\n",
            "src/models.py": 'API_KEY = "AKIAABCDEFGHIJKLMNOP"\n',
            "README.md": "# fixture\n",
            ".env": "SECRET=leakme_abcdefgh\n",
        }
    )
    em = ExclusionManager()
    checkout = Cloner(exclusion_manager=em).clone("tenant-a", src.as_uri())
    return checkout, em


async def _call(server: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Invoke a mounted tool through the REAL mcp CallToolRequest path (as the SDK drives it)."""
    import mcp.types as mt

    inst = server["instance"]
    handler = inst.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call", params=mt.CallToolRequestParams(name=tool_name, arguments=dict(args))
    )
    res = await handler(req)
    content = res.root.content
    text = content[0].text
    if getattr(res.root, "isError", False):
        return {"__error__": text}
    return json.loads(text)


async def _mounted_tool_names(server: Any) -> list[str]:
    """The tool names the server advertises, via the REAL ListToolsRequest path."""
    import mcp.types as mt

    inst = server["instance"]
    handler = inst.request_handlers[mt.ListToolsRequest]
    res = await handler(mt.ListToolsRequest(method="tools/list"))
    return [t.name for t in res.root.tools]


# ── PM-DOWN-03: fail-closed to None ──────────────────────────────────────────
def test_pm_down_03_no_clone_no_map_returns_none() -> None:
    ctx = RepoContext(clone_path=Path("/nonexistent/tenant/repo/checkout"), map_text=None)
    assert ctx.build_server() is None  # no crash, mounts nothing


def test_pm_down_03_map_but_no_clone_still_none_server_but_map_carried() -> None:
    ctx = RepoContext(clone_path=Path("/nonexistent/checkout"), map_text="# Repo Map\n")
    assert ctx.build_server() is None  # no tools to serve
    assert ctx.has_map()  # but the map still rides as a prompt prefix


def test_repo_context_carries_map_text(make_git_repo: Any) -> None:
    checkout, _em = _clone(make_git_repo)
    ctx = RepoContext(clone_path=checkout, map_text="# Repo Map — orientation", tenant_id="tenant-a")
    assert ctx.has_map()
    assert ctx.build_server() is not None  # clone exists → toolbelt mounts


# ── toolbelt serves real grep/read off the clone, redacting secrets ──────────
@pytest.mark.asyncio
async def test_toolbelt_grep_returns_real_file_line(make_git_repo: Any) -> None:
    checkout, em = _clone(make_git_repo)
    server = build_repo_context_server(checkout, exclusions=em)
    out = await _call(server, "grep", {"query": "handle_login"})
    refs = out["references"]
    assert any(r["file"] == "src/server.py" for r in refs)
    assert all(isinstance(r["line"], int) for r in refs)


@pytest.mark.asyncio
async def test_toolbelt_read_redacts_secret_value(make_git_repo: Any) -> None:
    checkout, em = _clone(make_git_repo)
    server = build_repo_context_server(checkout, exclusions=em)
    out = await _call(server, "read", {"paths": ["src/models.py"]})
    content = out["files"][0]["content"]
    assert content is not None
    assert "AKIAABCDEFGHIJKLMNOP" not in content  # secret value redacted on read
    assert "[REDACTED]" in content


@pytest.mark.asyncio
async def test_toolbelt_read_refuses_excluded_and_escape_paths(make_git_repo: Any) -> None:
    checkout, em = _clone(make_git_repo)
    server = build_repo_context_server(checkout, exclusions=em)
    # Excluded secret path → per-file error, never content.
    env = await _call(server, "read", {"paths": [".env"]})
    assert env["files"][0]["content"] is None
    assert env["files"][0]["error"] == "excluded path"
    # A .. escape → refused (confined to the clone).
    esc = await _call(server, "read", {"paths": ["../../../etc/passwd"]})
    assert esc["files"][0]["content"] is None
    assert "outside" in esc["files"][0]["error"]


@pytest.mark.asyncio
async def test_toolbelt_glob_excludes_git_and_secrets(make_git_repo: Any) -> None:
    checkout, em = _clone(make_git_repo)
    server = build_repo_context_server(checkout, exclusions=em)
    out = await _call(server, "glob", {"pattern": "*.py"})
    files = out["files"]
    assert "src/server.py" in files and "src/models.py" in files
    assert not any(f.startswith(".git") for f in files)


@pytest.mark.asyncio
async def test_server_mounts_the_curated_toolbelt_names(make_git_repo: Any) -> None:
    checkout, em = _clone(make_git_repo)
    server = build_repo_context_server(checkout, exclusions=em)
    # The mounted names match the single-source-of-truth constant (no phantom tool).
    mounted = await _mounted_tool_names(server)
    assert set(mounted) == set(repo_context.TOOL_BASENAMES)
    assert server["name"] == repo_context.SERVER_NAME  # resolves the mcp__code_intel__* names
