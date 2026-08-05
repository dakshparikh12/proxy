"""The operator CLI — the fix→replay loop, one chunk at a time.

Commands (each maps to a step; the pause is that YOU call the next one):

    test-provision        put Proxy into the Meet WITHOUT OAuth (real invite_proxy) →
                          prints the meeting_id to monitor (needs PROXY_INTERNAL_TOKEN)
    setup                 join the replica bots + confirm Proxy is present
    play-chunk  CP-N      play chunk N's beats (honoring gates); log SAID
    bundle      CP-N      assemble + store chunk N's monitoring bundle
    replay-chunk CP-N     re-play chunk N (after a fix)
    teardown              remove the replica bots
    smoke                 tiny end-to-end pipe check (1 replica → Proxy → trace)
    chunks                list the parsed chunks (offline; no network)

Config comes from ``.env`` + flags (``--meeting-url``, ``--origin``,
``--control-plane``, ``--replicas``, ``--run-id``). A live command that is
missing a required credential/URL fails fast, naming what is missing.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .config import HarnessConfig, build_config, missing_live_keys
from .monitor import Monitor
from .record import RecordStore
from .transcript import Chunk, load_transcript

if TYPE_CHECKING:
    from .driver import Driver

# The default speakers who are REPLICA bots (Daksh "drives" and is not a bot).
_DEFAULT_SPEAKERS = ["Pranav", "Riya"]
# Where the host writes wake records + run.log inside the (mounted) sandbox view.
_WAKE_OUT_ENV = "PROXY_WAKE_OUT"


def _load_chunks(cfg: HarnessConfig) -> list[Chunk]:
    return load_transcript(cfg.transcript_path)


def _require_live(cfg: HarnessConfig) -> None:
    missing = missing_live_keys(cfg)
    if missing:
        raise SystemExit(
            "missing required config for a live command: " + ", ".join(missing) + "\n"
            "set them in .env or pass the matching flags."
        )


def _speakers(args: argparse.Namespace) -> list[str]:
    if args.speakers:
        return [s.strip() for s in args.speakers.split(",") if s.strip()]
    return list(_DEFAULT_SPEAKERS)


def _build_driver(cfg: HarnessConfig, args: argparse.Namespace) -> "Driver":
    """Construct the live driver (real replicas). Imports the live wiring lazily."""
    from .driver import Driver
    from .live import build_proxy_probes
    from .replica import build_live_replicas

    replicas = build_live_replicas(
        _speakers(args),
        recall_api_key=cfg.recall_api_key,
        cartesia_api_key=cfg.cartesia_api_key,
        output_media_origin=cfg.output_media_origin,
    )
    proxy_speaking, _ = build_proxy_probes(cfg)
    driver = Driver(
        _load_chunks(cfg),
        {r.speaker: r for r in replicas},
        meeting_url=cfg.meeting_url,
        run_dir=cfg.run_dir,
        proxy_speaking=proxy_speaking,
    )
    return driver


def _record_store(cfg: HarnessConfig) -> RecordStore:
    """The DID source: the host's wake records + run.log usage trail."""
    import os

    wake_out = Path(os.environ.get(_WAKE_OUT_ENV, str(cfg.run_dir / "wake_out")))
    run_log = cfg.run_dir / "run.log"
    return RecordStore(wake_out, run_log if run_log.exists() else None)


def _build_monitor(cfg: HarnessConfig) -> Monitor:
    from .live import build_monitor_sources

    heard, notes, artifacts = build_monitor_sources(cfg)
    return Monitor(
        _load_chunks(cfg),
        _record_store(cfg),
        cfg.run_dir,
        heard_source=heard,
        notes_source=notes,
        artifact_source=artifacts,
    )


# ── command handlers ─────────────────────────────────────────────────────────


def cmd_chunks(cfg: HarnessConfig, args: argparse.Namespace) -> int:
    for chunk in _load_chunks(cfg):
        print(
            f"{chunk.checkpoint:6s} Part {chunk.part}: "
            f"{len(chunk.beats):3d} beats, {len(chunk.playable_beats):3d} playable "
            f"| {chunk.title}"
        )
    return 0


def cmd_setup(cfg: HarnessConfig, args: argparse.Namespace) -> int:
    _require_live(cfg)
    from .live import build_proxy_probes

    driver = _build_driver(cfg, args)
    _, confirm_proxy = build_proxy_probes(cfg)
    joined = asyncio.run(driver.setup(confirm_proxy=confirm_proxy))
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    (cfg.run_dir / "replicas.json").write_text(json.dumps(joined, indent=2), encoding="utf-8")
    print(f"joined replicas: {joined}")
    print(f"run dir: {cfg.run_dir}")
    return 0


def cmd_play(cfg: HarnessConfig, args: argparse.Namespace) -> int:
    _require_live(cfg)
    driver = _build_driver(cfg, args)
    playback = asyncio.run(driver.play_chunk(args.checkpoint))
    print(f"played {args.checkpoint}: {len(playback.said)} lines → {cfg.run_dir / args.checkpoint}")
    for s in playback.said:
        note = f"  [{s.note}]" if s.note else ""
        print(f"  [{s.timestamp}] {s.speaker} ({s.gate}): {s.line[:60]}{note}")
    return 0


