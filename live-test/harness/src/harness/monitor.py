"""The monitor — ``bundle N`` assembles a chunk's monitoring bundle and stores it.

Per chunk we prove, from real sources:

* **SAID**      — the driver's beat log (what the replicas spoke).
* **HEARD**     — Proxy's STT for the window + the sandbox ``MEETING_NOTES.md``
                  capture (confirms the transcript was captured — even when Proxy
                  stayed silent / wasn't addressed).
* **DID**       — the ``session_host`` per-turn RECORDS for the window: tools
                  called, reads-vs-resident-cache (WHERE it answered from), ms
                  timing, and the cache_read trail (residency). THE grader.
* **ARTIFACTS** — the real diff / run output the tools produced in the E2B
                  sandbox (ground truth: it actually ran, not just "compiled").
* **OUTPUT**    — what Proxy spoke/posted/showed/offered (the records' ``sent``
                  plus any relay tap).
* **RESIDENCY** — cache_read growing turn-over-turn + the declared expectation
                  from the chunk's beats (which asks should be ZERO-read).

Every non-local source is a pluggable ``Callable`` so an offline test injects a
fake and the whole bundle is assembled with no network. The DID source
(``RecordStore``) is already local (files the host wrote), so the primary grader
is exercised for real offline.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .record import RecordStore, RecordWindow
from .transcript import Chunk

#: HEARD source: (from_epoch, to_epoch) -> list of {ts, speaker, text} STT lines.
HeardSource = Callable[[float, float], list[dict[str, Any]]]
#: The sandbox transcript capture (MEETING_NOTES.md) -> its full text (or "").
NotesSource = Callable[[], str]
#: Artifact tap: () -> {"diff": str, "run_output": str, ...} from the E2B sandbox.
ArtifactSource = Callable[[], dict[str, Any]]


@dataclass
class Bundle:
    """One chunk's assembled monitoring bundle (stored as JSON + a readable txt)."""

    checkpoint: str
    said: dict[str, Any]
    heard: list[dict[str, Any]]
    notes_captured: bool
    did: dict[str, Any]
    artifacts: dict[str, Any]
    output: list[dict[str, Any]]
    residency: dict[str, Any]
    acceptance: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint": self.checkpoint,
            "SAID": self.said,
            "HEARD": {"stt_lines": self.heard, "notes_captured": self.notes_captured},
            "DID": self.did,
            "ARTIFACTS": self.artifacts,
            "OUTPUT": self.output,
            "RESIDENCY": self.residency,
            "ACCEPTANCE_DECLARED": self.acceptance,
        }


def _acceptance_rows(chunk: Chunk) -> list[dict[str, Any]]:
    """The declared 4-part acceptance per beat — what the trace is graded against."""
    rows: list[dict[str, Any]] = []
    for beat in chunk.beats:
        rows.append(
            {
                "timestamp": beat.timestamp,
                "speaker": beat.speaker,
                "gate": beat.gate.value,
                "scenario_ids": list(beat.scenario_ids),
                "process": beat.acceptance.process,
                "routing": beat.acceptance.routing,
                "output_sane": beat.acceptance.output,
                # A quick heuristic flag: does the declared process expect ZERO reads?
                "expects_zero_read": "zero-read" in beat.acceptance.process.lower()
                or "resident" in beat.acceptance.process.lower(),
            }
        )
    return rows


def _output_from_records(window: RecordWindow) -> list[dict[str, Any]]:
    """OUTPUT: every channel choice the agent made across the window's records."""
    out: list[dict[str, Any]] = []
    for rec in window.records:
        for intent in rec.sent:
            out.append(
                {
                    "wake_id": rec.wake_id,
                    "medium": intent.medium,
                    "to": intent.to,
                    "content": intent.content,
                }
            )
    return out


def _residency_view(chunk: Chunk, window: RecordWindow) -> dict[str, Any]:
    """RESIDENCY: the cache trail + which wakes answered zero-read (declared vs actual)."""
    return {
        "cache_read_trail": [u.cache_read for u in window.usage],
        "cache_growing": window.cache_growing,
        "wakes": [
            {
                "wake_id": rec.wake_id,
                "read_count": rec.read_count,
                "answered_from_cache": rec.answered_from_cache,
                "read_tools": list(rec.read_tools),
            }
            for rec in window.records
        ],
        "zero_read_expected_beats": [
            {"timestamp": b.timestamp, "process": b.acceptance.process}
            for b in chunk.beats
            if "zero-read" in b.acceptance.process.lower()
            or "resident" in b.acceptance.process.lower()
        ],
    }


