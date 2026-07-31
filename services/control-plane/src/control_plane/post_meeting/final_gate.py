"""B8 — the final gate. What lands in the world is a proposed draft, never a push.

Criteria: **AC-PME-15, AC-PME-15-NEG**.

Doc 07 §3.7. The artifact is a staged code-change draft: the multi-file bundle in GCS with
a ``staged_drafts`` row at ``status='proposed'``, a ``draft_id``, and the diff downloadable
as a branch bundle. Doc 04's accept handler records approval and exposes it. **It does not
push.**

This module **stages nothing itself**. Doc 07 §3.8 is explicit that writing ``staged_drafts``
directly is not permitted — staging is ``propose_change`` through the Workroom, as Doc 05
defines it. So what lives here is the *gate*: given what the Workroom returned, decide
whether the task may be recorded as DRAFTED, and refuse anything that looks like a push.

Three properties:

1. **The row must be at ``proposed``.** :func:`validate_draft` rejects any other status,
   including ``needs_review`` — the value four places in Doc 05 wrongly prescribed before
   P8/P8b, and which migration 0011's CHECK now also rejects in the database.
2. **A draft_id without a retrievable bundle is not a draft.** A row whose GCS bundle is
   missing is an orphan, and accepting it would show a human a draft they cannot open.
3. **No push, ever.** :data:`FORBIDDEN_REPO_WRITES` names the operations that must not
   appear, and the gate refuses a result that reports any of them. V1 stages drafts;
   PR creation needs ``contents:write`` and tenant re-consent, declined for V1 (D07.3, F4).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from .models import TaskState

log = logging.getLogger(__name__)

#: CANONICAL §4's staged_drafts enum. The DB enforces this too (migration 0011).
STAGED_DRAFT_STATUSES: frozenset[str] = frozenset(
    {"proposed", "accepted", "rejected", "applied"}
)

#: The ONLY status a freshly staged draft may carry (Doc 07 §3.7, Doc 04 §3.16.1).
PROPOSED = "proposed"

#: Repository writes V1 must never perform (D07.3 / F4 declined).
FORBIDDEN_REPO_WRITES: frozenset[str] = frozenset(
    {"push", "force_push", "create_branch", "open_pull_request", "merge", "commit_to_remote"}
)

#: The GitHub App scope that would be required to push. Its absence is the real guarantee.
FORBIDDEN_SCOPE = "contents:write"


class DraftRejected(Exception):
    """The gate refused to record this artifact. Carries why."""


@dataclass(frozen=True)
class DraftAcceptance:
    draft_id: Any
    status: str
    receipts: tuple[str, ...]


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def validate_draft(
    draft_row: Any,
    *,
    bundle_exists: bool,
    receipts: Sequence[str] = (),
) -> DraftAcceptance:
    """Check a staged draft. Raises :class:`DraftRejected` on anything not clean.

    Deliberately strict about ``needs_review``: it is a valid *envelope* status
    (CANONICAL §1.2) and an invalid *draft-row* status (CANONICAL §4). Accepting it here
    would re-introduce, at runtime, exactly the confusion P8 and P8b removed from the spec.
    """
    if draft_row is None:
        raise DraftRejected("no staged_drafts row was produced")
    draft_id = _field(draft_row, "draft_id")
    if draft_id is None:
        raise DraftRejected("staged draft carries no draft_id")

    status = _field(draft_row, "status")
    if not isinstance(status, str) or status not in STAGED_DRAFT_STATUSES:
        raise DraftRejected(
            f"draft status {status!r} is outside CANONICAL §4's enum "
            f"{sorted(STAGED_DRAFT_STATUSES)}"
        )
    if status != PROPOSED:
        raise DraftRejected(
            f"a freshly staged draft must be at {PROPOSED!r}, not {status!r}"
        )
    if not bundle_exists:
        raise DraftRejected(
            f"draft {draft_id} has no retrievable bundle — an orphan row is not a draft"
        )
    return DraftAcceptance(
        draft_id=draft_id, status=status, receipts=tuple(str(r) for r in receipts)
    )


def assert_no_repo_writes(
    *, operations: Sequence[str] = (), token_scopes: Sequence[str] = ()
) -> None:
    """Refuse anything that reached for the remote. Raises :class:`DraftRejected`.

    Checks both what was *done* and what was *possible*: an operation list containing a
    push is a violation, and so is holding ``contents:write`` at all, because a token that
    can push is the precondition the product promises not to have (§3.7).
    """
    offending = sorted({op for op in operations if op in FORBIDDEN_REPO_WRITES})
    if offending:
        raise DraftRejected(
            f"repository write attempted: {offending}; V1 stages drafts and never pushes"
        )
    if FORBIDDEN_SCOPE in set(token_scopes):
        raise DraftRejected(
            f"token carries {FORBIDDEN_SCOPE!r}; V1 must not hold push capability (D07.3)"
        )


async def run_final_gate(
    *,
    task_id: Any,
    envelope: Any,
    draft_row: Any,
    bundle_exists: bool,
    store: Any,
    operations: Sequence[str] = (),
    token_scopes: Sequence[str] = (),
    cost_usd: Optional[float] = None,
) -> tuple[Optional[DraftAcceptance], Optional[BaseException]]:
    """Record the outcome of a completed task. Never raises.

    A clean draft moves the task to ``DRAFTED``. Anything else records the honest failure
    on the task record — the artifact is not accepted, and the task does NOT report success.
    """
    try:
        assert_no_repo_writes(operations=operations, token_scopes=token_scopes)
        receipts = _field(envelope, "receipts", ()) or ()
        if isinstance(receipts, (str, bytes)):
            receipts = ()
        acceptance = validate_draft(
            draft_row, bundle_exists=bundle_exists, receipts=list(receipts)
        )
    except DraftRejected as exc:
        log.warning("final gate refused the artifact for task %s: %s", task_id, exc)
        try:
            await store.set_outcome(
                task_id, state=TaskState.DISCARDED, outcome=f"draft rejected: {exc}",
                cost_usd=cost_usd,
            )
        except Exception:  # noqa: BLE001 - the refusal stands regardless
            log.exception("could not record draft rejection for %s", task_id)
        return None, exc

    try:
        await store.set_outcome(
            task_id,
            state=TaskState.DRAFTED,
            outcome="staged draft awaiting a human's accept click",
            draft_id=acceptance.draft_id,
            cost_usd=cost_usd,
        )
    except Exception as exc:  # noqa: BLE001 - report plainly
        log.exception("could not record DRAFTED for %s", task_id)
        return acceptance, exc
    return acceptance, None