def cmd_replay(cfg: HarnessConfig, args: argparse.Namespace) -> int:
    _require_live(cfg)
    driver = _build_driver(cfg, args)
    playback = asyncio.run(driver.replay_chunk(args.checkpoint))
    print(f"replayed {args.checkpoint}: {len(playback.said)} lines")
    return 0


def cmd_bundle(cfg: HarnessConfig, args: argparse.Namespace) -> int:
    monitor = _build_monitor(cfg)
    bundle = monitor.bundle(args.checkpoint)
    out = cfg.run_dir / args.checkpoint
    print(f"bundle stored: {out / 'bundle.json'}")
    print(f"summary:       {out / 'summary.txt'}")
    from .monitor import render_summary

    print()
    print(render_summary(bundle))
    return 0


def cmd_teardown(cfg: HarnessConfig, args: argparse.Namespace) -> int:
    _require_live(cfg)
    driver = _build_driver(cfg, args)
    asyncio.run(driver.teardown())
    print("teardown: replica bots removed")
    return 0


def cmd_smoke(cfg: HarnessConfig, args: argparse.Namespace) -> int:
    _require_live(cfg)
    from .smoke import run_smoke

    ok = asyncio.run(run_smoke(cfg))
    print("SMOKE: PASS" if ok else "SMOKE: FAIL — see output above")
    return 0 if ok else 1


def cmd_test_provision(cfg: HarnessConfig, args: argparse.Namespace) -> int:
    """Drive the control-plane's dev-only ``POST /admin/test-provision`` (skip OAuth).

    Puts Proxy into ``cfg.meeting_url`` via the REAL invite path (real Recall bot + the drain's real
    E2B workroom) using the internal admin bearer instead of a Google session. Prints the meeting_id
    to monitor and the exact ``MEETING_ID=…`` line to export for the HEARD/transcript taps."""
    from .live import provision_proxy

    if not cfg.meeting_url:
        raise SystemExit("MEETING_URL is required (set it in .env or pass --meeting-url).")
    if not cfg.internal_token:
        raise SystemExit(
            "PROXY_INTERNAL_TOKEN is required for test-provision — set it in .env AND on the "
            "control-plane process, then re-run."
        )
    repo = getattr(args, "repo", None) or ""
    if not repo:
        raise SystemExit("--repo is required, e.g. --repo pgoel813/cova")
    result = asyncio.run(provision_proxy(cfg, repo=repo))
    if result is None:
        print("test-provision: FAILED — see the control-plane log (no meeting_id returned)")
        return 1
    print(f"test-provision: OK — Proxy is joining {cfg.meeting_url}")
    print(f"  meeting_id : {result.get('meeting_id')}")
    print(f"  bot_id     : {result.get('bot_id')}")
    print(f"  pinned_sha : {result.get('pinned_sha')}  (indexed={result.get('indexed')})")
    print()
    print("  To monitor this meeting, export:")
    print(f"    export MEETING_ID={result.get('meeting_id')}")
    print("  (the HEARD/transcript taps read this; the DID trace lands in $PROXY_WAKE_OUT)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness", description="Proxy live-test harness")
    parser.add_argument("--meeting-url", dest="meeting_url")
    parser.add_argument("--origin", dest="output_media_origin", help="public output-media origin")
    parser.add_argument("--control-plane", dest="control_plane_url")
    parser.add_argument("--meeting-id", dest="meeting_id", help="live meeting id for the HEARD taps")
    parser.add_argument("--internal-token", dest="internal_token", help="PROXY_INTERNAL_TOKEN bearer")
    parser.add_argument("--replicas", type=int)
    parser.add_argument("--run-id", dest="run_id")
    parser.add_argument("--speakers", help="comma-separated replica speakers (default Pranav,Riya)")

    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "teardown", "smoke", "chunks"):
        sub.add_parser(name)
    tp = sub.add_parser("test-provision")
    tp.add_argument("--repo", help="repo full_name/url to bind, e.g. pgoel813/cova")
    for name in ("play-chunk", "replay-chunk", "bundle"):
        p = sub.add_parser(name)
        p.add_argument("checkpoint", help="e.g. CP-1")
    return parser


_HANDLERS = {
    "chunks": cmd_chunks,
    "setup": cmd_setup,
    "play-chunk": cmd_play,
    "replay-chunk": cmd_replay,
    "bundle": cmd_bundle,
    "teardown": cmd_teardown,
    "smoke": cmd_smoke,
    "test-provision": cmd_test_provision,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = build_config(
        meeting_url=args.meeting_url,
        output_media_origin=args.output_media_origin,
        control_plane_url=args.control_plane_url,
        meeting_id=args.meeting_id,
        internal_token=args.internal_token,
        replicas=args.replicas,
        run_id=args.run_id,
    )
    handler = _HANDLERS[args.command]
    return handler(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
