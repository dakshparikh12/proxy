"""Tests for AC-M2-* — Clone milestone acceptance criteria."""
import pytest


@pytest.mark.smoke
def test_ac_m2_001_per_tenant_encrypted_volume():
    """AC-M2-001: Clone is stored on per-tenant encrypted volume with no cross-tenant sharing."""
    from services.code_intel.cloner import Cloner
    from tests.fixtures.stubs import TwoTenantCloneFixture

    fixture = TwoTenantCloneFixture()
    cloner = Cloner()

    path_a = cloner.clone(tenant_id="tenant-A", repo_url=fixture.repo_url)
    path_b = cloner.clone(tenant_id="tenant-B", repo_url=fixture.repo_url)

    assert str(path_a).startswith("/tenants/tenant-A/")
    assert str(path_b).startswith("/tenants/tenant-B/")

    with pytest.raises(PermissionError):
        fixture.open_as_tenant("tenant-B", path_a / "README.md")


def test_ac_m2_002_clone_valid_working_tree_at_pinned_sha():
    """AC-M2-002: Clone produces a valid working tree at pinned SHA."""
    import subprocess
    from services.code_intel.cloner import Cloner
    from tests.fixtures.repos import small_repo_fixture

    fixture = small_repo_fixture()
    cloner = Cloner()

    clone_path = cloner.clone(
        tenant_id="tenant-test",
        repo_url=fixture.url,
        sha=fixture.expected_sha,
    )

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=clone_path,
        capture_output=True,
        text=True,
    )
    actual_sha = result.stdout.strip()

    assert actual_sha == fixture.expected_sha
    actual_files = {str(p.relative_to(clone_path)) for p in clone_path.rglob("*") if p.is_file()}
    assert actual_files == set(fixture.expected_file_list)


def test_ac_m2_003_large_repo_uses_blobless_clone():
    """AC-M2-003: Large repo uses blobless clone."""
    from services.code_intel.cloner import Cloner
    from tests.fixtures.stubs import LargeFileCountStub, GitInterceptor

    stub = LargeFileCountStub(file_count=120_000)
    interceptor = GitInterceptor()
    cloner = Cloner(git_interceptor=interceptor, file_count_provider=stub)

    cloner.clone(tenant_id="tenant-test", repo_url="https://github.com/example/large-repo")

    clone_args = interceptor.recorded_args
    assert any("--filter=blob:none" in " ".join(args) for args in clone_args), (
        f"Expected --filter=blob:none in clone args, got: {clone_args}"
    )


@pytest.mark.smoke
def test_ac_m2_004_never_pushes_to_upstream():
    """AC-M2-004: System never pushes to upstream origin."""
    from services.code_intel.cloner import Cloner
    from services.code_intel.graph_builder import GraphBuilder
    from tests.fixtures.stubs import GitInterceptor, small_repo_fixture

    interceptor = GitInterceptor()
    fixture = small_repo_fixture()
    cloner = Cloner(git_interceptor=interceptor)
    builder = GraphBuilder(git_interceptor=interceptor)

    clone_path = cloner.clone(tenant_id="tenant-test", repo_url=fixture.url)
    cloner.pull_delta(clone_path)
    builder.build(clone_path)

    push_calls = [
        args for args in interceptor.recorded_args
        if args and args[0] == "git" and "push" in args
    ]
    assert push_calls == [], f"Unexpected git push calls: {push_calls}"


@pytest.mark.smoke
def test_ac_m2_005_canonical_clone_never_executes_repo_code():
    """AC-M2-005: Canonical clone never executes repository code."""
    import tempfile
    from pathlib import Path
    from services.code_intel.cloner import Cloner
    from services.code_intel.graph_builder import GraphBuilder
    from tests.fixtures.stubs import GitInterceptor, RepoWithExecutableScript

    with tempfile.TemporaryDirectory() as tmp:
        sentinel_path = Path(tmp) / "sentinel_executed"
        fixture = RepoWithExecutableScript(sentinel_path=sentinel_path)
        interceptor = GitInterceptor()

        cloner = Cloner(git_interceptor=interceptor)
        builder = GraphBuilder(git_interceptor=interceptor)

        clone_path = cloner.clone(tenant_id="tenant-test", repo_url=fixture.url)
        builder.build(clone_path)

        assert not sentinel_path.exists(), "Repo executable was run during clone/index"
        exec_calls = [
            args for args in interceptor.recorded_args
            if any(fixture.executable_name in str(a) for a in args)
        ]
        assert exec_calls == [], f"Unexpected exec calls to repo script: {exec_calls}"


def test_ac_m2_006_push_event_triggers_delta_pull_not_reclone():
    """AC-M2-006: Push event triggers delta pull, not re-clone."""
    from services.code_intel.cloner import Cloner
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.stubs import GitInterceptor, small_repo_fixture, push_webhook_fixture

    interceptor = GitInterceptor()
    fixture = small_repo_fixture()
    cloner = Cloner(git_interceptor=interceptor)
    handler = WebhookHandler(cloner=cloner, git_interceptor=interceptor)

    clone_path = cloner.clone(tenant_id="tenant-test", repo_url=fixture.url)
    interceptor.reset()

    webhook = push_webhook_fixture(repo_url=fixture.url, sha="deadbeef")
    handler.handle(webhook)

    all_args = interceptor.recorded_args
    clone_calls = [a for a in all_args if a and a[0] == "git" and "clone" in a]
    fetch_or_pull_calls = [
        a for a in all_args
        if a and a[0] == "git" and ("fetch" in a or "pull" in a)
    ]

    assert clone_calls == [], f"Unexpected re-clone after push: {clone_calls}"
    assert fetch_or_pull_calls, "Expected a git fetch/pull after push webhook"
