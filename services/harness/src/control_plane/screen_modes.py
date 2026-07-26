"""V0 shared-screen content modes (08-EXPERIENCE §2.5) + the §2.4 #12/#13 human gate.

The node ``experience.screen-content-modes``. Realizes §2.5: the three V0 screen render
modes, each a render of something that **already exists in the workroom envelope** —
never a fabrication, never a live-pixel sandbox mirror (deferred to Expansion):

  * **structured-progress-view** — the task's own plan/step state (Doc 05
    ``contracts.ProgressEvent`` at each real tool boundary, plus the terminal
    ``contracts.Envelope`` status + receipts) rendered as a legible step view;
  * **pin-to-source** — a citation's ``file:line`` pinned to its source. The
    ``file:line`` is the exact receipt the grounded answer envelope carried
    (``harness.orchestrator._direct_answer_envelope`` writes ``receipts=[citation]``,
    Law 1); a prose/tool-run receipt is NOT a source and is never pinned;
  * **final-artifact-preview** — the finished artifact a landed task produced
    (``Envelope.artifact`` — the diff/report/files), previewed for the room.

**Rendered ONLY over the transport show-screen verb.** Each mode returns a registered
``contracts.CanvasPatch`` (``type == 'canvas.patch'``) whose ``patch`` is the JSON render
payload — the SAME shape ``show_screen(artifact)`` produces on the live path
(``transport.projector`` §4.5: a ``show_screen`` TOOL_USE → ``CanvasPatch(patch=json)``).
The render is a **structured, non-pixel descriptor** (like ``transport.canvas.LiveWorkView``);
Doc 08's renderer turns it into pixels. No provider SDK, no network, no live sandbox mirror.

**Grounded (Law 1).** A mode with no backing content in the envelope does not render — it
raises :class:`NoBackingContentError`. No citation ⇒ no pin-to-source; no artifact ⇒ no
final-artifact-preview; no progress state ⇒ no structured-progress. A render therefore can
only ever carry content the envelope actually holds.

**Human-activated (Law 3 / §2.4 #12,#13).** verbal-walkthrough and screen-share are OFF by
default and NEVER auto-started. A render behind that gate requires an explicit
:class:`HumanActivation` sourced from a human — Proxy never self-promotes to the shared
screen. The default (``activation=None`` unset) is the always-allowed content render invoked
BY a human's ask; passing ``activation=None`` explicitly at a gated call site is refused
with :class:`NotHumanActivatedError`. The activation object itself refuses a non-human
source, so it can never be minted for an auto-push.

Copy carries no internal component name (naming law / §14): payload strings are product
copy drawn from the envelope's own headline/receipts, never an internal component label.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from libs.contracts import CanvasPatch, Envelope, ProgressEvent

# A ``file:line`` citation receipt: a path (not a bare tool name / prose) ending ``:<line>``.
# The path must contain a ``/`` or a filename-like ``.<ext>`` so a prose receipt
# ("run_command pytest -q") never masquerades as a source pin (Law 1 grounding).
_FILE_LINE_RX = re.compile(r"^(?P<file>(?:[^\s:]*[/.][^\s:]*)):(?P<line>\d+)$")

#: The activation-gated content modes (§2.4 #12/#13): a screen-share of the finished
#: artifact and the verbal walkthrough are turned on ONLY by a human. A render behind this
#: gate must carry a :class:`HumanActivation`.
ActivationKind = Literal["screen_share", "verbal_walkthrough"]


class _Unset(Enum):
    """A typed sentinel distinguishing "no activation arg passed" (an ungated content
    render invoked BY a human's ask) from an explicit ``activation=None`` at a gated call
    site (walkthrough/screen-share), which is refused. An Enum singleton is mypy-typeable,
    so the gate check narrows cleanly under ``--strict``."""

    UNSET = object()


_UNSET = _Unset.UNSET


class NoBackingContentError(Exception):
    """A mode was asked to render content the envelope does not hold (Law 1).

    No ``file:line`` citation ⇒ no pin-to-source; no artifact ⇒ no final-artifact-preview;
    no progress state ⇒ no structured-progress. Rendering fabricated/absent content is the
    node's hard NOT-done, so the mode refuses rather than invent a screen.
    """


class NotHumanActivatedError(Exception):
    """A gated render (walkthrough / screen-share) was attempted without a human's
    activation (§2.4 #12,#13 / Law 3). The screen is never auto-pushed."""


@dataclass(frozen=True)
class HumanActivation:
    """An explicit human activation of a gated screen mode (§2.4 #12,#13).

    Proxy never self-promotes to the shared screen: an activation is valid ONLY when it
    names a real human requester (a command, an implied ask, or a yes to Proxy's offer).
    An empty ``requested_by`` is rejected so an activation can never be minted for an
    auto-push. ``kind`` is one of the two gated modes.
    """

    kind: ActivationKind
    requested_by: str

    def __post_init__(self) -> None:
        if not str(self.requested_by).strip():
            raise ValueError(
                "a screen activation must name the human who requested it "
                "(the screen is never self-initiated)"
            )


def _canvas_patch(payload: dict[str, Any]) -> CanvasPatch:
    """Wrap a structured render payload as the show-screen-verb frame (a ``CanvasPatch``).

    This is the exact frame ``show_screen(artifact)`` renders on the live path
    (``transport.projector`` serializes the ``artifact`` dict to the capped ``patch``
    JSON string); rendering the mode here through the same frame keeps ONE screen render
    shape across the reactive turn and the host-driven modes.
    """
    return CanvasPatch(patch=json.dumps(payload, default=str))


def _require_human_activation(activation: HumanActivation | None | _Unset) -> None:
    """Fail-closed on a gated render with no human activation (§2.4 #12,#13 / Law 3).

    ``_UNSET`` (no activation arg) is an ungated content render invoked BY a human's ask
    and is allowed. An explicit ``None`` at a gated call site (walkthrough/screen-share)
    is refused — the screen is never auto-pushed. A real :class:`HumanActivation` passes.
    """
    if activation is None:
        raise NotHumanActivatedError(
            "walkthrough / screen-share is human-activated only — the screen is never "
            "auto-pushed; supply a HumanActivation to render it"
        )


def render_structured_progress(
    events: Sequence[ProgressEvent],
    envelope: Envelope | None = None,
    *,
    activation: HumanActivation | None | _Unset = _UNSET,
) -> CanvasPatch:
    """Render the **structured-progress-view** (§2.5) from the REAL plan/step state.

    Draws each step from a REAL ``contracts.ProgressEvent`` (minted at a real tool
    boundary, Doc 05 §3.12) — the step label IS the event headline, nothing fabricated.
    When the terminal ``envelope`` is supplied its ``status`` (and the verifier result)
    caps the view. With no progress state at all it refuses (Law 1).

    ``activation`` is a sentinel by default (this content render is itself invoked by a
    human's ask, §2.5); pass an explicit :class:`HumanActivation` / ``None`` only at a
    gated call site (walkthrough/screen-share), where ``None`` is refused.
    """
    _require_human_activation(activation)
    if not events:
        raise NoBackingContentError(
            "no progress events to render: refusing to fabricate a progress view"
        )
    steps = [{"label": event.headline, "done": False} for event in events]
    if steps:  # the latest boundary reached is the current step; earlier ones are done.
        for step in steps[:-1]:
            step["done"] = True
    payload: dict[str, Any] = {"mode": "structured-progress", "steps": steps}
    if envelope is not None:
        payload["status"] = envelope.status
        if envelope.verification is not None:
            payload["verification"] = envelope.verification
    return _canvas_patch(payload)


def render_pin_to_source(
    envelope: Envelope,
    *,
    activation: HumanActivation | None | _Unset = _UNSET,
) -> CanvasPatch:
    """Render **pin-to-source** (§2.5): the citation's ``file:line`` pinned to its source.

    The ``file:line`` is the EXACT receipt the grounded answer envelope carried
    (``harness.orchestrator`` writes ``receipts=[citation]`` for a grounded direct answer,
    Law 1). A receipt that is not a real ``path:line`` (a tool-run / prose receipt) is
    never pinned as a fabricated source. With no ``file:line`` receipt it refuses.
    """
    _require_human_activation(activation)
    for receipt in envelope.receipts:
        match = _FILE_LINE_RX.match(receipt.strip())
        if match is not None:
            payload: dict[str, Any] = {
                "mode": "pin-to-source",
                "file": match.group("file"),
                "line": int(match.group("line")),
                "citation": receipt.strip(),
            }
            return _canvas_patch(payload)
    raise NoBackingContentError(
        "no file:line citation in the envelope: refusing to fabricate a pinned source"
    )


def render_final_artifact_preview(
    envelope: Envelope,
    *,
    activation: HumanActivation | None | _Unset = _UNSET,
) -> CanvasPatch:
    """Render **final-artifact-preview** (§2.5): the finished artifact for the room to read.

    Draws the artifact straight from ``Envelope.artifact`` (the landed diff/report/files a
    task produced) — the preview IS the envelope's artifact, nothing fabricated. With no
    artifact it refuses (Law 1). ``headline`` rides along so the room reads the payoff in
    context; it is the envelope's OWN headline (product copy, no internal name).
    """
    _require_human_activation(activation)
    if not envelope.artifact:
        raise NoBackingContentError(
            "no artifact in the envelope: refusing to fabricate a final-artifact preview"
        )
    payload: dict[str, Any] = {
        "mode": "final-artifact-preview",
        "headline": envelope.headline,
        "artifact": envelope.artifact,
        "status": envelope.status,
    }
    if envelope.detail is not None:
        payload["detail"] = envelope.detail
    if envelope.verification is not None:
        payload["verification"] = envelope.verification
    return _canvas_patch(payload)


__all__ = [
    "ActivationKind",
    "HumanActivation",
    "NoBackingContentError",
    "NotHumanActivatedError",
    "render_final_artifact_preview",
    "render_pin_to_source",
    "render_structured_progress",
]
