"""B7 — report. Criteria: AC-PME-13, AC-PME-14, AC-PME-16, AC-PME-16-NEG."""
from __future__ import annotations

import uuid

import pytest
from contracts.channels import ChannelReport
from control_plane.post_meeting.report import (
    CONFIDENCE_BY_STATUS,
    DRAFT_CARD,
    KIND_BY_STATUS,
    REPORTABLE_EVENTS,
    Confidence,
    ReportKind,
    build_report,
    channels_in,
    confidence_rank,
    deliver,
    select_channel,
    status_rank,
)

pytestmark = pytest.mark.asyncio

TASK = uuid.uuid4()
REPORT_NO_DM = ChannelReport(dm_available=False)
REPORT_WITH_DM = ChannelReport(dm_available=True)
CARD = REPORT_NO_DM  # post-close, the card is the surviving surface either way
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
    assert set(res.attempted_channels) <= channels_in(CARD)


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
    """Takes Doc 02's real ChannelReport, not a parallel list-of-strings shape."""
    assert select_channel(REPORT_NO_DM) == DRAFT_CARD
    assert select_channel(REPORT_WITH_DM) == DRAFT_CARD, (
        "a meeting DM is not reachable after the bot has left (§3.6)"
    )
    assert select_channel(REPORT_NO_DM, card_available=False) is None
    # channels_in reports what a card-bearing meeting makes available.
    assert DRAFT_CARD in channels_in(REPORT_NO_DM)
    assert "platform_dm" in channels_in(REPORT_WITH_DM)
    assert "platform_dm" not in channels_in(REPORT_NO_DM)


# ── AC-PME-16-NEG · retries stay listed; nothing duplicates or vanishes ───
@pytest.mark.negative
async def test_ac_pme_16_neg_no_unlisted_channel_is_attempted_on_failure():
    send = Recorder(error=ConnectionResetError("send failed"))
    r = build_report(env("done"), task_id=TASK, owner="Sam")
    res = await deliver([r], channel_report=CARD, send=send)
    assert set(res.attempted_channels) <= channels_in(CARD), "reached for an unlisted channel"
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
    res = await deliver([r], channel_report=CARD, send=send, card_available=False)
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
        "services/control-plane/src/control_plane/post_meeting/report.py"
    ).read_text(encoding="utf-8").lower()
    assert "slack_dm" not in src
    assert 'channel == "slack"' not in src


# ── the card is Doc 08's, not a parallel shape ────────────────────────────
async def test_ac_pme_16_the_draft_card_is_built_by_doc08s_formatter():
    """Doc 07 defines no card shape. build_draft_card hands off to transport.chat.

    The card render and the /m/ accept route must keep reading the SAME typed draft_id
    (CANONICAL §11.5), which only holds if there is one formatter.
    """
    from contracts import DraftCard
    from control_plane.post_meeting.report import build_draft_card

    meeting = uuid.uuid4()
    draft = uuid.uuid4()
    r = build_report(
        env("needs_review", draft_id=draft), task_id=TASK, owner="Sam"
    )
    card = build_draft_card(r, meeting_id=meeting)

    assert isinstance(card, DraftCard), "a parallel card shape was invented"
    assert card.draft_id == draft
    assert f"/m/{meeting}" in card.summary, "the card must link the authenticated home"
    assert "gs://" not in card.summary, "never a raw GCS URI (§2.8)"


async def test_ac_pme_16_a_card_without_a_draft_id_is_a_wiring_error():
    """format_draft_card raises rather than render a click that points at nothing."""
    from control_plane.post_meeting.report import build_draft_card

    r = build_report(env("done"), task_id=TASK, owner="Sam")  # no draft_id
    with pytest.raises(ValueError, match="draft_id"):
        build_draft_card(r, meeting_id=uuid.uuid4())
