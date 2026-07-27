"""B7 — report. Criteria: AC-PME-13, AC-PME-14, AC-PME-16, AC-PME-16-NEG."""
from __future__ import annotations

import uuid

import pytest
from harness.post_meeting.report import (
    CONFIDENCE_BY_STATUS,
    KIND_BY_STATUS,
    REPORTABLE_EVENTS,
    Confidence,
    ReportKind,
    build_report,
    confidence_rank,
    deliver,
    select_channel,
    status_rank,
)

pytestmark = pytest.mark.asyncio

TASK = uuid.uuid4()
CARD = ("draft_card",)
ALL_STATUSES = ("done", "partial", "failed", "needs_clarification", "needs_review")


class Recorder:
    def __init__(self, error=None):
        self.sent: list[tuple[str, object]] = []
        self._error = error

    async def __call__(self, *, channel, report):
        if self._error:
            raise self._error
        self.sent.append((channel, report))


def env(status, **kw):
    base = {"status": status, "headline": "h", "detail": "d", "receipts": ["r.py:1"]}
    base.update(kw)
    return base


# ── AC-PME-13 · needs_clarification is a question, never a failure ────────
async def test_ac_pme_13_needs_clarification_is_a_question():
    r = build_report(
        env("needs_clarification", question="Which retry path?"),
        task_id=TASK, owner="Sam",
    )
    assert r.kind is ReportKind.QUESTION
    assert r.kind is not ReportKind.FAILURE
    assert r.is_failure is False
    assert r.confidence is Confidence.BLOCKED_ON_YOU


async def test_ac_pme_13_the_question_is_carried_verbatim():
    q = "Who is cutting over on Friday?"
    r = build_report(env("needs_clarification", question=q), task_id=TASK, owner="Sam")
    assert r.question == q


async def test_ac_pme_13_no_failure_language_on_a_question():
    r = build_report(
        env("needs_clarification", question="q", error="should not appear"),
        task_id=TASK, owner="Sam",
    )
    assert r.kind is ReportKind.QUESTION
    assert "should not appear" not in r.detail, "a question borrowed a failure reason"


async def test_ac_pme_13_only_needs_clarification_maps_to_question():
    for status in ALL_STATUSES:
        kind = KIND_BY_STATUS[status]
        assert (kind is ReportKind.QUESTION) == (status == "needs_clarification")


# ── AC-PME-14 · partial/failed are plain; confidence never rounds up ──────
async def test_ac_pme_14_confidence_never_rounds_up_for_any_status():
    """The mapping is monotone: report confidence never exceeds its status ceiling."""
    for status in ALL_STATUSES:
        r = build_report(env(status), task_id=TASK, owner="Sam")
        assert confidence_rank(r.confidence) <= status_rank(status), status


async def test_ac_pme_14_only_done_reads_as_confident():
    for status in ALL_STATUSES:
        r = build_report(env(status), task_id=TASK, owner="Sam")
        assert (r.confidence is Confidence.CONFIDENT) == (status == "done"), status


async def test_ac_pme_14_partial_reads_as_needs_attention():
    r = build_report(env("partial"), task_id=TASK, owner="Sam")
    assert r.confidence is Confidence.NEEDS_ATTENTION


async def test_ac_pme_14_failed_is_plainly_failed_with_its_reason():
    r = build_report(
        env("failed", error="the retry test never passed"), task_id=TASK, owner="Sam"
    )
    assert r.kind is ReportKind.FAILURE
    assert r.confidence is Confidence.FAILED
    assert "the retry test never passed" in r.detail, "the failure reason was dropped"


async def test_ac_pme_14_unknown_status_fails_safe_not_optimistic():
    r = build_report(env("something_new"), task_id=TASK, owner="Sam")
    assert r.kind is ReportKind.FAILURE
    assert r.confidence is Confidence.FAILED
    assert confidence_rank(r.confidence) == 0


async def test_ac_pme_14_confidence_order_is_total_and_failed_is_lowest():
    ranks = [confidence_rank(c) for c in Confidence]
    assert len(set(ranks)) == len(list(Confidence))
    assert confidence_rank(Confidence.FAILED) == 0
    assert confidence_rank(Confidence.CONFIDENT) == max(ranks)


async def test_ac_pme_14_every_envelope_status_is_mapped():
    for status in ALL_STATUSES:
        assert status in CONFIDENCE_BY_STATUS and status in KIND_BY_STATUS


