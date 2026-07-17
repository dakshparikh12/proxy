"""Tests for AC-GV-* — Graph version retention acceptance criteria."""
import pytest


@pytest.mark.smoke
def test_ac_gv_001_graph_version_retained_for_active_meeting_pinned_sha():
    """AC-GV-001: Graph version is retained for each active meeting's pinned SHA."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.meeting import MeetingSession
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import push_webhook_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    meeting1 = MeetingSession.start(pipeline=pipeline)
    sha_x = meeting1.pinned_sha

    pipeline.advance_to_sha("sha_Y")
    meeting2 = MeetingSession.start(pipeline=pipeline)
    sha_y = meeting2.pinned_sha

    assert sha_x != sha_y, "Meetings should be pinned at different SHAs"

    handler = WebhookHandler(pipeline=pipeline)
    webhook = push_webhook_fixture(repo_url=fixture.url, sha="sha_Z")
    handler.handle(webhook)

    result1_after = meeting1.tool_call("get_dependents", symbol="some_fn", limit=50)
    result2_after = meeting2.tool_call("get_dependents", symbol="some_fn", limit=50)

    assert result1_after.graph_sha == sha_x, (
        f"Meeting-1 should answer at SHA {sha_x!r}, got {result1_after.graph_sha!r}"
    )
    assert result2_after.graph_sha == sha_y, (
        f"Meeting-2 should answer at SHA {sha_y!r}, got {result2_after.graph_sha!r}"
    )


def test_ac_gv_002_graph_versions_gcd_when_no_live_meeting_pins():
    """AC-GV-002: Graph versions are GC'd when no live meeting pins them."""
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.meeting import MeetingSession
    from services.code_intel.graph_gc import GraphGarbageCollector
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import push_webhook_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)
    gc = GraphGarbageCollector(pipeline=pipeline)

    meeting = MeetingSession.start(pipeline=pipeline)
    sha_x = meeting.pinned_sha

    meeting.end()

    handler = WebhookHandler(pipeline=pipeline)
    webhook = push_webhook_fixture(repo_url=fixture.url, sha="sha_Y")
    handler.handle(webhook)

    gc.run()

    retention_index = pipeline.graph_retention_index
    assert sha_x not in retention_index, (
        f"SHA {sha_x!r} should have been GC'd from retention index after meeting ended"
    )
