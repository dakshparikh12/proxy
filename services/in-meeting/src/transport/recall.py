"""The Recall.ai carrier — one implementation of ``TransportProvider`` (§2, §3.1).

Recall gives bot join + per-speaker audio + Output Media + chat + roster/status
webhooks across Meet/Zoom/Teams behind a single API, so zero per-platform code lives
here. This is the sole ``TransportProvider`` impl; callers depend only on the Protocol
(AC-SEAM-01). Every outbound round-trip is issued through the injected ``call_external``
seam (AC-XCUT-03) — no raw provider client is held in this package. The transcript
reaches the agent via the meeting webhook drain (``webhook_routes`` → the resident
conversation cache), not through this carrier.
"""
from __future__ import annotations

import base64
import os
from collections.abc import Awaitable, Callable
from typing import Any

from .external import CallExternal
from .media import AudioChunk, CanvasFrame
from .seams import OutputMediaSink

# Recall's global default host — the us-east-1 alias (per the live regions doc); used
# only when no RECALL_REGION is configured, so unset envs see zero behavior change.
_RECALL_BASE = "https://api.recall.ai/api/v1"


def _recall_base() -> str:
    """Resolve the Recall API base for this deployment's region (Law 4 — never baked in).

    Recall workspaces are region-ISOLATED (https://docs.recall.ai/docs/regions): the
    API lives on per-region hosts (``us-east-1`` / ``us-west-2`` / ``eu-central-1`` /
    ``ap-northeast-1`` ``.recall.ai``) and ``api.recall.ai`` is merely the us-east-1
    alias — a key minted in one region's workspace is 401-rejected on every other
    host (proven live: 401 on api.recall.ai/us-east-1, 200 on us-west-2 for a
    us-west-2 key). ``RECALL_REGION`` names the deployment's region; read at
    transport-construction time (never import time) so tests and deploys can set it.
    Unset or blank keeps the global default.
    """
    region = os.environ.get("RECALL_REGION", "").strip()
    if region:
        return f"https://{region}.recall.ai/api/v1"
    return _RECALL_BASE
# Per-round-trip client timeout (seconds) — matches the workspace seam convention
# (premeeting's token mint); the retry policy above this lives in ``call_external``.
_HTTP_TIMEOUT_S = 15.0
# The realtime event names Recall's create-bot schema enumerates for a
# ``recording_config.realtime_endpoints`` webhook — transcript finals + partials plus
# the meeting-chat event (without it Recall never DELIVERS the chat webhooks the
# harness drain consumes) — protocol identifiers (wire physics), consumed by the
# harness webhook drain under the same names. Bot STATUS events are NOT subscribable
# here: Recall delivers those only through the account webhook configured in its
# dashboard (the §4.6 route verified by ``recall_webhook_secret``), never per-bot.
_REALTIME_EVENTS = (
    "transcript.data",
    "transcript.partial_data",
    "participant_events.chat_message",
)

# AssemblyAI Universal-Streaming (v3) session options, forwarded VERBATIM by Recall through
# ``recording_config.transcript.provider.assembly_ai_v3_streaming`` (Recall's documented AssemblyAI
# passthrough — https://docs.recall.ai/docs/assemblyai). Tuned for the ONE thing a live teammate
# needs: hear its own NAME and react at PARTIAL latency (~300ms) instead of end-of-turn (~1.3s).
# Every field is a REAL AssemblyAI v3 streaming param (verified against
# https://www.assemblyai.com/docs/api-reference/streaming-api/streaming-api and the turn-detection
# guide) — nothing invented:
#
# * ``keyterms_prompt`` — bias recognition toward "Proxy" so the spoken wake word is reliably
#   captured (AssemblyAI keyterms; each term ≤50 chars, ≤100 terms). A mis-heard "Proxy" is a
#   missed wake, so this is the single highest-value field for reliable addressing.
# * ``mode: "min_latency"`` — the Universal-3.5-Pro latency/accuracy preset that tightens
#   turn-detection + partial-emission defaults for the SHORTEST time-to-signal (min_latency |
#   balanced | max_accuracy). REQUIRES the Universal-3.5-Pro streaming model on the account.
# * ``include_partial_turns: true`` — emit partial (in-progress) transcripts (AssemblyAI default
#   true, set explicitly because the wake-on-partial path DEPENDS on them). Words in a partial are
#   immutable once ``word_is_final`` (AssemblyAI), so waking on the partial that carries "Proxy" is
#   safe — that word will not change under the reactive loop.
# * ``format_turns: true`` — clean, punctuated FINAL turns for the notes/transcript.
# * ``end_of_turn_confidence_threshold`` / ``min_end_of_turn_silence_when_confident`` — finalize a
#   short command ("Proxy, mute yourself") fast instead of waiting out a long trailing silence.
#
# HONEST live-verify (Law 2): Recall forwards these to AssemblyAI, but the EXACT accepted field set
# and the ``min_latency`` model-tier must be confirmed against the live Recall workspace before the
# real meeting — a field Recall rejects could 400 the join. This is a founder live-verify item; the
# prior config was ``{}`` (accepts nothing extra), so a live check is the safe cut-over.
_ASSEMBLY_STREAMING_CONFIG: dict[str, Any] = {
    "keyterms_prompt": ["Proxy"],
    "mode": "min_latency",
    "include_partial_turns": True,
    "format_turns": True,
    "end_of_turn_confidence_threshold": 0.4,
    "min_end_of_turn_silence_when_confident": 400,
}


