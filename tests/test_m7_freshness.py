"""Tests for AC-M7-* — Freshness acceptance criteria."""
import pytest


@pytest.mark.smoke
def test_ac_m7_001_push_webhook_signature_validated():
    """AC-M7-001: Push webhook signature is validated before processing."""
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.stubs import bad_hmac_webhook_fixture, GraphRebuildCounter

    rebuild_counter = GraphRebuildCounter()
    handler = WebhookHandler(rebuild_counter=rebuild_counter)

    webhook = bad_hmac_webhook_fixture()
    response = handler.handle(webhook)

    assert response.status_code == 401, (
        f"Expected HTTP 401 for bad HMAC signature, got {response.status_code}"
    )
    assert not response.enqueued, "Webhook with bad HMAC should not be enqueued"
    assert rebuild_counter.count == 0, (
        f"Graph rebuild should not be triggered on invalid signature, got {rebuild_counter.count}"
    )


def test_ac_m7_002_duplicate_webhook_deduplicated():
    """AC-M7-002: Duplicate webhook (same delivery-GUID + commit SHA) is deduplicated."""
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.stubs import push_webhook_fixture, GraphRebuildCounter

    rebuild_counter = GraphRebuildCounter()
    handler = WebhookHandler(rebuild_counter=rebuild_counter)

    webhook = push_webhook_fixture(
        repo_url="https://github.com/example/repo",
        sha="abc123def456",
        delivery_guid="guid-12345",
    )

    handler.handle(webhook)
    handler.handle(webhook)

    assert rebuild_counter.count == 1, (
        f"Expected exactly 1 graph rebuild for duplicate webhooks, got {rebuild_counter.count}"
    )


def test_ac_m7_003_meeting_start_reconciles_drift():
    """AC-M7-003: Meeting start reconciles ls-remote vs local tip and pulls drift before confirming readiness."""
    from services.code_intel.meeting import MeetingSession
    from services.code_intel.pipeline import Pipeline
    from tests.fixtures.stubs import DriftSimulation, EventLog

    drift = DriftSimulation(local_tip="sha_old", remote_tip="sha_new", commits_behind=2)
    event_log = EventLog()

    session = MeetingSession.start(
        pipeline=Pipeline.from_drift_fixture(drift),
        event_log=event_log,
    )

    ops = event_log.operations
    op_types = [op.type for op in ops]

    assert "pull" in op_types, "Expected a git pull/fetch during drift reconciliation"
    assert "graph_rebuild" in op_types, "Expected graph rebuild after drift pull"
    assert "readiness_confirmed" in op_types, "Expected readiness_confirmed in event log"

    pull_idx = op_types.index("pull")
    rebuild_idx = op_types.index("graph_rebuild")
    ready_idx = op_types.index("readiness_confirmed")

    assert pull_idx < rebuild_idx < ready_idx, (
        f"Event ordering wrong: pull({pull_idx}) -> rebuild({rebuild_idx}) -> ready({ready_idx})"
    )


@pytest.mark.smoke
def test_ac_m7_004_meeting_session_pins_to_single_commit_sha():
    """AC-M7-004: Meeting session pins to a single commit SHA written to meetings.pinned_sha."""
    from services.code_intel.meeting import MeetingSession
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import push_webhook_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    session = MeetingSession.start(pipeline=pipeline)
    pinned_at_start = session.pinned_sha

    assert pinned_at_start == fixture.expected_sha, (
        f"meetings.pinned_sha={pinned_at_start!r} does not match repo SHA {fixture.expected_sha!r}"
    )

    handler = WebhookHandler(pipeline=pipeline)
    webhook = push_webhook_fixture(repo_url=fixture.url, sha="new_sha_after_push")
    handler.handle(webhook)

    assert session.pinned_sha == pinned_at_start, (
        f"meetings.pinned_sha changed during meeting from {pinned_at_start!r} to {session.pinned_sha!r}"
    )


def test_ac_m7_005_mid_meeting_push_does_not_change_pin_or_results():
    """AC-M7-005: Mid-meeting push does not change the session's pinned SHA or query results."""
    from services.code_intel.meeting import MeetingSession
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import push_webhook_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)
    session = MeetingSession.start(pipeline=pipeline)
    pinned_sha = session.pinned_sha

    result_1 = session.tool_call("get_dependents", symbol="some_fn", limit=50)

    handler = WebhookHandler(pipeline=pipeline)
    webhook = push_webhook_fixture(repo_url=fixture.url, sha="sha_Y_after_push")
    handler.handle(webhook)

    result_2 = session.tool_call("get_dependents", symbol="some_fn", limit=50)

    assert result_1 == result_2, (
        "Tool results changed after mid-meeting push — session must remain pinned"
    )
    assert session.pinned_sha == pinned_sha, (
        f"meetings.pinned_sha changed from {pinned_sha!r} to {session.pinned_sha!r}"
    )
    assert session.notifications, "Expected a notification about repo advancing"


def test_ac_m7_006_mid_meeting_push_emits_repo_advanced_notification():
    """AC-M7-006: Mid-meeting push emits 'repo advanced N commits' notification."""
    from services.code_intel.meeting import MeetingSession
    from services.code_intel.pipeline import run_full_pipeline
    from services.code_intel.webhook_handler import WebhookHandler
    from tests.fixtures.repos import small_repo_fixture
    from tests.fixtures.stubs import push_webhook_fixture

    fixture = small_repo_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)
    session = MeetingSession.start(pipeline=pipeline)

    handler = WebhookHandler(pipeline=pipeline)
    webhook = push_webhook_fixture(
        repo_url=fixture.url,
        sha="sha_Y",
        num_commits=3,
    )
    handler.handle(webhook)

    notifications = session.notifications
    assert len(notifications) == 1, (
        f"Expected exactly 1 notification, got {len(notifications)}"
    )
    notif_text = notifications[0].text.lower()
    assert "repo advanced" in notif_text and "3" in notif_text, (
        f"Notification text does not match 'repo advanced N commits': {notifications[0].text!r}"
    )


def test_ac_m7_007_pr_meeting_pins_to_pr_head_not_default_branch():
    """AC-M7-007: Meeting about a PR pins meetings.pinned_sha to the PR head (not the default-branch tip)."""
    from services.code_intel.meeting import MeetingSession
    from services.code_intel.pipeline import run_full_pipeline
    from tests.fixtures.repos import pr_meeting_fixture

    fixture = pr_meeting_fixture()
    pipeline = run_full_pipeline(tenant_id="tenant-test", repo_url=fixture.url)

    session = MeetingSession.start(
        pipeline=pipeline,
        pr_number=fixture.pr_number,
    )

    assert session.pinned_sha == fixture.pr_head_sha, (
        f"meetings.pinned_sha should be PR head ({fixture.pr_head_sha!r}), "
        f"got {session.pinned_sha!r}"
    )
    assert session.pinned_sha != fixture.default_branch_tip, (
        f"meetings.pinned_sha must NOT be the default-branch tip ({fixture.default_branch_tip!r}) "
        f"for a PR-scoped meeting"
    )
