"""Invite + bot-id resolution — a meeting bound to (tenant, repo, pinned_sha).

Inviting Proxy creates a meetings row bound to (tenant, repo, pinned_sha=HEAD)
and, once the Recall bot launches, binds the bot_id back onto the row. A webhook
resolves its bot_id → meeting → (tenant, repo).

The invite path launches a REAL Recall bot through the ``TransportProvider`` seam
(the sole one is ``transport.recall.RecallTransport``, bound to the funded
``libs.http.call_external`` funnel): it drives the ``JoinSession`` FSM — join from
the link alone, write the id Recall actually returns back onto the row, and post
the consent notice pinned as the FIRST observable action (AC-JOIN-01/10/15). No id
is ever fabricated; a join/consent failure surfaces plainly, never a false success.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from transport.join import JoinSession, JoinSource
from transport.recall import RecallTransport
from transport.seams import TransportProvider

from libs.db import Database, repos


def _output_media_url_for(meeting_id: str) -> str:
    """The PER-MEETING Output-Media webpage URL the bot streams as its camera:
    ``<origin>/output-media/<meeting_id>`` — the exact page ``in_meeting.output_media``
    serves and the speak path writes PCM into for THIS meeting.

    The origin comes from ``RECALL_OUTPUT_MEDIA_URL`` (treated as an origin — any
    ``/output-media`` path or trailing slash it carries is stripped), falling back to
    ``PUBLIC_BASE_URL`` (the same origin the relay URL is built from). Without a
    meeting_id in the path the served page attaches to an EMPTY channel and every
    spoken frame is dropped (the bot joins silent) — so the id must ride here. An
    unset origin ⇒ "" (honest degrade: no Output-Media surface, mirrors the prior
    empty-URL behavior). Pure string physics (Law 4), never baked in."""
    import os

    origin = (os.environ.get("RECALL_OUTPUT_MEDIA_URL", "") or "").strip()
    if origin:
        # Treat a configured value as an ORIGIN even if it points at the bare page: strip a
        # trailing ``/output-media`` (with or without a meeting segment) and any trailing slash.
        origin = origin.rstrip("/")
        marker = "/output-media"
        idx = origin.find(marker)
        if idx != -1:
            origin = origin[:idx]
    if not origin:
        origin = (os.environ.get("PUBLIC_BASE_URL", "") or "").strip()
    origin = origin.rstrip("/")
    if not origin:
        return ""
    return f"{origin}/output-media/{meeting_id}"


def _default_transport(meeting_id: str = "") -> TransportProvider:
    """The product's real ``TransportProvider``: the sole ``RecallTransport`` impl,
    bound to the funded ``libs.http.call_external`` funnel (retry + cost telemetry,
    AC-XCUT-03) and the ``RECALL_API_KEY`` sourced from Secret Manager via settings.

    This is the ONE construction site for ``RecallTransport`` in the product — the
    invite path launches a real bot through it when no transport is injected. When a
    ``meeting_id`` is given the Output-Media camera URL is made PER-MEETING
    (``<origin>/output-media/<meeting_id>``) so the bot loads the page whose channel
    the speak path writes into — without it the page has no channel and the bot is
    silent. An empty ``meeting_id`` keeps the room-verb-only construction (the meeting
    connection's room sink needs no camera URL).
    """
    import os

    from libs.http.src.http.external import call_external

    # RECALL_API_KEY is surfaced from Secret Manager as env (the settings boot gate
    # validates its presence at startup); read it here without re-running that gate.
    # RECALL_WEBHOOK_URL (our public /webhooks/recall receiver) and
    # RECALL_OUTPUT_MEDIA_URL (the origin the bot streams as its camera) are
    # deployment facts fed the same way: they drive the full create-bot config
    # (streaming transcription + realtime transcript delivery + Output Media) on
    # the real join path — unset, the bot joins without those capabilities.
    api_key = os.environ.get("RECALL_API_KEY", "")
    output_media_url = _output_media_url_for(meeting_id) if meeting_id else ""
    return RecallTransport(
        call_external,
        api_key=api_key,
        webhook_url=os.environ.get("RECALL_WEBHOOK_URL", ""),
        output_media_url=output_media_url,
    )


@dataclass(frozen=True)
class InvitedMeeting:
    id: Any
    tenant_id: Any
    repo_id: Any
    pinned_sha: Any
    recall_bot_id: str
    notice_posted: bool = False


async def invite_proxy(
    db: Database,
    *,
    tenant_id: Any,
    repo_id: Any,
    meeting_url: str,
    head_sha: str,
    transport: TransportProvider | None = None,
    source: JoinSource = JoinSource.LINK,
) -> InvitedMeeting:
    """Create the meeting, launch a REAL Recall bot, bind the id Recall returns.

    The meetings row is created first (bound to tenant/repo/pinned_sha=HEAD) with a
    null ``recall_bot_id``; the ``JoinSession`` then launches the bot via
    ``transport.join(meeting_url)`` and, on launch, writes the REAL bot id back onto
    the row (AC-JOIN-10) — never a synthetic ``recall-bot-<uuid>``. The consent
    notice posts pinned as the first observable action before observation begins
    (AC-JOIN-03/15). A join or consent-post failure raises with the honest reason
    (Law 2) — no false joined/posted state is ever recorded.
    """
    async with db.acquire() as conn:
        row = await repos.meetings.insert_meeting(
            conn,
            tenant_id=tenant_id,
            repo_id=repo_id,
            meeting_url=meeting_url,
            pinned_sha=head_sha,
            recall_bot_id=None,  # written back once the real bot launches
            status="live",
            platform=_platform_for_url(meeting_url),  # set at join (CANONICAL §11.1)
        )
    meeting_id = row["id"]

    if transport is None:
        # Per-meeting transport: the Output-Media camera URL carries THIS meeting_id
        # (``/output-media/<id>``) so the bot loads the page whose channel the speak path
        # writes into — without the id the page has no channel and the bot joins silent.
        transport = _default_transport(meeting_id=str(meeting_id))

    async def _write_back(bot_id: str) -> None:
        async with db.acquire() as conn:
            await repos.meetings.update_bot_id(
                conn, meeting_id=meeting_id, recall_bot_id=bot_id
            )

    session = JoinSession(transport, on_bot_launched=_write_back)
    result = await session.join(meeting_url, source=source)
    if result.failed or result.bot_id is None:
        # Honest failure — never a false 'joined'/'consent posted' (AC-JOIN-16, Law 2).
        raise RuntimeError(result.reason or "join failed")

    return InvitedMeeting(
        id=meeting_id,
        tenant_id=row["tenant_id"],
        repo_id=row["repo_id"],
        pinned_sha=row["pinned_sha"],
        recall_bot_id=result.bot_id,
        notice_posted=result.notice_posted,
    )


def _platform_for_url(meeting_url: str | None) -> str:
    """Derive the meeting platform (recall|zoom|teams|meet) from the meeting URL.

    Recall brokers Meet/Zoom/Teams behind one API with no per-platform branch on the join
    path (AC-JOIN-09); the underlying platform is a property of the URL, recorded on the
    ``meetings`` row (CANONICAL §11.1). An unrecognised URL is honestly reported as the
    transport broker ``'recall'`` (the actual bot host) rather than a fabricated platform —
    never a false 'zoom'/'teams'. Kept as pure string physics (Law 4): the mapping is a
    URL fact, not a judgement.
    """
    url = (meeting_url or "").lower()
    if "zoom.us" in url or "zoom.com" in url:
        return "zoom"
    if "teams.microsoft.com" in url or "teams.live.com" in url:
        return "teams"
    if "meet.google.com" in url:
        return "meet"
    return "recall"


async def resolve_bot_id(db: Database, recall_bot_id: str) -> dict[str, Any] | None:
    """Resolve a Recall bot_id back to its meeting (→ tenant + repo)."""
    async with db.acquire() as conn:
        row = await repos.meetings.get_by_bot_id(conn, recall_bot_id)
    if row is None:
        return None
    return {"meeting_id": row["id"], "tenant_id": row["tenant_id"], "repo_id": row["repo_id"]}
