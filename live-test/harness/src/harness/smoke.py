"""``smoke`` — the tiny end-to-end pipe check before committing to the full run.

Proves the whole primitive chain works on real infra: ONE replica bot joins,
speaks "Proxy, can you hear me?" (Cartesia → its Output-Media channel → the page
Recall plays as the bot's mic), Proxy hears via real STT and responds, and the
monitor pulls Proxy's turn record (the trace). If every leg lights up, the full
transcript run is safe to start.

Legs verified:
1. replica joins (Recall bot id returned),
2. replica speaks (PCM chunks written to its channel),
3. Proxy responds (a wake record appears with a ``sent`` say),
4. the trace is captured (the record parses; tools/timing present).

This is a LIVE command (needs real creds + a real meeting URL); it is not part of
the offline suite. It reuses the same driver + monitor the full run uses.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from .config import HarnessConfig
from .driver import Driver
from .record import RecordStore
from .replica import build_live_replicas
from .transcript import Acceptance, Beat, Chunk, Gate

_SMOKE_LINE = "Proxy, can you hear me?"
_SMOKE_SPEAKER = "Riya"
#: How long to wait for Proxy's wake record to appear after the smoke line.
_WAKE_WAIT_S = 60.0
_POLL_S = 1.0


def _smoke_chunk() -> Chunk:
    beat = Beat(
        timestamp="T+00:00",
        speaker=_SMOKE_SPEAKER,
        gate=Gate.SPEAK_NOW,
        line=_SMOKE_LINE,
        is_proxy=False,
        is_stage=False,
        acceptance=Acceptance(process="respond to a direct address", routing="voice"),
        scenario_ids=("SMOKE-01",),
    )
    return Chunk(checkpoint="SMOKE", part="S", title="smoke", beats=(beat,))


async def run_smoke(cfg: HarnessConfig) -> bool:
    """Run the end-to-end smoke check; return True iff every leg lit up."""
    print("smoke: building 1 replica + driver ...")
    replicas = build_live_replicas(
        [_SMOKE_SPEAKER],
        recall_api_key=cfg.recall_api_key,
        cartesia_api_key=cfg.cartesia_api_key,
        output_media_origin=cfg.output_media_origin,
    )
    driver = Driver(
        [_smoke_chunk()],
        {r.speaker: r for r in replicas},
        meeting_url=cfg.meeting_url,
        run_dir=cfg.run_dir,
    )

    print(f"smoke: joining {_SMOKE_SPEAKER} to {cfg.meeting_url} ...")
    joined = await driver.setup()
    print(f"smoke: leg 1/4 OK — replica joined: {joined}")

    print(f'smoke: speaking "{_SMOKE_LINE}" ...')
    playback = await driver.play_chunk("SMOKE")
    said = playback.said[0] if playback.said else None
    if said is None or said.chunks_written <= 0:
        print("smoke: leg 2/4 FAIL — no audio chunks written to the replica channel")
        await driver.teardown()
        return False
    print(f"smoke: leg 2/4 OK — {said.chunks_written} PCM chunks pushed to '{said.channel_id}'")

    wake_out = Path(os.environ.get("PROXY_WAKE_OUT", str(cfg.run_dir / "wake_out")))
    store = RecordStore(wake_out)
    print(f"smoke: waiting up to {_WAKE_WAIT_S:.0f}s for Proxy's wake record ...")
    deadline = time.time() + _WAKE_WAIT_S
    new_records = []
    while time.time() < deadline:
        new_records = store.records_after(playback.started_at)
        if new_records:
            break
        await asyncio.sleep(_POLL_S)

    if not new_records:
        print("smoke: leg 3/4 FAIL — no wake record appeared (did Proxy hear it?)")
        await driver.teardown()
        return False
    rec = new_records[-1]
    print(
        f"smoke: leg 3/4 OK — wake {rec.wake_id}: tools={list(rec.tools)} "
        f"ttft={rec.ttft_ms}ms deliver={rec.deliver_at_ms}ms"
    )

    spoke = any(i.medium in {"say", "chat"} for i in rec.sent)
    if not spoke:
        print("smoke: leg 4/4 WARN — record has no say/chat send; check the relay")
    else:
        print(f"smoke: leg 4/4 OK — Proxy responded via {[i.medium for i in rec.sent]}")

    await driver.teardown()
    return bool(new_records) and spoke
