"""Live-side wiring — the taps that reach the running Proxy / control-plane / E2B.

These are the pieces that CANNOT be exercised offline (they hit real infra), so
each is a thin adapter behind the pluggable seams the driver/monitor accept. What
needs live tuning at smoke time is called out inline.

* ``build_proxy_probes`` — (proxy_speaking, confirm_proxy).
  ``confirm_proxy`` asks the control-plane whether Proxy's bot is in the room.
  ``proxy_speaking`` reports whether Proxy is currently speaking — the signal the
  ``wait-for-Proxy-done`` / ``interrupt`` gates need. There is no first-class
  "is Proxy speaking" endpoint yet, so on the live path this returns ``None``
  (the gates fall back to a bounded wait, flagged in the SAID log) UNLESS a
  control-plane speaking-state route is present. LIVE-TUNING-NEEDED.

* ``build_monitor_sources`` — (heard, notes, artifacts).
  ``heard`` + ``notes`` both read the control-plane ``GET /admin/transcript``
  (the sandbox ``MEETING_NOTES.md`` capture, surfaced host-side), authenticated
  with the internal admin bearer and scoped to ``cfg.meeting_id``: ``heard``
  filters the parsed lines to the SAID window, ``notes`` returns the full raw
  capture (its non-emptiness is the "transcript captured" proof). ``artifacts``
  taps the E2B sandbox for the real diff / run output (post-smoke stub). Each is
  best-effort: if the route/tap is unavailable it returns empty and the bundle
  records the gap honestly rather than faking data.

* ``provision_proxy`` — the OAuth-skipping direct provision: POSTs to the
  control-plane ``POST /admin/test-provision`` (the internal-bearer dev tap) to
  launch a REAL Recall bot into the Meet via ``invite_proxy`` — no Google session.

Every HTTP round-trip rides ``libs.http.call_external`` (the seam); no raw vendor
client lives here.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .config import HarnessConfig

ProxySpeaking = Callable[[], bool]
ConfirmProxy = Callable[[], Awaitable[bool]]
HeardSource = Callable[[float, float], list[dict[str, Any]]]
NotesSource = Callable[[], str]
ArtifactSource = Callable[[], dict[str, Any]]


async def _get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """One GET through the seam; ``None`` on any failure (best-effort tap)."""
    from libs.http.src.http.external import call_external, http_client

    async def op() -> dict[str, Any]:
        async with http_client(timeout=10.0) as client:
            resp = await client.get(url, params=params or {}, headers=headers or {})
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

    try:
        outcome = await call_external(op, service="control-plane")
    except Exception:
        return None
    result: dict[str, Any] = outcome.value
    return result


async def _post_json(
    url: str, body: dict[str, Any], headers: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """One POST through the seam; ``None`` on any failure (best-effort tap)."""
    from libs.http.src.http.external import call_external, http_client

    async def op() -> dict[str, Any]:
        async with http_client(timeout=30.0) as client:
            resp = await client.post(url, json=body, headers=headers or {})
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

    try:
        outcome = await call_external(op, service="control-plane")
    except Exception:
        return None
    result: dict[str, Any] = outcome.value
    return result


async def provision_proxy(cfg: HarnessConfig, *, repo: str) -> dict[str, Any] | None:
    """Drive the control-plane's dev-only ``POST /admin/test-provision`` — the OAuth-skipping invite.

    Binds the test tenant + ``repo`` and launches a REAL Recall bot into ``cfg.meeting_url`` via the
    same ``invite_proxy`` the product front door uses, authenticated by the internal admin bearer
    (``X-Internal-Token``) instead of a Google session. Returns the response dict
    (``{meeting_id, bot_id, pinned_sha, indexed, ...}``) or ``None`` on any failure (the caller
    reports it honestly)."""
    base = cfg.control_plane_url.rstrip("/")
    headers = {"X-Internal-Token": cfg.internal_token} if cfg.internal_token else {}
    return await _post_json(
        f"{base}/admin/test-provision",
        {"meeting_url": cfg.meeting_url, "repo": repo},
        headers,
    )


def build_proxy_probes(cfg: HarnessConfig) -> tuple[ProxySpeaking | None, ConfirmProxy]:
    """Return (proxy_speaking, confirm_proxy) for this run.

    ``confirm_proxy`` hits the control-plane meeting surface and looks for a bot
    labelled "Proxy". ``proxy_speaking`` is ``None`` for now (no speaking-state
    route) — LIVE-TUNING-NEEDED; wire it to a control-plane speaking flag or the
    Proxy output-media channel state when available.
    """
    base = cfg.control_plane_url.rstrip("/")

    async def confirm_proxy() -> bool:
        import asyncio

        data = await asyncio.wait_for(
            _get_json(f"{base}/admin/meetings"), timeout=15.0
        )
        if not data:
            return False
        # Best-effort: any meeting whose roster/bot_name mentions Proxy counts.
        text = str(data).lower()
        return "proxy" in text

    # proxy_speaking: no clean live signal yet → None (bounded-wait fallback).
    proxy_speaking: ProxySpeaking | None = None
    return proxy_speaking, confirm_proxy


def build_monitor_sources(
    cfg: HarnessConfig,
) -> tuple[HeardSource | None, NotesSource | None, ArtifactSource | None]:
    """Return (heard, notes, artifacts) taps — each best-effort, honest on gap.

    ``heard`` + ``notes`` both read the control-plane's ``GET /admin/transcript`` (the sandbox
    ``MEETING_NOTES.md`` capture, surfaced host-side), authenticated with the internal admin bearer
    and scoped to ``cfg.meeting_id``. ``heard`` filters the parsed ``lines`` to the SAID play window;
    ``notes`` returns the full raw capture (its non-emptiness is the "transcript was captured" proof).
    Both degrade to empty when the meeting id / token is unset or the tap is unreachable — the bundle
    records the gap honestly rather than faking data. ``artifacts`` stays a post-smoke stub (leg 5).
    """
    base = cfg.control_plane_url.rstrip("/")
    headers = {"X-Internal-Token": cfg.internal_token} if cfg.internal_token else {}

    def _fetch_transcript() -> dict[str, Any] | None:
        """One authenticated read of the live meeting's transcript capture (``None`` on any gap)."""
        import asyncio

        if not cfg.meeting_id:
            return None

        async def _pull() -> dict[str, Any] | None:
            return await _get_json(
                f"{base}/admin/transcript", {"meeting_id": cfg.meeting_id}, headers
            )

        try:
            return asyncio.run(_pull())
        except Exception:
            return None

    def heard(from_epoch: float, to_epoch: float) -> list[dict[str, Any]]:
        data = _fetch_transcript()
        if not data:
            return []
        lines = [dict(x) for x in data.get("lines", []) if isinstance(x, dict)]
        # Scope to the SAID play window when the lines carry usable timestamps; a line with ts==0
        # (unparsable / pre-clock) is kept so a captured-but-untimed transcript still shows as HEARD.
        return [
            ln
            for ln in lines
            if not isinstance(ln.get("ts"), (int, float))
            or ln["ts"] == 0
            or from_epoch <= float(ln["ts"]) <= to_epoch
        ]

    def notes() -> str:
        # The sandbox MEETING_NOTES.md capture surfaced host-side (the same read heard() uses); a
        # non-empty raw body is the "transcript was captured" proof (bundle notes_captured=True).
        data = _fetch_transcript()
        return str(data.get("raw", "") or "") if data else ""

    def artifacts() -> dict[str, Any]:
        # The real diff / run output the tools produced in the E2B sandbox.
        # LIVE-TUNING-NEEDED (post-smoke leg 5): wire to an E2B ``files.read`` / ``git diff`` tap.
        return {}

    return heard, notes, artifacts
