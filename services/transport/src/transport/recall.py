"""The Recall.ai carrier — one implementation of ``TransportProvider`` (§2, §3.1).

Recall gives bot join + per-speaker audio + Output Media + chat + roster/status
webhooks across Meet/Zoom/Teams behind a single API, so zero per-platform code lives
here. This is the sole ``TransportProvider`` impl; callers depend only on the Protocol
(AC-SEAM-01). Every outbound round-trip is issued through the injected ``call_external``
seam (AC-XCUT-03) — no raw provider client is held in this package. Live roster/chat
events arrive on in-process queues fed by the harness webhook layer (Doc 02 M2); the
carrier to the Orchestrator stays an in-process ``asyncio`` path (AC-SEAM-07).
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from contracts.channels import ChannelReport

from .external import CallExternal
from .media import AudioChunk, CanvasFrame
from .seams import OutputMediaSink
from .signals import ChatMessage, RosterEvent

# Recall rate card (managed V0, §4). Home for the accrual constant is config; this
# per-call unit is the telemetry hint passed to the seam.
_RECALL_BASE = "https://api.recall.ai/api/v1"
# Per-round-trip client timeout (seconds) — matches the workspace seam convention
# (premeeting's token mint); the retry policy above this lives in ``call_external``.
_HTTP_TIMEOUT_S = 15.0
# The realtime transcript event names Recall's create-bot schema enumerates for a
# ``recording_config.realtime_endpoints`` webhook (finals + partials) — protocol
# identifiers (wire physics), consumed by the harness webhook drain under the same
# names. Bot STATUS events are NOT subscribable here: Recall delivers those only
# through the account webhook configured in its dashboard (the §4.6 route verified
# by ``recall_webhook_secret``), never per-bot.
_REALTIME_TRANSCRIPT_EVENTS = ("transcript.data", "transcript.partial_data")


#: The transport's bound ``_api`` round-trip — (method, path, body) → parsed JSON body.
_ApiCall = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]


class _RecallOutputMedia:
    """Output-Media sink: small-chunk audio + canvas frames into the call (§3.3/§3.5).

    Every write is a REAL Recall round-trip through the transport's ``_api`` (the sole
    raw-HTTP home), issued via the injected ``call_external`` seam (AC-XCUT-03). The
    wire shapes are Recall's real output endpoints, confirmed against the live docs:

    * audio — POST ``/bot/{id}/output_audio/`` with ``{"kind": "mp3", "b64_data":
      <base64 of the chunk's exact bytes>}``; ``mp3`` is the ONLY ``kind`` the schema
      allows, the bot must carry ``automatic_audio_output``, and the endpoint is
      rate-limited 300 req/min/workspace — it is Recall's clip path, so the sustained
      conversational leg stays on the Output Media webpage surface (§3.3).
    * stop  — DELETE ``/bot/{id}/output_audio/`` (204, no body): kills any in-flight
      audio — Recall's real mechanism behind ``flush`` (barge-in) and ``mute``.
    * video — POST ``/bot/{id}/output_video/`` with ``{"kind": "jpeg", "b64_data":
      <base64 of the frame's exact bytes>}``; ``jpeg`` is the only ``kind`` allowed.

    While the bot is muted (C5) every audio write is suppressed sink-side — zero wire
    calls — until unmute lifts it. The mute state lives on the transport, observed live
    through ``is_muted``, so a sink created before ``mute()`` still honors it.
    """

    def __init__(
        self,
        call_external: CallExternal,
        bot_id: str,
        api: _ApiCall | None = None,
        is_muted: Callable[[], bool] | None = None,
    ) -> None:
        self._call_external = call_external
        self._bot_id = bot_id
        self._api = api
        self._is_muted = is_muted

    async def write_audio(self, chunk: AudioChunk) -> None:
        if self._is_muted is not None and self._is_muted():
            return  # muted: output-audio suppression — nothing rides the wire (C5)
        body = {"kind": "mp3", "b64_data": base64.b64encode(chunk.pcm).decode("ascii")}
        await self._call_external(
            lambda: self._via_api("POST", f"/bot/{self._bot_id}/output_audio/", body),
            service="recall",
        )

    async def flush(self) -> None:
        await self._call_external(
            lambda: self._via_api("DELETE", f"/bot/{self._bot_id}/output_audio/", {}),
            service="recall",
        )

    async def write_frame(self, frame: CanvasFrame) -> None:
        body = {"kind": "jpeg", "b64_data": base64.b64encode(frame.data).decode("ascii")}
        await self._call_external(
            lambda: self._via_api("POST", f"/bot/{self._bot_id}/output_video/", body),
            service="recall",
        )

    async def _via_api(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        # The real round-trip lives on the transport's ``_api`` (the sole raw-HTTP
        # home). An unbound sink raises INSIDE the op — surfaced through the seam and
        # absorbed by the delivery verbs' never-throw boundary as a typed error, never
        # a silent fake success (Law 2).
        if self._api is None:
            raise RuntimeError("output-media sink is not bound to the Recall API path")
        return await self._api(method, path, body)


class RecallTransport:
    """``TransportProvider`` over Recall.ai. Managed V0 (§2)."""

    def __init__(
        self,
        call_external: CallExternal,
        *,
        api_key: str,
        dm_available: bool = False,
        webhook_url: str = "",
        output_media_url: str = "",
    ) -> None:
        # ``api_key`` is sourced from Secret Manager by the caller and never logged
        # (AC-XCUT-02); stored only to pass into signed round-trips via the seam.
        # ``webhook_url`` is the absolute public URL of OUR Recall receiver
        # (``/webhooks/recall``) — where the per-bot realtime transcript events are
        # delivered. ``output_media_url`` is the webpage the bot streams as its
        # camera (Recall Output Media) — the low-latency surface that plays Proxy's
        # audio into the call. Both are deployment facts injected by the caller
        # (env/settings), never baked in here (Law 4).
        self._call_external = call_external
        self._api_key = api_key
        self._dm_available = dm_available
        self._webhook_url = webhook_url
        self._output_media_url = output_media_url
        self._roster: dict[str, asyncio.Queue[RosterEvent]] = {}
        self._chat: dict[str, asyncio.Queue[ChatMessage]] = {}
        # Bots whose output audio is muted (C5): sink-side suppression, per bot —
        # observed live by every OutputMediaSink handed out for that bot.
        self._muted: set[str] = set()

    def _join_body(self, meeting_link: str) -> dict[str, Any]:
        """The create-bot body, per Recall's REAL ``bot_create`` schema.

        Beyond ``meeting_url`` it carries the full config that makes the bot a live
        participant rather than a mute recorder:

        * ``recording_config.transcript.provider.assembly_ai_v3_streaming`` — runs
          AssemblyAI Universal-Streaming transcription. BYOK: our AssemblyAI key is
          registered in Recall's dashboard, so the provider object rides EMPTY — no
          credential ever enters the body (AC-XCUT-02).
        * ``recording_config.realtime_endpoints`` — one ``webhook`` endpoint at our
          receiver, subscribed to the transcript finals + partials Recall enumerates
          (``transcript.data``/``transcript.partial_data``). Bot status events cannot
          ride here (dashboard-webhook only — see ``_REALTIME_TRANSCRIPT_EVENTS``).
        * ``output_media.camera`` — ``{kind: "webpage", config: {url}}``, Recall's
          Output Media: the bot streams our webpage as its camera, the designated
          low-latency path for an agent to emit audio (the ``output_audio`` clip
          endpoint is explicitly not for conversational audio).

        Transcription + delivery ride together behind ``webhook_url``: a transport
        with no configured receiver cannot consume live transcripts, so it asks for
        none (and never ships an empty-string URL, which Recall's schema rejects).
        ``output_media`` likewise appears only when a surface URL is configured.
        """
        body: dict[str, Any] = {"meeting_url": meeting_link}
        if self._webhook_url:
            body["recording_config"] = {
                "transcript": {"provider": {"assembly_ai_v3_streaming": {}}},
                "realtime_endpoints": [
                    {
                        "type": "webhook",
                        "url": self._webhook_url,
                        "events": list(_REALTIME_TRANSCRIPT_EVENTS),
                    }
                ],
            }
        if self._output_media_url:
            body["output_media"] = {
                "camera": {
                    "kind": "webpage",
                    "config": {"url": self._output_media_url},
                }
            }
        return body

    async def join(self, meeting_link: str) -> str:
        outcome = await self._call_external(
            lambda: self._api("POST", "/bot", self._join_body(meeting_link)),
            service="recall",
            unit_cost_usd=0.50,
        )
        # The seam returns an ``ExternalCallOutcome`` (payload under ``.value``); a
        # fake may hand back the raw payload directly. Duck-type both — honoring the
        # seam contract (AC-XCUT-03) — without coupling transport to ``libs.http``.
        result = getattr(outcome, "value", outcome)
        if not (isinstance(result, dict) and result.get("id")):
            # Recall's POST /bot returns the launched bot's unique id; its absence
            # means no bot launched — surface honestly, never a shared placeholder
            # (Law 2; a non-unique 'bot' id would collide across meetings).
            raise RuntimeError("Recall /bot returned no bot id — no bot launched")
        bot_id = str(result["id"])
        self._roster.setdefault(bot_id, asyncio.Queue())
        self._chat.setdefault(bot_id, asyncio.Queue())
        return bot_id

    async def leave(self, bot_id: str) -> None:
        await self._call_external(lambda: self._api("POST", f"/bot/{bot_id}/leave", {}), service="recall")

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        await self._call_external(
            lambda: self._api("POST", f"/bot/{bot_id}/chat", {"message": message, "pinned": pinned}),
            service="recall",
        )

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        await self._call_external(
            lambda: self._api("POST", f"/bot/{bot_id}/chat", {"message": message, "to": participant_id}),
            service="recall",
        )

    async def mute(self, bot_id: str) -> None:
        """Silence the bot's output audio (C5).

        Recall exposes NO direct bot-mute endpoint (confirmed against the live
        output-audio docs), so mute is the documented equivalent: the REAL stop call —
        DELETE ``/bot/{bot_id}/output_audio/`` (204), which kills any in-flight audio
        now — plus sink-side suppression of every further audio write until
        :meth:`unmute`. The flag is set FIRST so a human mute wins even if the stop
        round-trip fails (Law 3 — human control is absolute).
        """
        self._muted.add(bot_id)
        await self._call_external(
            lambda: self._api("DELETE", f"/bot/{bot_id}/output_audio/", {}),
            service="recall",
        )

    async def unmute(self, bot_id: str) -> None:
        """Lift the bot's output-audio suppression (C5).

        No wire call rides here: Recall has no unmute endpoint — audio output resumes
        when the next real ``output_audio`` POST lands. Anything else would be an
        invented vendor call.
        """
        self._muted.discard(bot_id)

    def roster_events(self, bot_id: str) -> AsyncIterator[RosterEvent]:
        return _drain(self._roster.setdefault(bot_id, asyncio.Queue()))

    def chat_events(self, bot_id: str) -> AsyncIterator[ChatMessage]:
        return _drain(self._chat.setdefault(bot_id, asyncio.Queue()))

    def output_media(self, bot_id: str) -> OutputMediaSink:
        return _RecallOutputMedia(
            self._call_external,
            bot_id,
            api=self._api,
            is_muted=lambda: bot_id in self._muted,
        )

    def channel_report(self, bot_id: str) -> ChannelReport:
        return ChannelReport(dm_available=self._dm_available)

    # ── harness webhook layer feeds live events onto the in-process queues (M2) ──
    def _ingest_roster(self, bot_id: str, event: RosterEvent) -> None:
        self._roster.setdefault(bot_id, asyncio.Queue()).put_nowait(event)

    def _ingest_chat(self, bot_id: str, message: ChatMessage) -> None:
        self._chat.setdefault(bot_id, asyncio.Queue()).put_nowait(message)

    async def _api(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """The sole raw Recall round-trip; invoked only via ``call_external`` (AC-XCUT-03).

        Issues the REAL HTTP request: the raw client is constructed ONLY inside
        ``libs.http`` (``http_client`` — the single raw-client home per §14), imported
        lazily here so transport holds no client and no provider SDK at import time.
        Auth rides Recall's ``Authorization: Token <key>`` scheme; the key is placed
        on the request and never logged (AC-XCUT-02). A non-2xx raises (honest
        degrade, Law 2 — retried/absorbed by the seam and the never-throw delivery
        boundary above); a 2xx returns the parsed JSON body Recall actually sent —
        never a fabricated payload.
        """
        from libs.http.src.http.external import http_client

        headers = {"Authorization": f"Token {self._api_key}"}
        async with http_client(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.request(method, f"{_RECALL_BASE}{path}", headers=headers, json=body)
            resp.raise_for_status()
            if resp.status_code == 204:
                # Recall's DELETE output endpoints answer 204 with NO body (per the
                # live docs) — there is nothing to parse; {} is the honest empty result.
                return {}
            payload: dict[str, Any] = resp.json()
            return payload


async def _drain(queue: asyncio.Queue[Any]) -> AsyncIterator[Any]:
    while True:
        yield await queue.get()
