"""libs.contracts.capabilities — the typed source of truth for *what Proxy can do*.

Doc 08 §4.7 + CANONICAL §6/§12.5. A plain Python module constant
``CAPABILITIES: dict[str, Capability]`` (mirror of the funded sibling's ``AGENTS``
config). Each :class:`Capability` declares its user-facing ``label``, its
:class:`OutputKind`, its allowed :class:`Action` set, a ``renderer`` config (per-surface
UI render only — never a delivery flag), an optional ``service:`` binding to the
wake-behavior / disposition that fulfills it (Doc 04/05), and the surfaces it renders on.

* **The backend imports THIS module directly** — ``allowed_on(surface)`` is the
  authorization primitive the ``channel_action`` handler (§4.4) calls.
* **The UI does NOT import this module.** A build step (``contracts.gen_ui_manifest``)
  generates a small JSON manifest of ``{id, label, output, surfaces}`` only, so the
  internal ``service:`` bindings (``wake:…`` / ``disposition:…``) and the ``renderer``
  config **never reach the browser** — the service-string-in-TS fix (CANONICAL §12.5).
* There is **no runtime ``GET /capabilities`` endpoint**, no dynamic HTTP catalog, and
  no boot-time validator apparatus (all cut in CANONICAL §6). The manifest is generated
  at build, never fetched, so there is no drift path.

``walkthrough`` is a delivery **MODE** (§2.4 #12), owned by §12.3's delivery layer — not
a disposition (CANONICAL §8) and not a delivery flag on this catalog. Its ``service`` is
``None``; the catalog names only its label. When a human turns it on, the worker's
progress events are narrated through Proxy's normal ``speak()`` judgment — zero new
machinery lives here.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class OutputKind(StrEnum):
    """What kind of thing a capability produces (governs its default render)."""

    SPEECH = "speech"  # a spoken headline (voice)
    CHAT = "chat"      # chat detail + receipt + honesty tag
    CANVAS = "canvas"  # tile/screen structured render
    NOTES = "notes"    # a line in the markdown artifact
    DRAFT = "draft"    # a staged-draft card (approve = your click)


class Action(StrEnum):
    """What a capability is permitted to do on a surface (surface/propose/approve)."""

    SURFACE = "surface"  # may show/answer
    PROPOSE = "propose"  # may stage a draft (never apply)
    APPROVE = "approve"  # gated on a human click


# One of the five render channels (voice/chat/tile/canvas/screen). Kept as a plain str
# alias — the closed set is enforced at the funnel (§4.2 `ActionSurface` Literal), not here.
Surface = str


class Capability(BaseModel):
    """One thing Proxy can do — the typed, single-source-of-truth declaration.

    ``allowed_on(surface)`` is the authorization primitive: it returns this capability's
    action set on a surface it declares, and the empty frozenset (deny) on any surface it
    does not — deny-by-default, so an unlisted surface is never accidentally permitted.
    """

    id: str
    label: str                       # user-facing ("Catch me up", "Ask about the code")
    output: OutputKind
    # per-surface UI render config ONLY (tile state, screen mode, receipt on/off) —
    # NOT a delivery flag (delivery is §12.3's job). Never shipped to the browser.
    renderer: dict[str, object] = {}
    actions: frozenset[Action]
    # binding to a wake-behavior / disposition (Doc 04/05); None for a pure delivery mode
    # over an existing stream (e.g. walkthrough). NEVER shipped to the browser.
    service: str | None = None
    surfaces: frozenset[Surface]

    def allowed_on(self, surface: str) -> frozenset[Action]:
        """The actions permitted on ``surface`` — the capability's set if it declares the
        surface, else the empty frozenset (deny-by-default)."""
        return self.actions if surface in self.surfaces else frozenset()


# A plain module constant — imported, not fetched. No runtime endpoint, no boot-validator.
CAPABILITIES: dict[str, Capability] = {
    "answer_grounded": Capability(
        id="answer_grounded",
        label="Ask about the code",
        output=OutputKind.CHAT,
        actions=frozenset({Action.SURFACE}),
        service="wake:answer-question",
        surfaces=frozenset({"voice", "chat", "screen"}),
        renderer={"tile_state": "working", "receipt": True},
    ),
    "catch_me_up": Capability(
        id="catch_me_up",
        label="Catch me up",
        output=OutputKind.SPEECH,
        actions=frozenset({Action.SURFACE}),
        service="wake:catchup",
        surfaces=frozenset({"voice", "chat"}),
    ),
    "build": Capability(
        id="build",
        label="Build or change something",
        output=OutputKind.DRAFT,
        actions=frozenset({Action.PROPOSE, Action.APPROVE}),
        service="disposition:worktree-worker",
        surfaces=frozenset({"screen", "chat", "canvas"}),
        renderer={"tile_state": "working", "draft_card": True},
    ),
    # Walkthrough is NOT a disposition (CANONICAL §8) and NOT a delivery flag on this
    # catalog: it is a delivery MODE (§2.4 #12) owned by §12.3's delivery layer. The
    # catalog names ONLY the capability + its UI label; service is None, no `delivery`
    # remnant. When a human turns it on, the worker's progress events narrate through
    # Proxy's normal speak() judgment.
    "walkthrough": Capability(
        id="walkthrough",
        label="Walk us through it",
        output=OutputKind.SPEECH,
        actions=frozenset({Action.SURFACE}),
        service=None,
        surfaces=frozenset({"voice", "screen"}),
    ),
}
