"""Meeting-harness crash recovery — restart-not-resume.

A crashed harness re-joins the meeting via Recall (a fresh media session) and
replays from the persisted coarse progress; it never resumes the dead media
session and never does fine-grained checkpoint-resume.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from libs.db import Database

MEETING_HARNESS_OP = "meeting-harness"


@dataclass(frozen=True)
class RecoveryPlan:
    rejoin_recall: bool = True
    resume_media_session: bool = False
    checkpoint_resume: bool = False
    replay_from: Any = None
    action: str = "rejoin"


def _replay_offset(progress: Any) -> Any:
    if progress is None:
        return None
    value = progress
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
        return value.get("transcript_offset")
    return None


async def recover_meeting_harness(db: Database, scope_id: str) -> RecoveryPlan:
    """Plan a restart: re-join Recall, replay from persisted progress."""
    row = await db.get_operation_run(scope_id, MEETING_HARNESS_OP)
    progress = row.get("progress") if row is not None else None
    return RecoveryPlan(
        rejoin_recall=True,
        resume_media_session=False,
        checkpoint_resume=False,
        replay_from=_replay_offset(progress),
        action="rejoin",
    )
