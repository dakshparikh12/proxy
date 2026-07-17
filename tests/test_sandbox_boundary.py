"""Tests for AC-SANDBOX-* — Sandbox boundary contract acceptance criteria."""
import pytest


@pytest.mark.smoke
def test_ac_sandbox_001_lsp_tools_absent_from_sandbox_manifest():
    """AC-SANDBOX-001: find_references and LSP tools are absent from E2B sandbox tool manifest."""
    from services.code_intel.sandbox import get_sandbox_tool_manifest

    manifest = get_sandbox_tool_manifest()
    tool_names = set(manifest.tool_names)

    assert "find_references" not in tool_names, (
        "'find_references' must not be registered in the E2B sandbox tool manifest"
    )

    lsp_tool_names = {name for name in tool_names if "lsp" in name.lower() or "reference" in name.lower()}
    assert lsp_tool_names == set(), (
        f"LSP-related tools found in sandbox manifest: {lsp_tool_names}"
    )


@pytest.mark.smoke
def test_ac_sandbox_002_no_tool_name_duplicated_across_host_and_sandbox():
    """AC-SANDBOX-002: No code_intel tool name is duplicated across host-side and sandbox surfaces."""
    from services.code_intel.mcp_server import get_host_tool_manifest
    from services.code_intel.sandbox import get_sandbox_tool_manifest

    host_manifest = get_host_tool_manifest()
    sandbox_manifest = get_sandbox_tool_manifest()

    host_tools = set(host_manifest.tool_names)
    sandbox_tools = set(sandbox_manifest.tool_names)

    overlap = host_tools & sandbox_tools
    assert overlap == set(), (
        f"Tool names duplicated across host and sandbox manifests: {overlap}"
    )