class Monitor:
    """Assembles + stores a chunk's monitoring bundle from all sources."""

    def __init__(
        self,
        chunks: list[Chunk],
        record_store: RecordStore,
        run_dir: Path,
        *,
        heard_source: HeardSource | None = None,
        notes_source: NotesSource | None = None,
        artifact_source: ArtifactSource | None = None,
    ) -> None:
        self._chunks = {c.checkpoint: c for c in chunks}
        self._records = record_store
        self._run_dir = run_dir
        self._heard = heard_source
        self._notes = notes_source
        self._artifacts = artifact_source

    def _latest_said(self, checkpoint: str) -> dict[str, Any] | None:
        """The most recent SAID playback dict for this chunk (the driver wrote it)."""
        chunk_dir = self._run_dir / checkpoint
        if not chunk_dir.exists():
            return None
        saids = sorted(chunk_dir.glob("said-*.json"))
        if not saids:
            return None
        raw: dict[str, Any] = json.loads(saids[-1].read_text(encoding="utf-8"))
        return raw

    def bundle(self, checkpoint: str) -> Bundle:
        """Assemble + store the bundle for ``checkpoint``.

        The DID window is scoped to the chunk's SAID play window (records served
        at/after the play start), so a replay's bundle reflects that replay's
        wakes.
        """
        if checkpoint not in self._chunks:
            raise KeyError(f"no chunk {checkpoint!r} (have {sorted(self._chunks)})")
        chunk = self._chunks[checkpoint]

        said_dict = self._latest_said(checkpoint)
        if said_dict is None:
            said_dict = {"warning": "no SAID log — play the chunk first"}
            since = 0.0
            to = float("inf")
        else:
            since = float(said_dict.get("started_at", 0.0))
            to = float(said_dict.get("finished_at", since))

        window = self._records.window(since_epoch=since)

        heard: list[dict[str, Any]] = []
        if self._heard is not None:
            heard = self._heard(since, to)

        notes_captured = False
        if self._notes is not None:
            notes_captured = bool(self._notes().strip())

        artifacts: dict[str, Any] = {}
        if self._artifacts is not None:
            artifacts = self._artifacts()

        bundle = Bundle(
            checkpoint=checkpoint,
            said=said_dict,
            heard=heard,
            notes_captured=notes_captured,
            did=window.to_dict(),
            artifacts=artifacts,
            output=_output_from_records(window),
            residency=_residency_view(chunk, window),
            acceptance=_acceptance_rows(chunk),
        )
        self._store(bundle)
        return bundle

    # -- persistence ---------------------------------------------------------

    def _store(self, bundle: Bundle) -> None:
        chunk_dir = self._run_dir / bundle.checkpoint
        chunk_dir.mkdir(parents=True, exist_ok=True)
        (chunk_dir / "bundle.json").write_text(
            json.dumps(bundle.to_dict(), indent=2), encoding="utf-8"
        )
        (chunk_dir / "summary.txt").write_text(render_summary(bundle), encoding="utf-8")


def render_summary(bundle: Bundle) -> str:
    """A readable, grader-facing summary of the bundle (the operator reads this)."""
    lines: list[str] = []
    lines.append(f"═══ {bundle.checkpoint} — monitoring bundle ═══")
    lines.append("")

    said = bundle.said.get("said", []) if isinstance(bundle.said, dict) else []
    lines.append(f"SAID — {len(said)} lines played:")
    for s in said:
        note = f"   [{s['note']}]" if s.get("note") else ""
        lines.append(f"  [{s['timestamp']}] {s['speaker']} ({s['gate']}): {s['line'][:70]}{note}")
    lines.append("")

    lines.append(f"HEARD — {len(bundle.heard)} STT lines; notes captured: {bundle.notes_captured}")
    for h in bundle.heard[:12]:
        lines.append(f"  {h.get('speaker', '?')}: {h.get('text', '')[:70]}")
    lines.append("")

    records = bundle.did.get("records", [])
    lines.append(f"DID — {len(records)} wake record(s):")
    for r in records:
        cache = "CACHE (0 reads)" if r["answered_from_cache"] else f"{r['read_count']} read(s)"
        tools = ", ".join(r["tools"]) or "(none)"
        lines.append(
            f"  {r['wake_id']}: tools=[{tools}] · {cache} · "
            f"ttft={r['ttft_ms']}ms deliver={r['deliver_at_ms']}ms · turns={r['turns']}"
            + (f" · ERROR={r['error']}" if r.get("error") else "")
        )
        if r["read_tools"]:
            lines.append(f"      looked: {', '.join(r['read_tools'])}")
    lines.append("")

    res = bundle.residency
    lines.append(
        f"RESIDENCY — cache_read trail {res['cache_read_trail']} · growing={res['cache_growing']}"
    )
    lines.append("")

    lines.append(f"OUTPUT — {len(bundle.output)} channel send(s):")
    for o in bundle.output:
        to = f" → {o['to']}" if o.get("to") else ""
        lines.append(f"  [{o['medium']}]{to}: {o['content'][:70]}")
    lines.append("")

    art = bundle.artifacts
    if art:
        diff = str(art.get("diff", ""))
        run_out = str(art.get("run_output", ""))
        lines.append("ARTIFACTS:")
        lines.append(f"  diff: {len(diff)} chars" + (" (present)" if diff else " (none)"))
        lines.append(f"  run_output: {len(run_out)} chars" + (" (present)" if run_out else " (none)"))
        lines.append("")

    lines.append("ACCEPTANCE (declared) — grade DID against these:")
    for a in bundle.acceptance:
        if not (a["process"] or a["routing"]):
            continue
        zr = " [expects ZERO-read]" if a["expects_zero_read"] else ""
        lines.append(f"  [{a['timestamp']}] {a['speaker']}{zr}")
        if a["process"]:
            lines.append(f"      PROCESS: {a['process'][:100]}")
        if a["routing"]:
            lines.append(f"      ROUTING: {a['routing'][:100]}")
    return "\n".join(lines) + "\n"
