"""Regression guard — CANARY ESCAPE #2: the HOST code-intel tool matrix must be EXACTLY
the canonical 8 tools.

The sandbox side has an exact-set guard (``len(SANDBOX_TOOL_NAMES) == 8`` +
``set(...) == {...}`` in tests/doc05/test_sandbox_tools.py). The HOST side — the 8 read-only
``code_intel`` tools the wake turn calls (§7 / CANONICAL §12.6) — had NONE. A previous real
drift (the SDK server mounted 7, dropping shares_table / owner / lookup_referent — see the
sdk_server.py docstring) is exactly the failure this closes: a tool silently dropped from the
host matrix would leave every existing test green.

The canonical 8 host code-intel tools (locked, mcp_server.HOST_TOOL_NAMES + CANONICAL §7/§12.6):
    get_dependents, who_writes, shares_table, list_entry_points,
    owner, batch_read, lookup_referent, find_references

This test pins that set as a literal (so it does not merely restate the same constant it guards)
and asserts:
  * ``mcp_server.HOST_TOOL_NAMES`` equals the 8 (exact set, exactly 8, no dupes),
  * ``get_host_tool_manifest().tool_names`` equals the 8, and
  * ``sdk_server._CODE_INTEL_TOOL_BASENAMES`` is a SUPERSET of the 8 (it additionally mounts the
    native ``grep``/``read`` where the agent runs — but every one of the 8 host tools must be
    present, so dropping any turns this RED).

Dropping any one of the 8 from the host constant / manifest turns this RED.
"""
from __future__ import annotations

# The canonical set, pinned as an independent literal (the guard, not a re-export).
CANONICAL_HOST_TOOLS: frozenset[str] = frozenset(
    {
        "get_dependents",
        "who_writes",
        "shares_table",
        "list_entry_points",
        "owner",
        "batch_read",
        "lookup_referent",
        "find_references",
    }
)


def test_host_tool_constant_is_exactly_the_canonical_eight() -> None:
    """mcp_server.HOST_TOOL_NAMES is EXACTLY the canonical 8 host code-intel tools."""
    from code_intel.mcp_server import HOST_TOOL_NAMES

    assert len(CANONICAL_HOST_TOOLS) == 8, "the canonical host matrix is 8 tools (guard literal drifted)"
    assert set(HOST_TOOL_NAMES) == CANONICAL_HOST_TOOLS, (
        "HOST_TOOL_NAMES drifted from the canonical 8 host tools (a tool was dropped, renamed, or added)"
    )
    assert len(HOST_TOOL_NAMES) == 8, "the host tool matrix must be EXACTLY 8 tools (no dupes, none dropped)"


def test_host_tool_manifest_advertises_exactly_the_canonical_eight() -> None:
    """The host tool MANIFEST (what the server advertises) equals the canonical 8."""
    from code_intel.mcp_server import get_host_tool_manifest

    names = get_host_tool_manifest().tool_names
    assert set(names) == CANONICAL_HOST_TOOLS, (
        "get_host_tool_manifest().tool_names drifted from the canonical 8 host tools"
    )
    assert len(names) == 8, "the host tool manifest must advertise EXACTLY 8 tools"


def test_sdk_server_mounts_every_canonical_host_tool() -> None:
    """The SDK server's basenames MUST include every one of the 8 host tools (the drift that
    dropped shares_table / owner / lookup_referent must stay impossible)."""
    from code_intel.sdk_server import _CODE_INTEL_TOOL_BASENAMES

    missing = CANONICAL_HOST_TOOLS - set(_CODE_INTEL_TOOL_BASENAMES)
    assert not missing, f"SDK server dropped canonical host tool(s): {sorted(missing)}"
