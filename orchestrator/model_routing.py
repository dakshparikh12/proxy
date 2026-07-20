#!/usr/bin/env python3
"""Model-routing loader (Task 7) — the ONE place the pipeline resolves task-type -> model id.

Reads orchestrator/model-routing.json (the Superpowers pattern as declarative config, not the
plugin). Every model choice across generation, building, and verification goes through `model_for`,
so re-tiering a task (e.g. moving the extraction-count audit from opus to sonnet) is a one-line JSON
edit — never a code change scattered across orchestrate.py.

    model_for("reality-critic")  -> "claude-sonnet-4-6"
    tier("opus")                 -> "claude-opus-4-8"
"""
from __future__ import annotations

import json
import pathlib

_CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "model-routing.json"


def _load() -> dict:
    return json.loads(_CONFIG_PATH.read_text())


def tier(name: str) -> str:
    """Concrete model id for a tier name (sonnet|opus|haiku)."""
    cfg = _load()
    tiers = cfg.get("tiers", {})
    if name not in tiers:
        raise KeyError(f"unknown model tier {name!r}; known: {sorted(tiers)}")
    return tiers[name]


def tier_for(task_type: str) -> str:
    """Tier name a task-type routes to (falls back to the configured default tier)."""
    cfg = _load()
    return cfg.get("routes", {}).get(task_type, cfg.get("default", "sonnet"))


def model_for(task_type: str) -> str:
    """Concrete model id for a pipeline task-type (route -> tier -> id; default tier if unrouted)."""
    return tier(tier_for(task_type))


if __name__ == "__main__":
    cfg = _load()
    print(f"model-routing.json — tiers: {cfg.get('tiers')}")
    for t in sorted(cfg.get("routes", {})):
        print(f"  {t:24s} -> {tier_for(t):7s} -> {model_for(t)}")
    print(f"  {'(default)':24s} -> {cfg.get('default')}")
