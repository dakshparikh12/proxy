"""Offline tests for config resolution + the optional Langfuse fallback source."""
from __future__ import annotations

import pytest

from harness.config import build_config, missing_live_keys
from harness.langfuse import LangfuseClient, normalize_trace


def test_config_args_win_over_env(monkeypatch) -> None:
    monkeypatch.setenv("MEETING_URL", "from-env")
    cfg = build_config(meeting_url="from-arg", load_env=False)
    assert cfg.meeting_url == "from-arg"


def test_config_defaults_and_run_id(monkeypatch) -> None:
    for key in ("MEETING_URL", "RECALL_API_KEY", "RUN_ID"):
        monkeypatch.delenv(key, raising=False)
    cfg = build_config(load_env=False)
    assert cfg.run_id.startswith("run-")
    assert cfg.replicas == 2
    assert cfg.run_dir == cfg.runs_root / cfg.run_id


def test_missing_live_keys_named(monkeypatch) -> None:
    for key in (
        "MEETING_URL", "RECALL_OUTPUT_MEDIA_URL", "RECALL_API_KEY",
        "CARTESIA_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = build_config(load_env=False)
    missing = missing_live_keys(cfg)
    assert "MEETING_URL" in missing and "RECALL_API_KEY" in missing


def test_normalize_trace_reads_vs_cache_and_timing() -> None:
    raw = {
        "id": "t1", "name": "wake",
        "timestamp": "2026-08-05T00:00:00.000Z",
        "observations": [
            {"type": "GENERATION", "name": "reason",
             "startTime": "2026-08-05T00:00:00.000Z",
             "endTime": "2026-08-05T00:00:00.500Z", "output": "thinking..."},
            {"type": "SPAN", "name": "Read", "input": "route.ts",
             "startTime": "2026-08-05T00:00:00.500Z",
             "endTime": "2026-08-05T00:00:00.900Z"},
            {"type": "SPAN", "name": "to_meeting", "input": "say gist",
             "startTime": "2026-08-05T00:00:00.900Z",
             "endTime": "2026-08-05T00:00:01.000Z"},
        ],
    }
    view = normalize_trace(raw)
    assert view.read_count == 1  # the Read span
    assert view.answered_from_cache is False
    assert "route.ts" in view.read_targets[0]
    assert view.tools_called == ("Read", "to_meeting")
    assert view.thinking == ("thinking...",)
    assert view.latency_ms == pytest.approx(1000.0, abs=1.0)


@pytest.mark.asyncio
async def test_langfuse_client_uses_injected_fetch() -> None:
    calls: list[tuple[str, str]] = []

    async def fake_fetch(method, path, params):  # noqa: ANN001, ANN202
        calls.append((method, path))
        if path == "/api/public/traces":
            return {"data": [{"id": "t1"}]}
        return {"id": "t1", "name": "wake", "observations": []}

    client = LangfuseClient(
        base_url="https://lf", public_key="pk", secret_key="sk", fetch=fake_fetch
    )
    views = await client.traces_in_window("A", "B")
    assert len(views) == 1 and views[0].trace_id == "t1"
    # listed, then fetched the trace by id.
    assert ("GET", "/api/public/traces") in calls
    assert ("GET", "/api/public/traces/t1") in calls
