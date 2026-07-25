"""STT credential refresh — an availability-critical IN-PROCESS interval loop (§3.8).

Doc 04 §3.8 splits periodic work two ways: **cost-driven reaping** (stale harnesses,
ended-meeting sandboxes) rides the scale-to-zero reconcile cron (Cloud Scheduler every
5min); **availability-critical loops** — keeping a *live* meeting's STT/Recall
credentials fresh — stay on an in-process interval where a warm instance provably
exists (a live meeting is being served, so ``min_instances≥1``). This module is that
in-process loop: a scaled-to-zero instance runs no interval, so putting the STT refresh
on the reconcile cron would let a live meeting's transcription credentials go stale.

STT is AssemblyAI Universal-Streaming with our key configured in Recall (BYOK, Doc 02
§3.2), so the concrete refresh is a periodic re-assertion of the credential the audio
path (Recall→AssemblyAI) uses — supplied as the injectable ``refresh_fn`` seam so this
loop is drivable in a test and bound to the real credential rotation in production.

The loop is availability-critical, so it degrades honestly and NEVER dies silently: a
single failed refresh (a transient credential-endpoint blip) is logged and the loop
keeps trying on its interval. A loop that died on the first raise would silently
degrade transcription mid-meeting — the exact failure mode this split exists to avoid.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from libs.db import stt_refresh_interval_s

_log = logging.getLogger(__name__)


async def refresh_stt_credentials(
    refresh_fn: Callable[[], Awaitable[None]],
    *,
    interval_s: float | None = None,
) -> None:
    """Refresh STT (AssemblyAI) credentials forever on an in-process interval (§3.8).

    Runs ``refresh_fn`` immediately, then every ``interval_s`` seconds (falling back to
    the configured ``stt_refresh_interval_s()`` cadence). A refresh that raises is
    swallowed and logged — the loop keeps its cadence rather than dying, so a live
    meeting's transcription credentials never go stale on a single transient blip. The
    loop ends only when its task is cancelled (meeting end tears it down).
    """
    cadence = (
        float(interval_s) if interval_s is not None else float(stt_refresh_interval_s())
    )
    while True:
        try:
            await refresh_fn()
        except asyncio.CancelledError:
            # Meeting-end teardown cancels the loop's task — propagate, never swallow.
            raise
        except Exception:  # noqa: BLE001 — availability-critical: degrade, never die.
            # A failed refresh must not kill the loop (it would silently degrade
            # transcription mid-meeting). Log for a human and keep the cadence.
            _log.exception("stt_credential_refresh_failed")
        await asyncio.sleep(cadence)


async def _noop_refresh() -> None:
    """The default refresh seam — a benign no-op (BYOK credential lives in Recall).

    STT is BYOK: our AssemblyAI key is configured in Recall, so the audio path needs no
    Proxy-side rotation in V0. The loop still runs at join so the seam is LIVE and wired
    (a real rotation binds here without touching the join path); the default keeps the
    availability-critical interval a real, running loop rather than an unwired capability.
    """
    return None
