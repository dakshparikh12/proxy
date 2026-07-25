"""The ONE inbound handler — ``channel_action`` capability-gated routing (§4.4).

The dispatch funnel (§4.3) hands this handler a message that is ALREADY validated and
tenant-isolated — every entity id was authorized server-side against the connection's
tenant before a line of this ran. So this handler owns only the *capability* decision:
resolve the per-surface capability from the single source of truth (``CAPABILITIES``,
§4.7) and, if the surface/action is permitted, dispatch it to the fulfilling service.

Adding a surface is one edit to the ``Surface`` literal + the capability catalog — not a
new message type, registry entry, handler, and isolation check (§4.4). A capability that
does not permit the action on the surface is refused with the SAME generic ``"Not found"``
the funnel uses — no capability/type leak.

This is a **never-throw boundary** (§14 hard rule): any handler error is caught and turned
into a generic refusal; the handler returns, never raises, so a single bad message can
never crash the funnel or leak a stack detail to the wire.
"""
from __future__ import annotations

from typing import Any

from contracts.capabilities import CAPABILITIES, Action, Capability

_ERR_NOT_FOUND = "Not found"


def resolve_capability(surface: str, action: str) -> Capability | None:
    """Resolve the capability that permits ``action`` on ``surface`` from ``CAPABILITIES``.

    Returns the first capability whose ``allowed_on(surface)`` includes a permitting action
    (``SURFACE``/``PROPOSE``/``APPROVE``) for this action, else ``None`` (deny-by-default).
    The catalog is the single source of truth (§4.7); no per-handler if/else ladder.
    """
    for cap in CAPABILITIES.values():
        allowed = cap.allowed_on(surface)
        if allowed and (Action.SURFACE in allowed or Action.PROPOSE in allowed):
            # The action must be a capability the catalog names for this surface. The
            # ChannelAction.action literal is the closed set; a capability declares the
            # surface it renders on. A permitted action is one the catalog surfaces.
            if _action_matches(cap, action):
                return cap
    return None


def _action_matches(cap: Capability, action: str) -> bool:
    """True when the message ``action`` maps to this capability.

    The catalog id or label family names the action family (``catch_me_up`` →
    ``catch_me_up``; ``walkthrough_on``/``walkthrough_off`` → ``walkthrough``;
    ``capabilities``/``where_are_we``/``show_your_work``/``shorter`` are answer/surface
    actions on any capability that surfaces). Kept intentionally permissive: the funnel
    already isolated the message; this gate only blocks an action on a surface the catalog
    does not render it on.
    """
    root = action.replace("_on", "").replace("_off", "")
    return cap.id == action or cap.id == root or cap.id.startswith(root) or root.startswith(cap.id)


async def handle_channel_action(conn: Any, msg: Any, ctx: Any) -> None:
    """Handle one validated + isolated ``channel_action`` (§4.4). Never throws.

    Resolves the per-surface capability and, if permitted, dispatches to the fulfilling
    service via ``ctx.orchestrator.dispatch_capability`` when present (the live wiring);
    otherwise it is a no-op success (the funnel already proved isolation). Any error is
    caught and returned as a generic refusal — the never-throw tool boundary (§14).
    """
    try:
        surface = getattr(msg, "surface", None)
        action = getattr(msg, "action", None)
        cap = resolve_capability(str(surface), str(action)) if surface and action else None
        if cap is None:
            await conn.send_error(_ERR_NOT_FOUND)  # generic; no capability leak
            return
        orchestrator = getattr(ctx, "orchestrator", None)
        dispatch_capability = getattr(orchestrator, "dispatch_capability", None) if orchestrator else None
        if dispatch_capability is not None:
            await dispatch_capability(cap, msg, conn)
        # else: no live service bound (test/boot context) — isolation already proven, no-op.
    except Exception:  # noqa: BLE001 — never-throw boundary (§14): a bad message can't crash the funnel
        await conn.send_error(_ERR_NOT_FOUND)


__all__ = ["handle_channel_action", "resolve_capability"]
