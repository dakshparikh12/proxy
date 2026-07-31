"""The ONE inbound handler — ``channel_action`` (§4.4), the human→Proxy gateway seam.

The dispatch funnel (§4.3) hands this handler a message that is ALREADY validated and
tenant-isolated — every entity id was authorized server-side against the connection's
tenant before a line of this ran, and the ``ChannelAction`` model's closed
``surface``/``action`` Literals rejected any out-of-set value centrally. So by the time
a message lands here it is a well-formed, isolated human action; this handler is the
seam where a live fulfilling service binds. With no live service bound (the current
product shape — the in-meeting engine is fed by the meeting webhook drain, not the WS),
an owned, valid frame is a no-op success: the funnel provably ran end-to-end.

A frame missing its structural fields (only reachable when a caller bypasses the
funnel's central validation with a hand-built object) is refused with the SAME generic
``"Not found"`` the funnel uses — no shape/type leak.

This is a **never-throw boundary** (§14 hard rule): any handler error is caught and turned
into a generic refusal; the handler returns, never raises, so a single bad message can
never crash the funnel or leak a stack detail to the wire.
"""
from __future__ import annotations

from typing import Any

_ERR_NOT_FOUND = "Not found"


async def handle_channel_action(conn: Any, msg: Any, ctx: Any) -> None:
    """Handle one validated + isolated ``channel_action`` (§4.4). Never throws.

    The funnel already proved validation + isolation; a structurally sound frame is
    accepted (a no-op success until a live fulfilling service binds on this seam).
    A frame with no ``surface``/``action`` — impossible through the funnel's central
    ``ChannelAction`` validation, so only a bypass shape — draws the generic refusal.
    Any error is caught and returned as a generic refusal — the never-throw tool
    boundary (§14).
    """
    try:
        surface = getattr(msg, "surface", None)
        action = getattr(msg, "action", None)
        if not surface or not action:
            await conn.send_error(_ERR_NOT_FOUND)  # generic; no shape leak
            return
        # No live fulfilling service is bound on this seam (the in-meeting engine is
        # fed by the meeting webhook drain): the owned, valid frame is a no-op success.
    except Exception:  # noqa: BLE001 — never-throw boundary (§14): a bad message can't crash the funnel
        await conn.send_error(_ERR_NOT_FOUND)


__all__ = ["handle_channel_action"]
