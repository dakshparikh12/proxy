"""Shared doubles for the Doc 07 suite.

The fakes here stand in for the DURABLE substrate at the unit rung only. Per the sealed
bundle's ``mock_boundary`` for ``db:postgres`` ("real Postgres only; no in-memory
substitute for the integration tier"), these MUST NOT be used to satisfy an
``integration`` or ``negative`` rung — those rungs drive a real database and are marked
``integration`` so they skip cleanly on a host without one.

Nothing here is permissive: a fake that quietly succeeds where the real thing would fail
would invert the criterion it is meant to prove. The sandbox and git doubles below count
calls and raise on use, so a test asserting "zero sandboxes started" fails loudly if the
code under test ever reaches for one.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from control_plane.post_meeting.models import TaskRecord, TaskState, Tier


class FakeTaskStore:
    """In-memory ``post_meeting_tasks``, including the two database guards.

    The guards are re-implemented here on purpose: migration 0009 enforces them in
    Postgres, and these fakes enforce the same rules so the unit rung proves the
    application never *attempts* an illegal write. The integration rung proves the
    database rejects it even if the application does.
    """

    def __init__(self) -> None:
        self.rows: dict[Any, dict[str, Any]] = {}
        self.tables_written: set[str] = set()
        self.insert_error: Optional[BaseException] = None

    # ── writes ────────────────────────────────────────────────────────────
    async def insert_task(self, task: TaskRecord) -> Any:
        if self.insert_error is not None:
            raise self.insert_error
        # Guard 2, INSERT arm (migration 0009): a row cannot be born RUNNING.
        if _val(task.state) == "RUNNING":
            raise ValueError(
                "post_meeting_tasks: cannot INSERT a row directly at RUNNING"
            )
        tid = uuid.uuid4()
        self.rows[tid] = {
            "task_id": tid,
            "tenant_id": task.tenant_id,
            "meeting_id": task.meeting_id,
            "source": _val(task.source),
            "item_ref": task.item_ref,
            "tier": _val(task.tier),
            "owner": task.owner,
            "state": _val(task.state),
            "plan": None,
            "approved_by": None,
            "approved_at": None,
            "operation_ref": None,
            "draft_id": None,
            "cost_usd": 0.0,
            "outcome": None,
            "planned_at": None,
        }
        self.tables_written.add("post_meeting_tasks")
        return tid

    async def set_tier(self, task_id: Any, tier: Tier, *, state: TaskState) -> None:
        self._update(task_id, tier=_val(tier), state=_val(state))

    async def set_state(self, task_id: Any, state: TaskState) -> None:
        self._update(task_id, state=_val(state))

    async def set_plan(self, task_id: Any, plan: str, *, state: TaskState) -> None:
        # Mirrors the real store: writing a plan stamps the §3.4 expiry clock.
        self._update(task_id, plan=plan, state=_val(state), planned_at=_now())

    async def downgrade_to_ticket(self, task_id: Any, *, outcome: str) -> None:
        self._update(
            task_id, tier=Tier.TICKET.value, state=TaskState.TRIAGED.value,
            plan=None, planned_at=None, outcome=outcome,
        )

    async def planned_tasks_for_sweep(self, *, tenant_id: Any = None) -> list[dict[str, Any]]:
        return [
            {"task_id": tid, "state": r["state"], "planned_at": r.get("planned_at")}
            for tid, r in self.rows.items()
            if r["state"] == "PLANNED" and (tenant_id is None or r["tenant_id"] == tenant_id)
        ]

    async def approve(self, task_id: Any, *, approved_by: str, approved_at: Any) -> None:
        self._update(
            task_id, state="APPROVED", approved_by=approved_by, approved_at=approved_at
        )

    async def set_operation_ref(self, task_id: Any, operation_ref: Any) -> None:
        self._update(task_id, operation_ref=operation_ref)

    async def set_outcome(
        self,
        task_id: Any,
        *,
        state: TaskState,
        outcome: str,
        draft_id: Any = None,
        cost_usd: Optional[float] = None,
    ) -> None:
        patch: dict[str, Any] = {"state": _val(state), "outcome": outcome}
        if draft_id is not None:
            patch["draft_id"] = draft_id
        if cost_usd is not None:
            patch["cost_usd"] = cost_usd
        self._update(task_id, **patch)

    # ── reads ─────────────────────────────────────────────────────────────
    async def get(self, task_id: Any) -> Optional[dict[str, Any]]:
        row = self.rows.get(task_id)
        return dict(row) if row else None

    async def task_id_for_draft(self, draft_id: Any) -> Optional[Any]:
        for tid, r in self.rows.items():
            if r["draft_id"] == draft_id:
                return tid
        return None

    async def count_running_for_tenant(self, tenant_id: Any) -> int:
        return sum(
            1
            for r in self.rows.values()
            if r["tenant_id"] == tenant_id and r["state"] == "RUNNING"
        )

    async def count_dispatchable_for_meeting(
        self, meeting_id: Any, *, exclude_task_id: Any = None
    ) -> int:
        """Mirrors the real SQL: dispatchable tier, non-terminal state, candidate excluded."""
        from control_plane.post_meeting.models import DISPATCHABLE_TIERS, TERMINAL_STATES

        tiers = {t.value for t in DISPATCHABLE_TIERS}
        terminal = {s.value for s in TERMINAL_STATES}
        return sum(
            1
            for tid, r in self.rows.items()
            if r["meeting_id"] == meeting_id
            and r["tier"] in tiers
            and r["state"] not in terminal
            and tid != exclude_task_id
        )

    # ── the two database guards from migration 0009 ───────────────────────
    def _update(self, task_id: Any, **patch: Any) -> None:
        row = self.rows[task_id]
        new_state = patch.get("state", row["state"])
        approved_by = patch.get("approved_by", row["approved_by"])
        approved_at = patch.get("approved_at", row["approved_at"])

        # Guard 1: CHECK state <> 'APPROVED' OR (approved_by AND approved_at NOT NULL).
        if new_state == "APPROVED" and (approved_by is None or approved_at is None):
            raise ValueError(
                "post_meeting_tasks_approved_needs_approver: APPROVED requires "
                "approved_by and approved_at"
            )
        # Guard 2, UPDATE arm: a real transition INTO running requires APPROVED.
        if (
            new_state == "RUNNING"
            and row["state"] != "RUNNING"
            and row["state"] != "APPROVED"
        ):
            raise ValueError(
                f"post_meeting_tasks: RUNNING may only be entered from APPROVED "
                f"(was {row['state']})"
            )
        row.update(patch)
        self.tables_written.add("post_meeting_tasks")


class FakeClarifyStore:
    """In-memory ``clarify_items`` — the one table writable before APPROVED."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.insert_error: Optional[BaseException] = None
        self.tables_written: set[str] = set()

    async def insert(
        self,
        *,
        tenant_id: Any,
        meeting_id: Any,
        question: str,
        kind: Optional[str] = None,
        blocking_ref: Optional[str] = None,
        urgency: Optional[str] = None,
    ) -> Any:
        if self.insert_error is not None:
            raise self.insert_error
        cid = uuid.uuid4()
        self.rows.append(
            {
                "clarify_id": cid,
                "tenant_id": tenant_id,
                "meeting_id": meeting_id,
                "question": question,
                "kind": kind,
                "blocking_ref": blocking_ref,
                "urgency": urgency,
                "answer": None,
                "answered_by": None,
            }
        )
        self.tables_written.add("clarify_items")
        return cid

    async def pending_for_meeting(self, meeting_id: Any) -> list[dict[str, Any]]:
        return [
            r for r in self.rows if r["meeting_id"] == meeting_id and r["answer"] is None
        ]