# ── AC-PME-16 · channel discipline, recipients, four-event cadence ────────
async def test_ac_pme_16_report_goes_to_owner_and_named_recipients():
    r = build_report(
        env("done"), task_id=TASK, owner="Sam", named_recipients=("Priya", "Marcus")
    )
    assert r.recipients == ("Sam", "Priya", "Marcus")


async def test_ac_pme_16_owner_is_not_duplicated_when_also_named():
    r = build_report(env("done"), task_id=TASK, owner="Sam", named_recipients=("Sam",))
    assert r.recipients == ("Sam",)


async def test_ac_pme_16_delivery_uses_only_a_channel_report_channel():
    send = Recorder()
    r = build_report(env("done"), task_id=TASK, owner="Sam")
    res = await deliver([r], channel_report=CARD, send=send)
    assert [c for c, _ in send.sent] == ["draft_card"]
    assert set(res.attempted_channels) <= set(CARD)


async def test_ac_pme_16_only_the_four_events_are_reportable():
    assert REPORTABLE_EVENTS == {
        ReportKind.COMPLETION, ReportKind.QUESTION,
        ReportKind.FAILURE, ReportKind.COST_ASK,
    }
    assert len(REPORTABLE_EVENTS) == 4


async def test_ac_pme_16_intermediate_steps_produce_no_report():
    """Silence means it is running. Only built reports are delivered."""
    send = Recorder()
    res = await deliver([], channel_report=CARD, send=send)
    assert send.sent == [] and res.delivered == []


async def test_ac_pme_16_select_channel_returns_only_listed_channels():
    assert select_channel(("draft_card",)) == "draft_card"
    assert select_channel(()) is None
    assert select_channel(("", "   ")) is None


# ── AC-PME-16-NEG · retries stay listed; nothing duplicates or vanishes ───
@pytest.mark.negative
async def test_ac_pme_16_neg_no_unlisted_channel_is_attempted_on_failure():
    send = Recorder(error=ConnectionResetError("send failed"))
    r = build_report(env("done"), task_id=TASK, owner="Sam")
    res = await deliver([r], channel_report=CARD, send=send)
    assert set(res.attempted_channels) <= set(CARD), "reached for an unlisted channel"
    assert len(res.errors) == 1


@pytest.mark.negative
async def test_ac_pme_16_neg_undeliverable_report_surfaces_on_the_card():
    send = Recorder(error=RuntimeError("down"))
    r = build_report(env("failed", error="x"), task_id=TASK, owner="Sam")
    res = await deliver([r], channel_report=CARD, send=send)
    assert res.surfaced_on_card == [r], "an undeliverable report vanished"
    assert res.delivered == []


@pytest.mark.negative
async def test_ac_pme_16_neg_no_channel_at_all_surfaces_on_the_card():
    send = Recorder()
    r = build_report(env("done"), task_id=TASK, owner="Sam")
    res = await deliver([r], channel_report=(), send=send)
    assert send.sent == [], "a message was sent with no channel available"
    assert res.surfaced_on_card == [r]
    assert res.attempted_channels == []


@pytest.mark.negative
async def test_ac_pme_16_neg_retry_after_a_successful_send_does_not_duplicate():
    send = Recorder()
    r = build_report(env("done"), task_id=TASK, owner="Sam")
    seen: set = set()
    await deliver([r], channel_report=CARD, send=send, already_delivered=seen)
    await deliver([r], channel_report=CARD, send=send, already_delivered=seen)
    assert len(send.sent) == 1, "a retry duplicated a report that already went out"


@pytest.mark.negative
async def test_ac_pme_16_neg_distinct_kinds_for_one_task_are_not_deduped():
    """Idempotency is per (task, kind) — a question and a completion are both real."""
    send = Recorder()
    seen: set = set()
    q = build_report(env("needs_clarification", question="q"), task_id=TASK, owner="Sam")
    d = build_report(env("done"), task_id=TASK, owner="Sam")
    await deliver([q, d], channel_report=CARD, send=send, already_delivered=seen)
    assert len(send.sent) == 2


@pytest.mark.negative
async def test_ac_pme_16_neg_slack_is_not_special_cased_anywhere():
    """P6 deferred: Slack would ride channel-report like any other channel."""
    import pathlib

    src = pathlib.Path(
        "services/harness/src/harness/post_meeting/report.py"
    ).read_text(encoding="utf-8").lower()
    assert "slack_dm" not in src
    assert 'channel == "slack"' not in src
