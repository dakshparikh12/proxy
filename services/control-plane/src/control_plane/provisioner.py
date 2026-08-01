"""``provisioner`` — the per-meeting join spine for the reactive-workroom model.

On a Recall ``in_call`` webhook this turns the built pieces into a RUNNING meeting
(SPEC §0/§2/§3/§8):

1. **Atomically claims** the meeting via ``ops.claim_meeting`` — an INSERT ... ON
   CONFLICT DO NOTHING against the ``operation_runs`` partial-unique index
   (``(scope_id, operation_type) WHERE status='running'``). The non-null returner owns
   the meeting; a concurrent duplicate join gets ``None`` and backs off — **one harness
   per meeting, no broker, no Redis**. The claim/fencing substrate is unchanged.

2. **Provisions the workroom** (``in_meeting.workroom.provision_workroom`` — a per-meeting
   E2B sandbox with the repo cloned in, seeded with the prime ``CLAUDE.md``, ``MEETING_
   INFO.md``, ``REPO_MAP.md`` from the pre-meeting map, and an empty ``MEETING_NOTES.md``)
   and **builds the host-side meeting connection** (``in_meeting.meeting_connection.
   MeetingConnection`` over the Cartesia speak pipe + the Recall room verbs — creds stay
   host-side). Both are stashed on the :class:`MeetingRuntime` with a :class:`MeetingSession`
   that runs the transcript→wake→respond loop the webhook drain feeds.

3. **Launches the run loop** — :func:`run_meeting_until_end` provisions, then runs until
   the meeting-end webhook (routed via the registry's ``end_meeting``) tears the workroom
   down. A meeting has NO time cap (SPEC §9); the only wall clock is the generous env-
   configurable safety ceiling (``MEETING_MAX_HOURS``, default 12h) so a run whose end
   never arrives can't leak an instance forever.

Honest-degrade throughout (§3.8 / Rule 6): a missing subscription token, an unbound repo,
or a provision fault surfaces in the log and yields a runtime with no workroom — the
meeting still boots, it just cannot wake a workroom this meeting. World-touching is
impossible from the sandbox by construction (no push/send creds; egress denied — Law 3).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
from dataclasses import dataclass
from typing import Any

from libs.db import Database, repos
from libs.ops import MEETING_HARNESS_OP, OperationHandle, claim_meeting

from .meeting_runtime import MeetingRuntime
from .meeting_session import MeetingSession

_log = logging.getLogger(__name__)

# A meeting has NO time cap (SPEC §9): the launched loop runs until the meeting-end
# webhook, never a wall-clock kill. The ONLY remaining wall clock is a GENEROUS safety
# ceiling so a wedged loop can never leak an instance forever — env-configurable via
# ``MEETING_MAX_HOURS`` and resolved at LAUNCH time (never frozen at import), default 12h.
MEETING_MAX_HOURS_ENV = "MEETING_MAX_HOURS"
DEFAULT_MEETING_MAX_HOURS: float = 12.0

# Interval between sandbox keep-warm beats: the warm E2B sandbox self-times-out (1h); with
# no meeting cap a live meeting can outlast it, so the provisioner extends the sandbox
# lifetime on this cadence while the meeting runs. Comfortably inside the 1h lifetime so a
# missed/failed beat still leaves a full beat before expiry. unit: seconds.
SANDBOX_KEEPWARM_INTERVAL_S: float = 1800.0

# The sandbox lifetime (seconds) each keep-warm beat resets from now (the workroom
# provisions the sandbox with its own PROVISION_TIMEOUT_S; the beat re-extends by this).
SANDBOX_TIMEOUT_S: float = 3600.0

# Recall bot-status event names that mean "the bot is now IN the room" — the moment the
# harness claims + provisions the per-meeting runtime (mirrors ``control_plane.webhooks``).
_IN_CALL_EVENTS = frozenset(
    {"bot.in_call", "in_call", "bot.in_call_recording", "bot.joining_call"}
)


@dataclass
class ProvisionOutcome:
    """The result of handing an ``in_call`` webhook to the provisioner.

    ``claimed`` is the load-bearing bit: True iff THIS instance won the atomic claim and
    opened the harness; False iff a concurrent/existing harness already owns the meeting
    (this instance backed off). ``ran_to_end`` is set by :func:`run_meeting_until_end`
    when the loop ran to the meeting-end signal (vs a timeout).
    """

    claimed: bool
    run_id: Any = None
    ran_to_end: bool = False


def _meeting_max_s() -> float:
    """The wall-clock SAFETY CEILING on one meeting's run loop, in seconds.

    NOT a meeting time cap (SPEC §9): this is the leak backstop for a loop whose end never
    arrives. ``MEETING_MAX_HOURS`` overrides; an unset, unparsable, or non-positive value
    falls back to the generous 12h default — never unbounded-by-typo, never a hard hour."""
    raw = os.environ.get(MEETING_MAX_HOURS_ENV, "")
    try:
        hours = float(raw)
    except ValueError:
        hours = DEFAULT_MEETING_MAX_HOURS
    if hours <= 0:
        hours = DEFAULT_MEETING_MAX_HOURS
    return hours * 3600.0


def _event_name(payload: dict[str, Any]) -> str:
    name = payload.get("event") or payload.get("type") or ""
    return str(name).strip().lower()


def _bot_id(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    if isinstance(data, dict) and data.get("bot_id"):
        return str(data["bot_id"])
    if payload.get("bot_id"):
        return str(payload["bot_id"])
    return None


def _default_meeting_transport() -> Any:
    """The REAL RecallTransport for the meeting connection's room verbs.

    Reuses the ONE production construction site, ``control_plane.meetings._default_transport``
    (RecallTransport bound to the funded ``call_external`` funnel + env/Secret-Manager
    config). One recipe, no second construction site."""
    from .meetings import _default_transport

    return _default_transport()


@dataclass
class _SpeakSink:
    """Adapt the sentence-buffering ``SpeakPipe`` to the connection's ``SpeakSink`` shape.

    ``MeetingConnection`` carries the agent's spoken text with a single ``say`` per send;
    the underlying ``SpeakPipe`` buffers by sentence and would leave a trailing partial to a
    0.5s tail timer, so this flushes after each ``say`` — the whole utterance reaches the
    room deterministically (physics, not a decision). ``cut`` is the barge-in primitive,
    forwarded unchanged."""

    pipe: Any

    async def say(self, text: str) -> None:
        await self.pipe.say(text)
        flush = getattr(self.pipe, "flush", None)
        if flush is not None:
            await flush()

    async def cut(self) -> None:
        cut = getattr(self.pipe, "cut", None)
        if cut is not None:
            await cut()


def _approve_url_for(meeting_id: str, draft_id: str) -> str:
    """The human approve URL for a staged draft: ``<PUBLIC_BASE_URL>/m/<mid>/drafts/<did>/accept``.

    Built from the deployment's public origin (``PUBLIC_BASE_URL``) + the EXISTING accept route
    (``accept_route.ACCEPT_PATH``) so the offer link a human clicks lands on the real, hardened
    accept handler. Empty origin ⇒ "" (honest degrade: no reachable approve link) — pure string
    physics (Law 4), the same shape ``relay.relay_url_for`` uses."""
    base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base:
        return ""
    from .accept_route import ACCEPT_PATH

    path = ACCEPT_PATH.format(meeting_id=meeting_id, draft_id=draft_id)
    return f"{base}{path}"


def _build_meeting_sinks(
    *, db: Database, meeting_id: str, tenant_id: str
) -> tuple[Any, Any, Any]:
    """Build the ``(offer, screen, audio_mute)`` sinks the :class:`MeetingConnection` carries (Law-3 safe).

    * **offer** ``(content, to) -> approve_url``: STAGES a durable draft (``workroom.drafts.
      propose_change`` on the async pool → one GCS bundle + one ``staged_drafts`` row at
      ``status=proposed``) and returns the approve URL built from ``PUBLIC_BASE_URL`` + the accept
      route. It NEVER pushes/sends — the actual apply happens only when a human clicks accept (Law 3;
      the sandbox holds no push/send creds). A staging fault returns "" (honest — no approve link),
      never a raise (the connection turns a "" into an honest ``MeetingSend(ok=..)``).
    * **screen** ``(url) -> shown_url``: points the meeting's Output-Media surface at ``url`` via the
      real ``output_media`` channel and returns the URL it recorded — an honest surface intent, never
      a fabricated success.
    * **audio_mute** ``(muted) -> None``: mutes/unmutes the meeting's Output-Media WEBPAGE channel —
      where the spoken PCM actually rides — so 'mute yourself' really silences the bot (Law 3, human
      control is absolute). A surface fault is swallowed to an honest no-op, never a crash.

    All three are async callables matching the ``OfferSink`` / ``ScreenSink`` / ``AudioMuteSink``
    Protocols; ``db`` + ``meeting_id`` are closed over so the staged draft binds to THIS meeting.
    ``tenant_id`` is accepted for symmetry/audit; the accept route re-derives the owning tenant
    server-side from the persisted row (a client field never authorizes an entity)."""
    _ = tenant_id  # the accept route derives the owning tenant server-side from the persisted row

    async def _offer(content: str, to: str) -> str:
        # World-touching = a STAGED draft behind a human click (Law 3): persist the artifact +
        # return the approve URL; NEVER push. A staging fault degrades to "" (no approve link).
        try:
            from workroom.drafts import propose_change

            summary = (content or "").strip().splitlines()[0][:120] if content else "proposed change"
            proposed = await propose_change(
                db,
                meeting_id=meeting_id,
                kind="code-change",
                summary=summary or "proposed change",
                content=content,
            )
            return _approve_url_for(meeting_id, str(proposed.draft_id))
        except Exception:  # noqa: BLE001 - a staging fault is an honest "" (no link), never a crash
            _log.warning(
                "offer staging failed for meeting %s — no approve link (honest degrade)",
                meeting_id,
                exc_info=True,
            )
            return ""

    async def _screen(url: str) -> str:
        # Point the bot's Output-Media surface at the URL (a rendered diff/mock/page) and return
        # the URL recorded — an honest surface intent, never a fabricated success.
        try:
            from in_meeting import output_media

            channel = output_media.channel_for(meeting_id)
            return await channel.set_screen(url)
        except Exception:  # noqa: BLE001 - a surface fault degrades to the honest URL, never a crash
            _log.warning(
                "screen surface update failed for meeting %s (honest degrade)",
                meeting_id,
                exc_info=True,
            )
            return (url or "").strip()

    async def _audio_mute(muted: bool) -> None:
        # Silence/restore the conversational audio at the Output-Media webpage channel (where the
        # spoken PCM rides) so 'mute yourself' really stops the bot (Law 3). Never crashes the turn.
        try:
            from in_meeting import output_media

            channel = output_media.channel_for(meeting_id)
            if muted:
                channel.mute()
            else:
                channel.unmute()
        except Exception:  # noqa: BLE001 - a mute fault must never crash the meeting (honest no-op)
            _log.warning(
                "audio mute toggle (muted=%s) failed for meeting %s (honest degrade)",
                muted,
                meeting_id,
                exc_info=True,
            )

    return _offer, _screen, _audio_mute


def _meeting_info_md(payload: dict[str, Any]) -> str:
    """Render ``MEETING_INFO.md`` (who's in the room) from the ``in_call`` webhook payload.

    Title + participant names are read VERBATIM from the same Recall callback the join
    already processes (``data.title`` / ``data.participants[].name``) — never synthesized.
    Delegates the markdown to the KEEP ``in_meeting.prime.render_meeting_info`` so the
    workroom's who's-in-the-room file uses the one canonical shape. A payload with no
    metadata renders an honest "(no meeting metadata available)" body."""
    from in_meeting.prime import render_meeting_info

    data = payload.get("data")
    data = data if isinstance(data, dict) else {}
    title = str(data.get("title", "") or "")
    raw_parts = data.get("participants")
    # Carry the Recall participant id alongside the name when present, so the agent can pass it as a
    # DM's ``to`` (``send_dm`` addresses by id, never by name). ``id``/``participant_id`` are the two
    # shapes Recall uses; an absent id degrades to a name-only line (render handles both). Verbatim
    # from the same callback the join already reads — never synthesized.
    participants: tuple[tuple[str, str], ...] = tuple(
        (
            str(p.get("name", "") or ""),
            str(p.get("id", "") or p.get("participant_id", "") or ""),
        )
        for p in (raw_parts if isinstance(raw_parts, list) else [])
        if isinstance(p, dict) and p.get("name")
    )
    return render_meeting_info(title=title, participants=participants)


async def _resolve_map_text(resolved: dict[str, Any], *, db: Database) -> str | None:
    """Load the pre-meeting MAP for this meeting's repo from ``premeeting.map_store``.

    The pre-meeting system stored the durable ``index.md`` repo map in Postgres
    ``repo_maps`` for ``(tenant, repo, sha)``; the workroom seeds it as ``REPO_MAP.md`` so
    native Claude opens oriented (SPEC §8). Resolved off the SAME repo row the join path
    already uses (repo_id → tenant + full_name). Prefer the exact ``pinned_sha`` map, then
    fall back to the latest for the repo. Fail-closed to ``None`` (an unmapped repo simply
    has no map to prime on — the meeting is unaffected); ALWAYS tenant-scoped so a
    cross-tenant read is impossible (PM-STORE-02). Never a raise on the join path."""
    repo_id = resolved.get("repo_id")
    if repo_id is None:
        return None
    try:
        from premeeting.map_store import load_latest_map, load_map
        from premeeting.paths import repo_name_from_url

        async with db.acquire() as conn:
            repo = await repos.meetings.get_repo_by_id(conn, repo_id)
        if repo is None or not repo.get("full_name"):
            return None
        tenant_id = str(repo["tenant_id"])
        repo_name = repo_name_from_url(str(repo["full_name"]))
        pinned_sha = str(resolved.get("pinned_sha") or "")
        async with db.acquire() as conn:
            if pinned_sha:
                exact: str | None = await load_map(
                    conn, tenant_id=tenant_id, repo=repo_name, sha=pinned_sha
                )
                if exact is not None:
                    return exact
            latest: tuple[str, str] | None = await load_latest_map(
                conn, tenant_id=tenant_id, repo=repo_name
            )
        return None if latest is None else latest[1]
    except Exception:  # noqa: BLE001 - Rule 6: a resolution fault degrades to no map, never a crash
        return None


async def _repo_clone_url(resolved: dict[str, Any], *, db: Database) -> str:
    """The public GitHub URL of the meeting's bound repo (``""`` when unbound).

    Resolved off the SAME repo row the map/claim paths use (repo_id → full_name). A
    read-only clone token for private repos is a follow-up via ``premeeting.github_auth``.
    """
    repo_id = resolved.get("repo_id")
    if repo_id is None:
        return ""
    async with db.acquire() as conn:
        repo = await repos.meetings.get_repo_by_id(conn, repo_id)
    if repo is None or not repo.get("full_name"):
        return ""
    return f"https://github.com/{str(repo['full_name']).strip('/')}"


async def provision_meeting(
    payload: dict[str, Any],
    *,
    db: Database,
    registry: Any,
    transport: Any = None,
    speak: Any = None,
    oauth_token: str | None = None,
) -> ProvisionOutcome:
    """Claim + provision the per-meeting workroom runtime from a Recall ``in_call`` webhook.

    The atomic-claim entry: resolve the bot back to its meeting, then INSERT the
    ``meeting-harness`` ``operation_runs`` row via ``ops.claim_meeting``. On a WIN this
    instance owns the meeting — it provisions the workroom, builds the meeting connection,
    wires the reactive session, and stashes all three on a :class:`MeetingRuntime` bound to
    the claimed row's fencing handle. On a LOSS (a concurrent duplicate join, or an
    already-running harness) it returns ``claimed=False`` and opens NO second runtime.

    A non-``in_call`` event, or an unresolvable bot, is a safe no-op (``claimed=False``) —
    never a raise on the webhook path. The injection kwargs (``transport``/``speak``/
    ``oauth_token``) are the vendor seams — ``None`` everywhere means the REAL production
    edges (RecallTransport, Cartesia speak pipe, the subscription token from env).
    """
    if _event_name(payload) not in _IN_CALL_EVENTS:
        return ProvisionOutcome(claimed=False)

    bot_id = _bot_id(payload)
    if bot_id is None:
        return ProvisionOutcome(claimed=False)

    async with db.acquire() as conn:
        resolved = await repos.meetings.get_by_bot_id(conn, bot_id)
    if resolved is None:
        return ProvisionOutcome(claimed=False)  # unknown bot never opens a harness
    meeting_id = str(resolved["id"])

    # An already-registered runtime on THIS instance means we already own the meeting; a
    # redelivered in_call must NOT re-claim or re-wire (idempotent).
    if registry.get(meeting_id) is not None:
        return ProvisionOutcome(claimed=False)

    # THE atomic claim: the partial-unique index arbitrates the race. A non-null id → we
    # won and own the meeting; None → a concurrent/existing harness owns it.
    run_id = await claim_meeting(
        db, meeting_id, MEETING_HARNESS_OP, created_by=db.instance_id
    )
    if run_id is None:
        return ProvisionOutcome(claimed=False)  # lost the race — back off, no harness

    handle = OperationHandle(db, run_id, meeting_id, MEETING_HARNESS_OP)
    runtime = MeetingRuntime(meeting_id=meeting_id, operation_handle=handle)
    registry.register(runtime)

    # Provision the workroom + build the connection + wire the session. A provision fault
    # must never strand the claimed meeting OR crash the webhook path — it degrades to a
    # runtime with no workroom (the meeting boots without a brain) with a loud log.
    try:
        map_text = await _resolve_map_text(resolved, db=db)
        session, workroom, connection, speak_pipe = await _assemble_workroom(
            resolved,
            db=db,
            bot_id=bot_id,
            transport=transport,
            speak=speak,
            oauth_token=oauth_token,
            map_text=map_text,
            meeting_info=_meeting_info_md(payload),
        )
    except Exception:  # noqa: BLE001 - never a raise on the webhook path; loud, not silent
        _log.critical(
            "workroom assembly failed for meeting %s — the meeting runs WITHOUT its brain; "
            "this needs a human",
            meeting_id,
            exc_info=True,
        )
    else:
        runtime.session = session
        runtime.workroom = workroom
        runtime.connection = connection
        runtime.speak_pipe = speak_pipe
        if workroom is not None:
            # Keep-warm: a meeting has no time cap, so the 1h-lifetime sandbox is
            # periodically re-extended while the meeting is live. Cancelled at meeting end
            # before the kill; a failed beat logs, never crashes.
            runtime.sandbox_keepwarm = asyncio.ensure_future(
                _sandbox_keepwarm(workroom, meeting_id)
            )
    return ProvisionOutcome(claimed=True, run_id=run_id)


async def _assemble_workroom(
    resolved: dict[str, Any],
    *,
    db: Database,
    bot_id: str,
    transport: Any = None,
    speak: Any = None,
    oauth_token: str | None = None,
    map_text: str | None = None,
    meeting_info: str = "",
) -> tuple[MeetingSession | None, Any, Any, Any]:
    """Provision the workroom + build the meeting connection + wire the reactive session.

    Returns ``(session, workroom, connection, speak_pipe)``. Honest-degrade throughout
    (§3.8): a missing subscription token or an unbound repo surfaces in the log and yields
    ``(None, None, None, speak_pipe)`` — the meeting still boots (it has a voice channel),
    it just can't wake a workroom this meeting. World-touching is impossible from the
    sandbox by construction (no push/send creds; egress denied — Law 3). ``meeting_info`` is
    the rendered ``MEETING_INFO.md`` (who's in the room) seeded into the sandbox so the agent
    can address/read the room (SPEC §2/§8)."""
    import in_meeting.speak as im_speak
    from in_meeting.meeting_connection import MeetingConnection
    from in_meeting.workroom import provision_workroom

    from libs.http.src.http.external import call_external

    from .relay import relay_url_for

    meeting_id = str(resolved["id"])
    pinned_sha = str(resolved.get("pinned_sha") or "") or None
    live_transport = transport if transport is not None else _default_meeting_transport()
    speak_pipe = speak if speak is not None else im_speak.real_speak_sink(meeting_id)

    # The subscription token that lets native ``claude`` authenticate INSIDE the sandbox
    # (Secret Manager in prod; env locally). The ONLY credential the sandbox receives.
    token = (oauth_token or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")).strip()
    if not token:
        _log.warning(
            "no CLAUDE_CODE_OAUTH_TOKEN for meeting %s — workroom cannot start; meeting "
            "boots without a workroom (honest degrade)",
            meeting_id,
        )
        return None, None, None, speak_pipe

    repo_url = await _repo_clone_url(resolved, db=db)
    if not repo_url:
        _log.warning(
            "no bound repo for meeting %s — workroom cannot clone; meeting boots without a "
            "workroom (honest degrade)",
            meeting_id,
        )
        return None, None, None, speak_pipe

    # Mint the per-meeting relay wiring for the in-sandbox MCP server (SPEC §4/§5): a fresh secret
    # bearer the relay route authenticates, and the public relay URL the sandbox POSTs to. When
    # PUBLIC_BASE_URL is unset the URL is "" (honest degrade): no reachable relay ⇒ the agent's
    # dynamic mediums are recorded locally only and the session speaks the result text.
    relay_token = secrets.token_urlsafe(32)
    relay_url = relay_url_for(meeting_id)
    if not relay_url:
        _log.warning(
            "no PUBLIC_BASE_URL for meeting %s — the in-sandbox to_meeting relay is unreachable; "
            "the agent falls back to result-text speak (honest degrade)",
            meeting_id,
        )

    try:
        workroom = await provision_workroom(
            call=call_external,
            token=token,
            repo_url=repo_url,
            sha=pinned_sha,
            map_text=map_text or "",
            relay_url=relay_url,
            relay_token=relay_token,
        )
    except Exception:  # noqa: BLE001 — a provision fault degrades honestly, never kills the join
        _log.warning(
            "workroom provision failed for meeting %s — meeting boots without a workroom "
            "(honest degrade; %s)",
            meeting_id,
            repo_url,
            exc_info=True,
        )
        return None, None, None, speak_pipe

    # Seed MEETING_INFO.md (who's in the room) into the sandbox alongside the prime/map/notes
    # provision_workroom already seeded, so the agent can address/read the room (SPEC §2/§8).
    # Best-effort: a seed fault leaves the workroom running without the info file (honest
    # degrade — the transcript still carries who's speaking), never kills the join.
    if meeting_info:
        with contextlib.suppress(Exception):
            from in_meeting.prime import MEETING_INFO_FILE
            from in_meeting.workroom import REPO_DIR

            info_path = f"{REPO_DIR}/{MEETING_INFO_FILE}"
            await call_external(
                lambda: workroom.sandbox.files.write(info_path, meeting_info),
                service="e2b",
            )

    # The one meeting connection: the agent's result is carried out over the Cartesia speak
    # pipe (say/cut) + the Recall room verbs (chat/dm/mute/unmute — creds host-side) + the
    # offer/screen sinks (world-touching-safe, below). This is a driver, not a decision (Law 4):
    # the agent chooses what/how, this maps to physics.
    tenant_id = str(resolved.get("tenant_id") or "")
    offer_sink, screen_sink, audio_mute_sink = _build_meeting_sinks(
        db=db, meeting_id=meeting_id, tenant_id=tenant_id
    )
    connection = MeetingConnection(
        speak=_SpeakSink(pipe=speak_pipe),
        room=live_transport,
        bot_id=bot_id,
        offer=offer_sink,
        screen=screen_sink,
        audio_mute=audio_mute_sink,
    )
    session = MeetingSession(workroom=workroom, connection=connection)
    _log.info(
        "workroom assembled for meeting %s (sandbox=%s repo=%s)",
        meeting_id,
        workroom.sandbox_id,
        repo_url,
    )
    return session, workroom, connection, speak_pipe


async def _sandbox_keepwarm(
    workroom: Any, meeting_id: str, *, interval_s: float | None = None
) -> None:
    """Keep the meeting's warm E2B sandbox alive past its self-timeout.

    A meeting has no time cap, but the sandbox is provisioned with a bounded lifetime and
    would silently die under a longer meeting. This heartbeat extends the sandbox lifetime
    by ``SANDBOX_TIMEOUT_S`` on every beat (the confirmed e2b ``AsyncSandbox.set_timeout``
    RESETS the lifetime from now) every ``SANDBOX_KEEPWARM_INTERVAL_S`` while the meeting is
    live. The extension rides the ONE ``call_external`` seam like every E2B round-trip.
    NEVER-THROW: a failed beat logs and the loop keeps beating; cancellation (the teardown
    path) propagates. ``interval_s`` is injectable for tests; the default is resolved per
    beat off the module constant so it stays patchable."""
    while True:
        delay = interval_s if interval_s is not None else SANDBOX_KEEPWARM_INTERVAL_S
        await asyncio.sleep(delay)
        try:
            from libs.http.src.http import external as _http

            sandbox = workroom.sandbox
            await _http.call_external(
                lambda: sandbox.set_timeout(int(SANDBOX_TIMEOUT_S)),
                service="e2b",
                max_retries=1,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never-throw: a failed beat must not kill the meeting
            _log.warning(
                "sandbox keep-warm extension failed for meeting %s — retrying on the next "
                "beat; if the sandbox lapses the meeting continues (honest degrade)",
                meeting_id,
                exc_info=True,
            )


async def run_meeting_until_end(
    payload: dict[str, Any],
    *,
    db: Database,
    registry: Any,
    timeout_s: float | None = None,
    transport: Any = None,
    speak: Any = None,
    oauth_token: str | None = None,
) -> ProvisionOutcome:
    """The ``asyncio.run``-style meeting entry: claim, provision, run to meeting end.

    This is what the harness process runs per meeting. It provisions (claim + assemble the
    workroom runtime), then waits until the meeting-end webhook has torn the runtime down
    (the drain calls ``registry.end_meeting`` on a terminal webhook, which drops the runtime
    from the table). A meeting has NO time cap (SPEC §9): ``timeout_s=None`` (production)
    resolves the generous env-configurable safety ceiling — a leak backstop, never a
    meeting kill; an explicit ``timeout_s`` is honored for tests/shutdown paths. On timeout
    the runtime is torn down here. Either way the ``operation_runs`` row completes.
    """
    outcome = await provision_meeting(
        payload,
        db=db,
        registry=registry,
        transport=transport,
        speak=speak,
        oauth_token=oauth_token,
    )
    if not outcome.claimed:
        return outcome

    bot_id = _bot_id(payload)
    async with db.acquire() as conn:
        resolved = await repos.meetings.get_by_bot_id(conn, bot_id or "")
    if resolved is None:
        return outcome  # a claim won but the bot no longer resolves — nothing to run
    meeting_id = str(resolved["id"])

    # Wait for the meeting-end webhook to drop the runtime (the drain runs end_meeting on a
    # terminal webhook). No meeting time cap (SPEC §9) — the resolved bound is only the
    # generous leak-backstop ceiling, resolved at launch time so a deploy can raise it.
    bound_s = timeout_s if timeout_s is not None else _meeting_max_s()
    ran_to_end = False
    try:
        await asyncio.wait_for(_wait_until_ended(registry, meeting_id), timeout=bound_s)
        ran_to_end = True
    except asyncio.TimeoutError:
        ran_to_end = False
        # The end webhook never arrived (a leak): tear the workroom down ourselves.
        await registry.end_meeting(meeting_id, reason="safety_ceiling")
    finally:
        await _complete_run(db, outcome.run_id)

    outcome.ran_to_end = ran_to_end
    return outcome


async def _wait_until_ended(
    registry: Any, meeting_id: str, *, poll_s: float = 0.5
) -> None:
    """Return once the meeting's runtime has been dropped from the registry (meeting end).

    Meeting end is EXPLICIT (SPEC §3.1): the meeting-end webhook routes through the drain's
    ``registry.end_meeting``, which drops the runtime. This polls the registry for that
    drop rather than owning a carrier — the run loop and the webhook drain share the one
    registry table, so the drop IS the end signal."""
    while registry.get(meeting_id) is not None:
        await asyncio.sleep(poll_s)


async def _complete_run(db: Database, run_id: Any) -> None:
    """Flip the meeting's ``operation_runs`` row to completed (only if still owned)."""
    if run_id is None:
        return
    with contextlib.suppress(Exception):
        async with db.acquire() as conn:
            await conn.execute(
                "UPDATE operation_runs "
                "SET status = 'completed', completed_at = now() "
                "WHERE id = $1 AND status = 'running'",
                run_id,
            )


def make_provision_launcher(
    db: Database,
    registry: Any,
    *,
    timeout_s: float | None = None,
    tasks: set[asyncio.Task[Any]] | None = None,
) -> Any:
    """Build the ``launch`` callback the webhook drain wires in.

    Returns an async callable ``launch(payload)`` that spawns :func:`run_meeting_until_end`
    as a BACKGROUND task — the webhook drain returns 200 immediately while the meeting runs
    for hours on its own task. The task set holds a strong reference so the meeting task is
    never GC'd mid-flight; the done-callback discards it on completion. This is the ONE
    production caller that turns an ``in_call`` webhook into a running, atomically-claimed
    meeting."""
    live: set[asyncio.Task[Any]] = tasks if tasks is not None else set()

    async def _launch(payload: dict[str, Any]) -> None:
        task = asyncio.ensure_future(
            run_meeting_until_end(
                payload, db=db, registry=registry, timeout_s=timeout_s
            )
        )
        live.add(task)
        task.add_done_callback(live.discard)

    return _launch


__all__ = [
    "DEFAULT_MEETING_MAX_HOURS",
    "MEETING_MAX_HOURS_ENV",
    "SANDBOX_KEEPWARM_INTERVAL_S",
    "ProvisionOutcome",
    "make_provision_launcher",
    "provision_meeting",
    "run_meeting_until_end",
]