class ForbiddenSandbox:
    """A sandbox provider that must never be called.

    NOT a permissive stub: it counts and raises. AC-PME-07/-12 assert zero sandboxes start
    before approval or before a cost answer, and a stub that quietly returned a handle
    would make those criteria pass while the product violated them.
    """

    def __init__(self) -> None:
        self.call_count = 0

    async def start(self, *args: Any, **kwargs: Any) -> Any:
        self.call_count += 1
        raise AssertionError(
            "sandbox provisioned when the criterion forbids it (Doc 07 §3.4/§3.5)"
        )


class ForbiddenGitRemote:
    """A git remote that records write attempts and refuses them (AC-PME-15)."""

    def __init__(self) -> None:
        self.write_operations = 0

    def push(self, *args: Any, **kwargs: Any) -> Any:
        self.write_operations += 1
        raise AssertionError("push attempted; Doc 07 §3.7 stages drafts and never pushes")

    create_branch = push
    open_pull_request = push


# ── close-output doubles ──────────────────────────────────────────────────
@dataclass
class FakeActionItem:
    text: str
    owner: Optional[str] = None
    due: Optional[str] = None


@dataclass
class FakeFinalNotes:
    """Structurally what B1 reads off ``scribe.close.FinalNotes``."""

    summary: str = "s"
    action_items: list[Any] = field(default_factory=list)
    decisions: list[Any] = field(default_factory=list)
    open_questions: list[Any] = field(default_factory=list)


def _now() -> Any:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _val(v: Any) -> Any:
    return v.value if hasattr(v, "value") else v


def assert_no_code_reference(path: str, tokens: tuple[str, ...]) -> None:
    """Assert a module contains no CODE reference to any of ``tokens``.

    Deliberately AST-based rather than a substring grep over the source. Prose mentions a
    forbidden thing all the time — triage.py's seat comment says "WORKROOM is the
    sandboxed builder" — and a grep that fails on a comment is a test that punishes
    documentation. What matters is whether the module can *reach* the thing, so this
    inspects imports, identifiers, attribute names, and string literals used as code
    (call arguments), and ignores comments and docstrings.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen.update(a.name.lower() for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            seen.add((node.module or "").lower())
            seen.update(a.name.lower() for a in node.names)
        elif isinstance(node, ast.Name):
            seen.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            seen.add(node.attr.lower())
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A bare string constant is only "code" if it is not a docstring; docstrings
            # are Expr statements, which we skip by only taking strings inside calls.
            pass
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for arg in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    seen.add(arg.value.lower())

    for token in tokens:
        hits = sorted(s for s in seen if token in s)
        assert not hits, f"{path} reaches for {token!r} in code: {hits}"
