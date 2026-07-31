"""Doc 08 · §2.5 — the three V0 screen content modes + §2.4 #12/#13 human activation.

The node ``experience.screen-content-modes`` (``services/control-plane/src/control_plane/
screen_modes.py``) realizes §2.5: the three V0 screen render modes, each a render of
something that ALREADY EXISTS in the workroom envelope —

* **structured-progress-view** — the live plan/step state (Doc 05 ``ProgressEvent`` /
  the terminal ``Envelope`` receipts + status), rendered as a legible step view;
* **pin-to-source** — a citation's ``file:line`` pinned to its source (the ``file:line``
  the envelope carries as a receipt, Law 1 grounding);
* **final-artifact-preview** — the finished artifact (the ``Envelope.artifact`` a landed
  task produced), previewed for the room.

Each mode is rendered ONLY over the transport show-screen verb: it returns a registered
``CanvasPatch`` (``type == 'canvas.patch'``) whose ``patch`` is the JSON render payload —
the same shape ``show_screen(artifact)`` renders (``transport.projector`` §4.5). A mode
NEVER fabricates content absent from the envelope: with no citation there is no
pin-to-source render, with no artifact there is no final-artifact-preview render.

Law 3 (§2.4 #12/#13): verbal-walkthrough and screen-share are gated behind an explicit
human activation and are NEVER auto-started — a render attempted without a human
activation is refused.

Every test drives the REAL module over REAL ``libs.contracts`` ``Envelope`` /
``ProgressEvent`` instances and asserts on REAL registered ``ProxyMessage`` frames.
Product imports live inside the test bodies so the module COLLECTS clean and fails RED
before ``screen_modes.py`` exists.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from libs.contracts import (
    CanvasPatch,
    Envelope,
    ProgressEvent,
    ProxyMessage,
    assert_registry_closed,
)


def _real_progress_events() -> list[ProgressEvent]:
    """Two REAL ``ProgressEvent``s minted at real tool boundaries (Doc 05 §3.12)."""
    task_id = uuid4()
    return [
        ProgressEvent(
            headline="running grep_repo",
            receipts=["grep_repo"],
            task_id=task_id,
        ),
        ProgressEvent(
            headline="edit_file: payments/retry.py",
            receipts=["edit_file payments/retry.py"],
            task_id=task_id,
        ),
    ]


def _real_answer_envelope() -> Envelope:
    """A REAL grounded direct-answer ``Envelope`` carrying a ``file:line`` citation
    receipt — the exact shape ``control_plane.orchestrator._direct_answer_envelope`` builds."""
    return Envelope(
        headline="max attempts is 5",
        detail="the retry cap lives in the config loader",
        receipts=["payments/retry.py:42"],
        status="done",
        verification="verified",
        task_id=uuid4(),
    )


def _real_build_envelope() -> Envelope:
    """A REAL landed-build ``Envelope`` carrying a finished artifact (the diff/files)."""
    return Envelope(
        headline="Built: raise the retry cap to 5",
        detail="changed max_attempts 3 → 5 in the retry loop; two tests green",
        artifact={
            "kind": "diff",
            "files": ["payments/retry.py"],
            "cost": {"total_cost_usd": 0.12},
        },
        receipts=["edit_file payments/retry.py", "run_command pytest -q"],
        status="needs_review",
        verification="unverified",
        draft_id=uuid4(),
        task_id=uuid4(),
    )


# ── the module renders each mode ONLY over the show-screen verb (a CanvasPatch) ────
def test_structured_progress_renders_canvaspatch_from_real_progress() -> None:
    """structured-progress draws the plan/step state from REAL ``ProgressEvent``s and
    renders a registered ``CanvasPatch`` (the show-screen verb frame)."""
    from control_plane.screen_modes import render_structured_progress

    events = _real_progress_events()
    frame = render_structured_progress(events)

    assert isinstance(frame, CanvasPatch)
    assert isinstance(frame, ProxyMessage)  # a REGISTERED instance, not a bare dict
    assert frame.type == "canvas.patch"
    payload = json.loads(frame.patch)
    assert payload["mode"] == "structured-progress"
    # every step label traces to a REAL progress-event headline — nothing fabricated.
    labels = [step["label"] for step in payload["steps"]]
    assert labels == [e.headline for e in events]


def test_pin_to_source_renders_the_real_file_line_citation() -> None:
    """pin-to-source draws the ``file:line`` from a REAL envelope receipt and renders it
    over the show-screen verb — the pin is the exact citation, not a re-derived string."""
    from control_plane.screen_modes import render_pin_to_source

    envelope = _real_answer_envelope()
    frame = render_pin_to_source(envelope)

    assert isinstance(frame, CanvasPatch)
    assert frame.type == "canvas.patch"
    payload = json.loads(frame.patch)
    assert payload["mode"] == "pin-to-source"
    assert payload["file"] == "payments/retry.py"
    assert payload["line"] == 42
    # the pinned file:line is the literal receipt the envelope carried.
    assert "payments/retry.py:42" in envelope.receipts


def test_final_artifact_preview_renders_the_real_finished_artifact() -> None:
    """final-artifact-preview draws the finished artifact from a REAL landed ``Envelope``
    and renders it over the show-screen verb."""
    from control_plane.screen_modes import render_final_artifact_preview

    envelope = _real_build_envelope()
    frame = render_final_artifact_preview(envelope)

    assert isinstance(frame, CanvasPatch)
    assert frame.type == "canvas.patch"
    payload = json.loads(frame.patch)
    assert payload["mode"] == "final-artifact-preview"
    # the previewed artifact IS the envelope's artifact — nothing fabricated.
    assert payload["artifact"] == envelope.artifact


# ── grounded: a mode NEVER fabricates content absent from the envelope (Law 1) ─────
def test_pin_to_source_refuses_when_the_envelope_has_no_citation() -> None:
    """No ``file:line`` citation in the envelope ⇒ NO pin-to-source render (Law 1)."""
    from control_plane.screen_modes import NoBackingContentError, render_pin_to_source

    # an honest abstention envelope: `done` but no citation receipt.
    envelope = Envelope(
        headline="not found by this method",
        receipts=[],
        status="done",
        verification="unverified",
        task_id=uuid4(),
    )
    with pytest.raises(NoBackingContentError):
        render_pin_to_source(envelope)


def test_final_artifact_preview_refuses_when_the_envelope_has_no_artifact() -> None:
    """No artifact in the envelope ⇒ NO final-artifact-preview render (Law 1)."""
    from control_plane.screen_modes import (
        NoBackingContentError,
        render_final_artifact_preview,
    )

    envelope = Envelope(
        headline="max attempts is 5",
        artifact=None,
        receipts=["payments/retry.py:42"],
        status="done",
        verification="verified",
        task_id=uuid4(),
    )
    with pytest.raises(NoBackingContentError):
        render_final_artifact_preview(envelope)


def test_structured_progress_refuses_with_no_progress_events() -> None:
    """No plan/step state at all ⇒ NO structured-progress render (grounded, Law 1)."""
    from control_plane.screen_modes import (
        NoBackingContentError,
        render_structured_progress,
    )

    with pytest.raises(NoBackingContentError):
        render_structured_progress([])


def test_pin_to_source_picks_only_a_real_file_line_receipt_not_a_prose_receipt() -> None:
    """A receipt that is NOT a ``file:line`` (e.g. a tool-run receipt) is never pinned as
    a fabricated source — only a real ``path:line`` receipt becomes the pin."""
    from control_plane.screen_modes import NoBackingContentError, render_pin_to_source

    # a landed-build envelope whose receipts are tool-run lines, NOT file:line citations.
    envelope = Envelope(
        headline="Built: raise the retry cap",
        receipts=["edit_file payments/retry.py", "run_command pytest -q"],
        status="needs_review",
        verification="unverified",
        task_id=uuid4(),
    )
    with pytest.raises(NoBackingContentError):
        render_pin_to_source(envelope)


# ── Law 3: walkthrough / screen-share are gated behind an explicit human activation ─
def test_walkthrough_refuses_without_human_activation() -> None:
    """verbal-walkthrough is OFF by default; rendering it without a human activation is
    refused (never auto-started, §2.4 #12 / Law 3)."""
    from control_plane.screen_modes import (
        NotHumanActivatedError,
        render_final_artifact_preview,
    )

    envelope = _real_build_envelope()
    # requesting an activation-gated render with no human activation → refused.
    with pytest.raises(NotHumanActivatedError):
        render_final_artifact_preview(envelope, activation=None)


def test_human_activated_render_is_allowed() -> None:
    """A render carrying an EXPLICIT human activation is allowed (§2.4 #13)."""
    from control_plane.screen_modes import (
        HumanActivation,
        render_final_artifact_preview,
    )

    envelope = _real_build_envelope()
    activation = HumanActivation(kind="screen_share", requested_by="a participant")
    frame = render_final_artifact_preview(envelope, activation=activation)

    assert isinstance(frame, CanvasPatch)
    payload = json.loads(frame.patch)
    assert payload["mode"] == "final-artifact-preview"


def test_human_activation_rejects_a_non_human_source() -> None:
    """An activation that is not sourced from a human is not a valid activation — Proxy
    never self-promotes to the shared screen (§2.4 #13)."""
    from control_plane.screen_modes import HumanActivation

    with pytest.raises(ValueError):
        HumanActivation(kind="screen_share", requested_by="")


# ── the frames stay registered → assert_registry_closed stays green ────────────────
def test_all_three_modes_emit_registered_frames_registry_closed() -> None:
    """Every mode emits a registered ``CanvasPatch`` and the registry stays closed —
    ``show_screen`` is the sole verb and no new unregistered frame type is introduced."""
    from control_plane.screen_modes import (
        render_final_artifact_preview,
        render_pin_to_source,
        render_structured_progress,
    )

    frames = [
        render_structured_progress(_real_progress_events()),
        render_pin_to_source(_real_answer_envelope()),
        render_final_artifact_preview(_real_build_envelope()),
    ]
    for frame in frames:
        assert isinstance(frame, CanvasPatch)
        # a registered instance round-trips through model_dump (what send() serializes).
        dumped = frame.model_dump(mode="json")
        assert dumped["type"] == "canvas.patch"
    # the contracts registry is still closed — no orphan/unregistered outbound frame.
    assert_registry_closed()


def test_no_internal_names_in_the_rendered_payloads() -> None:
    """No internal component name (Orchestrator/Scribe/workroom) leaks into a rendered
    payload — the render carries product copy only (naming law / §14)."""
    from lint.naming import check_user_visible_strings

    from control_plane.screen_modes import (
        render_final_artifact_preview,
        render_pin_to_source,
        render_structured_progress,
    )

    payloads = {
        "structured-progress": render_structured_progress(_real_progress_events()).patch,
        "pin-to-source": render_pin_to_source(_real_answer_envelope()).patch,
        "final-artifact-preview": render_final_artifact_preview(_real_build_envelope()).patch,
    }
    result = check_user_visible_strings(payloads)
    assert result.exit_code == 0, result.violations
