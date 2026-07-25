"""libs.http.handlers — the inbound WS message handlers (one per inbound type, §4.1).

The dispatch funnel (§4.3) routes a VALIDATED + ISOLATED message to the exactly-one
handler for its type. The sole inbound client type is ``channel_action`` (§4.4), so this
package holds the one handler: :func:`handlers.channel_action.handle_channel_action`.
"""
from __future__ import annotations

from .channel_action import handle_channel_action as handle_channel_action

__all__ = ["handle_channel_action"]