def _output_media_surface() -> str:
    """Which Recall Output-Media SURFACE carries Proxy's webpage — its ``camera`` tile (default,
    proven) or a ``screenshare`` (Law 4 — deployment-set via env, never baked in).

    The ``screenshare`` surface is a REAL, documented Recall key, not an invented one. Recall's
    OpenAPI ``OutputMedia`` schema (from https://docs.recall.ai/reference/bot_output_screenshare_create
    ``.md`` — the shared components block) defines BOTH ``camera`` AND ``screenshare`` as an
    ``OutputMediaConfig``, and ``OutputMediaConfig`` is a ``{kind:"webpage", config:{url}}`` webpage
    (``OutputMediaWebpage``; ``kind`` enum = ``webpage`` only). So the same webpage that renders as
    the camera tile can instead be presented as a prominent shared screen — this is Recall's stated
    "the bot can present the webpage either as a screenshare or as its camera video"
    (https://docs.recall.ai/docs/stream-media), and the symmetric ``output_media.screenshare`` key is
    exactly how it is selected (both at Create-Bot time and via the runtime
    ``POST /bot/{id}/output_media/`` endpoint, which takes the same ``OutputMedia`` shape).

    NOTE — a DIFFERENT endpoint, ``POST /bot/{id}/output_screenshare/`` (``VideoOutputRequest``),
    only accepts static ``kind:"jpeg"`` frames, NOT a webpage; the live-HTML screenshare rides
    ``output_media.screenshare`` (webpage), never that frame endpoint.

    The DEFAULT stays the proven ``camera`` tile (which already renders Proxy's ``screen`` content
    full-frame as the bot's video); ``screenshare`` is an OPT-IN
    (``RECALL_OUTPUT_MEDIA_SURFACE=screenshare``) — schema-confirmed above, but not yet proven on a
    live meeting from this deployment, so it is human-enabled per environment rather than
    defaulted-on (Law 2 — claim only what is proven; Law 3 — the surface is a deployment choice).
    Any unknown value falls back to ``camera``.
    """
    surface = os.environ.get("RECALL_OUTPUT_MEDIA_SURFACE", "").strip().lower()
    return "screenshare" if surface == "screenshare" else "camera"


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
    * flush — a NO-OP: barge-in / mute silence the conversational audio at the
      Output-Media webpage channel (``in_meeting.output_media`` — ``cut()`` clears it,
      ``mute()`` suppresses it), not on this clip sink; there is no ``output_audio``
      stop to issue here (the once-used ``DELETE`` targets a clip that never rides the
      conversational path).
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
        # No-op: barge-in / mute silence the conversational audio at the Output-Media webpage
        # channel, not on this (unused) clip sink; there is no ``output_audio`` stop to issue.
        return None

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
        webhook_url: str = "",
        output_media_url: str = "",
        bot_name: str = "Proxy",
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
        self._webhook_url = webhook_url
        self._output_media_url = output_media_url
        # The name Recall shows in the room AND labels this bot's transcribed speech with.
        # Proxy's own bot keeps the default (the self-wake guard filters PROXY_SPEAKER ==
        # "Proxy"); a test replica passes its human name so attribution reads correctly.
        self._bot_name = bot_name
        # The region-resolved API base (``RECALL_REGION``): captured at construction —
        # every ``_api`` round-trip (and every output-media sink bound to it) rides
        # the workspace's own region host; unset env keeps the global default.
        self._base = _recall_base()
        # Bots whose output audio is muted (C5): sink-side suppression, per bot —
        # observed live by every OutputMediaSink handed out for that bot.
        self._muted: set[str] = set()

    def _join_body(self, meeting_link: str) -> dict[str, Any]:
        """The create-bot body, per Recall's REAL ``bot_create`` schema.

        Beyond ``meeting_url`` it carries the full config that makes the bot a live
        participant rather than a mute recorder:

        * ``recording_config.transcript.provider.assembly_ai_v3_streaming`` — runs
          AssemblyAI Universal-Streaming transcription, carrying the wake-tuning session
          options in ``_ASSEMBLY_STREAMING_CONFIG`` (keyterms "Proxy", min-latency mode,
          partial turns, tightened end-of-turn) so Proxy can be woken by the immutable
          partial that names it, not only at end-of-turn. BYOK: our AssemblyAI key is
          registered in Recall's dashboard, so NO credential ever enters this body — only
          non-secret tuning options do (AC-XCUT-02).
        * ``recording_config.realtime_endpoints`` — one ``webhook`` endpoint at our
          receiver, subscribed to the transcript finals + partials Recall enumerates
          (``transcript.data``/``transcript.partial_data``) plus the meeting-chat
          event (``participant_events.chat_message``) — the subscription that makes
          Recall actually deliver chat to the harness drain. Bot status events cannot
          ride here (dashboard-webhook only — see ``_REALTIME_EVENTS``).
        * ``bot_name`` — ``"Proxy"``, so Recall labels the bot's own transcribed
          speech as "Proxy"; the self-wake guard (``PROXY_SPEAKER == "Proxy"``) then
          filters Proxy's own lines instead of self-waking on them.
        * ``output_media.<surface>`` — ``{kind: "webpage", config: {url}}``, Recall's
          Output Media: the bot streams our webpage as its ``camera`` (default, proven) or
          ``screenshare`` (opt-in — schema-confirmed, see ``_output_media_surface``). Recall's
          ``OutputMedia`` schema defines both keys as the same webpage config, so the surface KEY
          is the only difference. This is the designated low-latency path for an agent to emit
          audio (the ``output_audio`` clip endpoint is explicitly not for conversational audio).

        Transcription + delivery ride together behind ``webhook_url``: a transport
        with no configured receiver cannot consume live transcripts, so it asks for
        none (and never ships an empty-string URL, which Recall's schema rejects).
        ``output_media`` likewise appears only when a surface URL is configured.
        """
        body: dict[str, Any] = {"meeting_url": meeting_link, "bot_name": self._bot_name}
        if self._webhook_url:
            body["recording_config"] = {
                "transcript": {
                    "provider": {"assembly_ai_v3_streaming": dict(_ASSEMBLY_STREAMING_CONFIG)}
                },
                "realtime_endpoints": [
                    {
                        "type": "webhook",
                        "url": self._webhook_url,
                        "events": list(_REALTIME_EVENTS),
                    }
                ],
            }
        if self._output_media_url:
            # The webpage rides one Output-Media surface: the ``camera`` tile (default, proven) or a
            # ``screenshare`` (opt-in) — see ``_output_media_surface``. Recall's ``OutputMedia`` schema
            # exposes both as an ``OutputMediaConfig`` webpage, so the same
            # ``{kind:"webpage", config:{url}}`` shape rides either surface; the surface KEY is the
            # only difference, chosen by deployment env (Law 4), never baked in.
            body["output_media"] = {
                _output_media_surface(): {
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
        return bot_id

    async def leave(self, bot_id: str) -> None:
        # Recall's real endpoint is ``leave_call/`` (verified live; ``/leave`` is a 404).
        await self._call_external(
            lambda: self._api("POST", f"/bot/{bot_id}/leave_call/", {}), service="recall"
        )

    async def post_chat(self, bot_id: str, message: str, *, pinned: bool = False) -> None:
        # Recall's real chat endpoint is ``/bot/{id}/send_chat_message/`` (trailing
        # slash, like every other resource verb) with the pin flag named ``pin`` —
        # NOT ``/chat`` / ``pinned`` (verified against docs.recall.ai).
        await self._call_external(
            lambda: self._api(
                "POST", f"/bot/{bot_id}/send_chat_message/", {"message": message, "pin": pinned}
            ),
            service="recall",
        )

    async def send_dm(self, bot_id: str, message: str, participant_id: str) -> None:
        # Same endpoint; ``to`` names the recipient (Recall defaults to "everyone" —
        # per-participant DM is Zoom-only, degraded honestly by the platform).
        await self._call_external(
            lambda: self._api(
                "POST",
                f"/bot/{bot_id}/send_chat_message/",
                {"message": message, "to": participant_id},
            ),
            service="recall",
        )

    async def mute(self, bot_id: str) -> None:
        """Silence the bot's output audio (C5).

        The conversational audio rides the Output-Media WEBPAGE channel (``in_meeting.
        output_media``), silenced there by the host (``MeetingConnection.audio_mute``).
        This verb only marks the (unused) clip path muted so any ``output_audio`` POST
        is sink-side suppressed too — NO wire call: Recall exposes no bot-mute endpoint,
        and the ``DELETE /bot/{id}/output_audio/`` stop it once used targets a clip
        stream that never rides the conversational path (a 404 for a real meeting). The
        flag is set so a human mute wins regardless of the wire (Law 3 — human control
        is absolute).
        """
        self._muted.add(bot_id)

    async def unmute(self, bot_id: str) -> None:
        """Lift the bot's output-audio suppression (C5).

        No wire call rides here: Recall has no unmute endpoint — audio output resumes
        when the next real ``output_audio`` POST lands. Anything else would be an
        invented vendor call.
        """
        self._muted.discard(bot_id)

    def output_media(self, bot_id: str) -> OutputMediaSink:
        return _RecallOutputMedia(
            self._call_external,
            bot_id,
            api=self._api,
            is_muted=lambda: bot_id in self._muted,
        )

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
            resp = await client.request(method, f"{self._base}{path}", headers=headers, json=body)
            resp.raise_for_status()
            if resp.status_code == 204:
                # Recall's DELETE output endpoints answer 204 with NO body (per the
                # live docs) — there is nothing to parse; {} is the honest empty result.
                return {}
            payload: dict[str, Any] = resp.json()
            return payload
