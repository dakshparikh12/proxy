"""The ``[post_meeting]`` tunables (Doc 07 §4), read from ``config/defaults.toml``.

Delegates the file read to ``libs.db.config.load_defaults`` — the repo already has ONE
loader for this file and re-implementing it here would be the DRY violation CANONICAL
§11.9 names. Only the section accessor lives here.

Every value is a LIMIT or a SWITCH. There is deliberately no key mapping item text to a
tier: tiering is model judgment (Law 4), and a rule table here would be exactly the
"situation→action mapping in code" the law forbids.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Conservative in-code fallbacks, matching config/defaults.toml. Used only when the file
#: is absent or unreadable — never as a silent override of it.
_FALLBACK: dict[str, Any] = {
    "max_concurrent_tasks": 3,
    "max_tasks_per_meeting": 10,
    "task_cost_ceiling": 1.00,
    "plan_expiry": 48,
    "draft_tier_enabled": True,
}


@dataclass(frozen=True)
class PostMeetingConfig:
    """One resolved read of ``[post_meeting]``."""

    max_concurrent_tasks: int = 3
    max_tasks_per_meeting: int = 10
    #: USD per task, checked BEFORE the sandbox spins (§3.5).
    task_cost_ceiling: float = 1.00
    #: Hours a plan waits unanswered before the task closes quietly (§3.4).
    plan_expiry_hours: int = 48
    #: Whether the ticket+plan+draft tier is available at all (§3.1).
    draft_tier_enabled: bool = True


def load_post_meeting_config(raw: dict[str, Any] | None = None) -> PostMeetingConfig:
    """Resolve ``[post_meeting]``. ``raw`` overrides the file read (tests only)."""
    if raw is None:
        try:
            from libs.db.src.db.config import load_defaults

            raw = dict(load_defaults().get("post_meeting", {}))
        except Exception:  # noqa: BLE001 - config must never break the caller
            raw = {}
    merged = {**_FALLBACK, **(raw or {})}
    return PostMeetingConfig(
        max_concurrent_tasks=int(merged["max_concurrent_tasks"]),
        max_tasks_per_meeting=int(merged["max_tasks_per_meeting"]),
        task_cost_ceiling=float(merged["task_cost_ceiling"]),
        plan_expiry_hours=int(merged["plan_expiry"]),
        draft_tier_enabled=bool(merged["draft_tier_enabled"]),
    )
