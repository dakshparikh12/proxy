#!/usr/bin/env python3
"""watch_live — the founder's real-time meeting monitor (read-only).

Prints one line per event as it happens:
  [HH:MM:SS] HEARD   <speaker>: <text>            (STT line captured into notes)
  [HH:MM:SS] WAKE    queued=..ms ttft=..s spoke_at=..s tools=[...]
  [HH:MM:SS] REPLY   "<what it said/streamed>"    (or SILENT — judged no response)
  [HH:MM:SS] AUDIO   TTS call #n                  (voice actually synthesized)

Run:  .venv/bin/python live-test/watch_live.py <meeting_id>
Env:  PROXY_INTERNAL_TOKEN (defaults to the smoke token), CONTROL_PLANE_URL, PROXY_WAKE_OUT.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.request

MEETING = sys.argv[1] if len(sys.argv) > 1 else ""
BASE = os.environ.get("CONTROL_PLANE_URL", "http://localhost:8080")
TOKEN = os.environ.get("PROXY_INTERNAL_TOKEN", "proxy-smoke-2026-internal")
WAKE_OUT = pathlib.Path(
    os.environ.get(
        "PROXY_WAKE_OUT",
        "/Users/daksh/Desktop/proxy/live-test/live-runs/smoke/wake_out",
    )
)
CP_LOG = pathlib.Path("/Users/daksh/.claude/jobs/db65e0b5/tmp/cp.log")

if not MEETING:
    print("usage: watch_live.py <meeting_id>")
    sys.exit(1)


def now() -> str:
    return time.strftime("%H:%M:%S")


def fetch_lines() -> list[dict]:
    req = urllib.request.Request(
        f"{BASE}/admin/transcript?meeting_id={MEETING}",
        headers={"X-Internal-Token": TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            return json.load(r).get("lines", [])
    except Exception:
        return []


def main() -> None:
    seen_lines = 0
    seen_records: set[str] = set()
    tts_seen = 0
    print(f"— watching meeting {MEETING} (Ctrl-C to stop) —")
    while True:
        # HEARD: new transcript lines
        lines = fetch_lines()
        for line in lines[seen_lines:]:
            print(f"[{now()}] HEARD   {line.get('speaker')}: {line.get('text')}")
        if lines:
            seen_lines = len(lines)

        # WAKE/REPLY: new wake records
        if WAKE_OUT.is_dir():
            for f in sorted(WAKE_OUT.glob("*.json")):
                if f.name in seen_records:
                    continue
                seen_records.add(f.name)
                try:
                    d = json.loads(f.read_text())
                except Exception:
                    continue
                print(
                    f"[{now()}] WAKE    queued={d.get('queued_ms')}ms "
                    f"ttft={d.get('ttft')}s spoke_at={d.get('deliver_at')}s "
                    f"tools={d.get('tools')}"
                )
                text = (d.get("text") or "").strip()
                if text.upper().startswith("[SILENT]"):
                    print(f"[{now()}] REPLY   SILENT (judged: no response) :: {text[:120]}")
                else:
                    print(f'[{now()}] REPLY   "{text[:220]}"')

        # AUDIO: TTS synth calls (from the server log)
        try:
            tts = CP_LOG.read_text().count("api.cartesia.ai/tts/bytes")
            while tts_seen < tts:
                tts_seen += 1
                print(f"[{now()}] AUDIO   TTS call #{tts_seen}")
        except Exception:
            pass

        time.sleep(1.0)


if __name__ == "__main__":
    main()
