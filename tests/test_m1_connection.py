"""Tests for AC-M1-* — Connection milestone acceptance criteria."""
import pytest


@pytest.mark.smoke
def test_ac_m1_001_github_app_scope():
    """AC-M1-001: GitHub App requests exactly contents:read + metadata:read."""
    from services.code_intel.repo_provider import GitHubAppConfig

    config = GitHubAppConfig()
    assert set(config.requested_permissions) == {"contents:read", "metadata:read"}


@pytest.mark.smoke
def test_ac_m1_002_token_minted_per_operation():
    """AC-M1-002: Token is minted per-operation via Nango, not cached."""
    from services.code_intel.repo_provider import RepoProvider
    from tests.fixtures.stubs import NangoStub

    nango = NangoStub(tokens=["ghp_TOKEN_OP1", "ghp_TOKEN_OP2"])
    provider = RepoProvider(nango=nango)

    provider.perform_operation("op-1")
    provider.perform_operation("op-2")

    assert nango.mint_call_count == 2
    assert nango.minted_tokens[0] != nango.minted_tokens[1]


def test_ac_m1_003_token_never_logged():
    """AC-M1-003: GitHub token never written to logs."""
    import logging
    from services.code_intel.repo_provider import RepoProvider
    from tests.fixtures.stubs import NangoStub, LogCapture

    sentinel = "ghp_SENTINEL_TOKEN_VALUE"
    nango = NangoStub(tokens=[sentinel])
    capture = LogCapture()

    with capture:
        provider = RepoProvider(nango=nango)
        provider.perform_operation("op-1")

    assert sentinel not in capture.output
    assert f"token {sentinel}" not in capture.output
    assert f"Authorization: Bearer {sentinel}" not in capture.output


@pytest.mark.smoke
def test_ac_m1_004_repo_provider_encapsulates_git_host():
    """AC-M1-004: RepoProvider interface encapsulates all git-host-specific operations."""
    from services.code_intel.repo_provider import RepoProvider
    from services.code_intel.verifier import StaticAnalysisVerifier

    verifier = StaticAnalysisVerifier()
    violations = verifier.find_git_host_calls_outside_provider()

    assert violations == [], (
        f"Git-host-specific calls found outside RepoProvider boundary: {violations}"
    )


def test_ac_m1_005_repo_provider_bypass_rejected_by_verifier():
    """AC-M1-005: RepoProvider bypasses are rejected by verifier (pre-build negative)."""
    import subprocess
    from tests.fixtures.negative_builds import negative_build_repo_provider_bypass

    with negative_build_repo_provider_bypass() as build_path:
        result = subprocess.run(
            ["python", "-m", "services.code_intel.verifier", str(build_path)],
            capture_output=True,
        )

    assert result.returncode != 0, "Verifier should exit non-zero on RepoProvider bypass"
    assert b"violation" in result.stdout.lower() or b"violation" in result.stderr.lower()
