"""The in-sandbox MCP server that gives native Claude its ONE connection to the meeting.

This standalone script runs INSIDE the workroom sandbox and is registered with native ``claude``
via ``.mcp.json`` (stdio transport). It exposes exactly one tool, ``to_meeting`` — the agent's ONE
interface to the NON-SPOKEN channels. Speaking is not one of them: the agent speaks by simply
writing its reply, which is streamed to the room (Design B); ``to_meeting`` is only for chat / dm /
screen / offer / mute / unmute / raise_hand. The tool's own docstring is the authoritative vocabulary;
the host-side driver (``meeting_connection``) routes the same mediums (see ``ADVERTISED_MEDIA``).

Two modes, one tool signature (so the agent's behavior is identical either way):

* **proof / simulation** (default): every ``to_meeting`` call is appended as one JSON line to
  ``$PROXY_MEETING_OUT`` (default ``/tmp/to_meeting.jsonl``). The host reads that file to see exactly
  what Proxy chose to communicate, when, and how — proving the agent's dynamic behavior on real
  cal.com data with a simulated meeting, before any live vendor round-trip.
* **live** (``$PROXY_MEETING_RELAY`` set to the host URL): the call is POSTed to the host, which
  lands it on ``MeetingConnection`` (the real Recall/Cartesia send, creds host-side).

The script is intentionally dependency-light (only the ``mcp`` SDK, ``pip install``-ed into the
sandbox at setup) and imports nothing from the workspace, since it runs where the workspace is not.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

_OUT_PATH = os.environ.get("PROXY_MEETING_OUT", "/tmp/to_meeting.jsonl")  # nosec B108 — path INSIDE the isolated per-tenant E2B microVM, not the host
_RELAY_URL = os.environ.get("PROXY_MEETING_RELAY", "").strip()
_RELAY_TOKEN = os.environ.get("PROXY_MEETING_TOKEN", "").strip()


def _record(rec: dict[str, Any]) -> None:
    with open(_OUT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _relay(rec: dict[str, Any]) -> str:
    data = json.dumps(rec).encode("utf-8")
    req = urllib.request.Request(_RELAY_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if _RELAY_TOKEN:
        req.add_header("Authorization", f"Bearer {_RELAY_TOKEN}")
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310  # nosec B310 — fixed host relay URL (https), not attacker-controlled; runs in-sandbox
        return resp.read().decode("utf-8", "replace")  # type: ignore[no-any-return]


def _deliver(content: str, medium: str, to: str) -> str:
    rec = {"ts": time.time(), "content": content, "medium": medium, "to": to}
    if _RELAY_URL:
        try:
            return _relay(rec) or f"delivered via {medium}"
        except Exception as exc:  # noqa: BLE001 — never crash the agent's turn on a send fault
            _record({**rec, "relay_error": str(exc)})
            return f"send via {medium} failed: {exc}"
    _record(rec)
    return f"delivered via {medium}"


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("meeting")

    @server.tool()
    def to_meeting(content: str, medium: str = "chat", to: str = "") -> str:
        """Deliver to the live meeting through a NON-SPOKEN channel. To SPEAK ALOUD, do NOT use this
        tool — just write your reply; your words are spoken to the room live as you type. Use this
        ONLY for the other channels:

        medium: 'chat' (post in the meeting chat) | 'dm' (a private message, needs `to`) | 'screen'
        (show something on the meeting surface — pass a URL OR raw HTML/text content; PREFER content
        you produce, e.g. a rendered doc/mockup/diff, because external sites often refuse to embed
        (X-Frame-Options/CSP) and would show blank. Keep it presentation-ready; pass "" to clear back
        to the orb) | 'offer' (stage a world-touching change for a human's one-click approval) |
        'mute' | 'unmute' | 'raise_hand' (put up a visible "✋ Proxy raised its hand" bar + a quiet
        chat nudge so people know you have something to add — WITHOUT talking over anyone; it clears
        itself the moment you next speak). Use your judgment like a great teammate.
        """
        return _deliver(content, (medium or "chat").strip().lower(), to)

    server.run()


if __name__ == "__main__":
    main()
