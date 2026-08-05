"""Offline tests for the monitor — bundle assembly from faked sources, no network."""
from __future__ import annotations

import json

from harness.monitor import Monitor, render_summary
from harness.record import RecordStore
from harness.transcript import Acceptance, Beat, Chunk, Gate

_CACHE_RECORD = {
    "tools": ["to_meeting"],
    "text": "entrypoint is route.ts:12",
    "cost_usd": 0.01, "turns": 1, "error": None,
    "deliver_at": 1.0, "ttft": 0.6,
    "sent": [{"content": "gist", "medium": "say", "to": ""},
             {"content": "detail", "medium": "chat", "to": ""}],
    "_served_at": 2000.0,
}
_READ_RECORD = {
    "tools": ["Read", "to_meeting"],
    "text": "tail detail", "cost_usd": 0.02, "turns": 2, "error": None,
    "deliver_at": 2.0, "ttft": 0.9,
    "sent": [{"content": "dm to riya", "medium": "dm", "to": "Riya"}],
    "_served_at": 2005.0,
}


def _chunk() -> Chunk:
    return Chunk(
        checkpoint="CP-2", part="B", title="oriented",
        beats=(
            Beat(
                timestamp="T+03:10", speaker="Daksh", gate=Gate.SPEAK_NOW,
                line="Proxy, where's the entrypoint?", is_proxy=False, is_stage=False,
                acceptance=Acceptance(
                    scn="G2-01 (entrypoint).",
                    process="`zero-read-cache` answer from resident understanding.",
                    routing="voice-gist.", output="grounded file:line.",
                ),
                scenario_ids=("G2-01",),
            ),
        ),
    )


def _seed(tmp_path):  # noqa: ANN202
    """A run dir with a SAID log + a wake_out with two records + a run.log."""
    run_dir = tmp_path / "run"
    cp = run_dir / "CP-2"
    cp.mkdir(parents=True)
    said = {
        "checkpoint": "CP-2", "started_at": 1999.0, "finished_at": 2001.0,
        "said": [{"timestamp": "T+03:10", "speaker": "Daksh", "gate": "speak-now",
                  "line": "Proxy, where's the entrypoint?", "channel_id": "",
                  "chunks_written": 0, "played_at": 2000.0,
                  "note": "SKIPPED: no replica bot for this speaker"}],
    }
    (cp / "said-1999000.json").write_text(json.dumps(said), encoding="utf-8")

    wake_out = run_dir / "wake_out"
    wake_out.mkdir()
    (wake_out / "w1.json").write_text(json.dumps(_CACHE_RECORD), encoding="utf-8")
    (wake_out / "w2.json").write_text(json.dumps(_READ_RECORD), encoding="utf-8")
    run_log = run_dir / "run.log"
    run_log.write_text(
        "[usage] cache_read=0 cache_write=800 input=1 output=1\n"
        "[usage] cache_read=800 cache_write=0 input=1 output=1\n",
        encoding="utf-8",
    )
    return run_dir, RecordStore(wake_out, run_log)


def test_bundle_assembles_all_sources(tmp_path) -> None:
    run_dir, store = _seed(tmp_path)

    def heard(frm, to):  # noqa: ANN001, ANN202
        assert frm == 1999.0
        return [{"speaker": "Daksh", "text": "Proxy, where's the entrypoint?"}]

    def notes() -> str:
        return "# MEETING_NOTES\nDaksh: entrypoint?\n"

    def artifacts():  # noqa: ANN202
        return {"diff": "--- a\n+++ b\n", "run_output": "3 passed"}

    monitor = Monitor([_chunk()], store, run_dir,
                      heard_source=heard, notes_source=notes, artifact_source=artifacts)
    bundle = monitor.bundle("CP-2")

    # SAID present.
    assert bundle.said["said"][0]["speaker"] == "Daksh"
    # HEARD + notes captured.
    assert bundle.heard[0]["text"].startswith("Proxy")
    assert bundle.notes_captured is True
    # DID: both records windowed (served_at >= 1999).
    did = bundle.did["records"]
    assert {r["wake_id"] for r in did} == {"w1", "w2"}
    cache_rec = next(r for r in did if r["wake_id"] == "w1")
    assert cache_rec["answered_from_cache"] is True
    read_rec = next(r for r in did if r["wake_id"] == "w2")
    assert read_rec["read_count"] == 1 and read_rec["read_tools"] == ["Read"]
    # OUTPUT: every channel choice across records.
    mediums = {o["medium"] for o in bundle.output}
    assert mediums == {"say", "chat", "dm"}
    dm = next(o for o in bundle.output if o["medium"] == "dm")
    assert dm["to"] == "Riya"
    # RESIDENCY: growing trail + declared zero-read beat surfaced.
    assert bundle.residency["cache_growing"] is True
    assert bundle.residency["cache_read_trail"] == [0, 800]
    assert bundle.residency["zero_read_expected_beats"]
    # ARTIFACTS.
    assert bundle.artifacts["run_output"] == "3 passed"
    # ACCEPTANCE declared, with the zero-read flag lifted.
    row = bundle.acceptance[0]
    assert row["expects_zero_read"] is True


def test_bundle_persisted_to_disk(tmp_path) -> None:
    run_dir, store = _seed(tmp_path)
    monitor = Monitor([_chunk()], store, run_dir)
    monitor.bundle("CP-2")
    assert (run_dir / "CP-2" / "bundle.json").exists()
    summary = (run_dir / "CP-2" / "summary.txt").read_text(encoding="utf-8")
    assert "DID —" in summary
    assert "CACHE (0 reads)" in summary  # the cache record rendered


def test_bundle_without_said_log_warns_not_crashes(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = RecordStore(run_dir / "wake_out")  # no records
    monitor = Monitor([_chunk()], store, run_dir)
    bundle = monitor.bundle("CP-2")
    assert "warning" in bundle.said
    assert bundle.did["records"] == []


def test_render_summary_is_readable(tmp_path) -> None:
    run_dir, store = _seed(tmp_path)
    monitor = Monitor([_chunk()], store, run_dir)
    text = render_summary(monitor.bundle("CP-2"))
    assert "SAID —" in text and "HEARD —" in text and "OUTPUT —" in text
    assert "RESIDENCY —" in text and "ACCEPTANCE (declared)" in text
