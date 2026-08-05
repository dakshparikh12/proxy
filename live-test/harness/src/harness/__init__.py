"""Proxy live-test harness — operator-driven, chunk-by-chunk meeting replay.

Plays a scripted meeting (``live-test/MEETING_TRANSCRIPT.md``) into a real Proxy
meeting: two "replica" Recall bots speak the transcript lines (Cartesia TTS →
Recall Output-Media webpage — the SAME primitives ``services/in-meeting`` uses),
Proxy hears via real STT and responds. The operator drives it one chunk at a
time, reads the trace, grades PROCESS/ROUTING against the declared acceptance,
fixes generalizably, replays, continues.

The four monitor surfaces per chunk:

* **SAID**   — the driver's beat log (what the replicas spoke).
* **HEARD**  — Proxy's STT for the window (from the control-plane meeting stream).
* **DID**    — the Langfuse trace(s) for the wake(s): tools, reads-vs-cache,
  where it looked, thinking, ms timing. THE key output — the acceptance grader.
* **OUTPUT** — what Proxy spoke/posted/showed/offered (from the relay/meeting).

Every vendor round-trip rides the existing seams (``libs.http.call_external`` +
the ``services/in-meeting`` transport), so nothing here holds a raw vendor client
and an offline mock test exercises the whole flow.
"""
from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.0.0"
