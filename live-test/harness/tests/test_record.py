"""Offline tests for the DID record source (the primary acceptance grader)."""
from __future__ import annotations

import json

from harness.record import (
    RecordStore,
    RecordWindow,
    UsageSample,
    parse_record,
    parse_usage_lines,
)

_CACHE_RECORD = {
    "tools": ["to_meeting"],  # answered + delivered, NO file read
    "text": "The entrypoint is app/api/pipeline/redesign/route.ts:12.",
    "cost_usd": 0.01,
    "turns": 1,
    "error": None,
    "deliver_at": 1.2,
    "ttft": 0.8,
    "sent": [{"content": "The entrypoint is ...", "medium": "say", "to": ""}],
    "_served_at": 1000.0,
}
_READ_RECORD = {
    "tools": ["Read", "Grep", "to_meeting"],  # two disk reads before answering
    "text": "Found it.",
    "cost_usd": 0.02,
    "turns": 2,
    "error": None,
    "deliver_at": 3.5,
    "ttft": 1.1,
    "sent": [{"content": "detail", "medium": "chat", "to": ""}],
    "_served_at": 1010.0,
}


def test_zero_read_record_is_answered_from_cache() -> None:
    rec = parse_record("w1", _CACHE_RECORD)
    assert rec.read_count == 0
    assert rec.answered_from_cache is True
    assert rec.mediums == ("say",)
    assert rec.ttft_ms == 800.0
    assert rec.deliver_at_ms == 1200.0


def test_read_record_counts_disk_reads_and_names_them() -> None:
    rec = parse_record("w2", _READ_RECORD)
    assert rec.read_count == 2
    assert rec.answered_from_cache is False
    assert set(rec.read_tools) == {"Read", "Grep"}
    # to_meeting is a DELIVERY channel, never counted as a read.
    assert "to_meeting" not in rec.read_tools


def test_error_record_surfaced_honestly() -> None:
    rec = parse_record("w3", {**_CACHE_RECORD, "error": "turn did not complete"})
    assert rec.error == "turn did not complete"


def test_usage_line_parsing_and_growth() -> None:
    log = (
        "some boot noise\n"
        "[usage] cache_read=0 cache_write=1200 input=1500 output=40\n"
        "[usage] cache_read=1200 cache_write=0 input=1600 output=55\n"
        "[usage] cache_read=1800 cache_write=0 input=1700 output=30\n"
    )
    samples = parse_usage_lines(log)
    assert [s.cache_read for s in samples] == [0, 1200, 1800]
    window = RecordWindow(usage=tuple(samples))
    assert window.cache_growing is True


def test_cache_not_growing_flagged() -> None:
    window = RecordWindow(
        usage=(
            UsageSample(cache_read=2000, cache_write=0, input_tokens=1, output_tokens=1),
            UsageSample(cache_read=1000, cache_write=0, input_tokens=1, output_tokens=1),
        )
    )
    assert window.cache_growing is False


def test_record_store_reads_and_windows(tmp_path) -> None:
    wake_out = tmp_path / "wake_out"
    wake_out.mkdir()
    (wake_out / "w1.json").write_text(json.dumps(_CACHE_RECORD), encoding="utf-8")
    (wake_out / "w2.json").write_text(json.dumps(_READ_RECORD), encoding="utf-8")
    # breadcrumbs + temp files must be ignored.
    (wake_out / "_host.ready").write_text("123", encoding="utf-8")
    (wake_out / ".w9.json.tmp").write_text("{}", encoding="utf-8")
    run_log = tmp_path / "run.log"
    run_log.write_text(
        "[usage] cache_read=0 cache_write=900 input=1 output=1\n"
        "[usage] cache_read=900 cache_write=0 input=1 output=1\n",
        encoding="utf-8",
    )

    store = RecordStore(wake_out, run_log)
    all_recs = store.all_records()
    assert [r.wake_id for r in all_recs] == ["w1", "w2"]  # sorted by served_at

    # window scoped to after w1's serve keeps only w2.
    later = store.records_after(1005.0)
    assert [r.wake_id for r in later] == ["w2"]

    window = store.window(since_epoch=0.0)
    assert len(window.records) == 2
    assert window.cache_growing is True
    assert window.to_dict()["cache_read_trail"] == [0, 900]


def test_missing_wake_dir_is_empty_not_error(tmp_path) -> None:
    store = RecordStore(tmp_path / "nope")
    assert store.all_records() == []
    assert store.window().records == ()
