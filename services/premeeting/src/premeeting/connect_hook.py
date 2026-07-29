"""The connect→map-build hook — the pre-meeting system's live entry from the connect trigger.

The connect trigger (``control_plane.connect.trigger_connect_index``) already clones + indexes a
tenant's repo (the graph pipeline that still feeds the Scribe referent seam). This hook runs the
pre-meeting MAP build ADDITIVELY on the SAME already-materialised clone: build the bounded map
→ store it durably in ``repo_maps`` → verify. It is deliberately thin and injection-driven so it
composes with the sync trigger and is testable with a fake model provider.

**The model seam is out of credits (D-032).** ``run_map_build_for_clone`` drives the real
build/store/verify path but needs a ``provider`` (the model seam). When none is supplied (the
live connect trigger today, since the key is unfunded) it NO-OPS honestly — the graph index +
readiness are unaffected; the map is simply not built until a funded provider is wired. It never
fabricates a map. A funded deployment injects the real ``ClaudeAgentProvider`` and the map builds.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exclusions import ExclusionManager
from .gitio import head_sha
from .map_build import build_map
from .verify import verify_map


@dataclass
class MapBuildOutcome:
    """The hook outcome — whether a map was built + stored, and the honest verify verdict."""

    built: bool
    ready: bool = False
    sha: str = ""
    reasons: list[str] | None = None


async def run_map_build_for_clone(
    *,
    tenant_id: str,
    repo: str,
    clone_path: Path,
    provider: Any | None,
    map_store: Any | None,
    sha: str | None = None,
    model: str = "claude-sonnet",
    exclusions: ExclusionManager | None = None,
) -> MapBuildOutcome:
    """Build + store + verify the map for an ALREADY-CLONED repo (never raises out).

    ``provider`` is the model seam; ``None`` no-ops honestly (the map is not built — D-032, the
    key is unfunded on the live path). Fake at ``provider`` in a test to exercise the real
    build/store/verify path. Returns an honest outcome; a fault degrades to ``built=False``,
    never a crash on the connect flow."""
    if provider is None:
        # Honest no-op: no funded model seam → no map (never a fabricated one). The graph index
        # + readiness the connect trigger already produced are unaffected.
        return MapBuildOutcome(built=False, reasons=["map-build skipped: no model provider (D-032)"])
    clone = Path(clone_path)
    if not clone.exists() or not any(clone.iterdir()):
        return MapBuildOutcome(built=False, reasons=["no clone to map"])
    try:
        em = exclusions if exclusions is not None else _scanned(clone)
        resolved_sha = sha or head_sha(clone) or ""
        result = await build_map(
            provider=provider, clone_path=clone, repo_name=repo, sha=resolved_sha,
            model=model, exclusions=em,
        )
        if map_store is not None:
            await map_store.save(
                tenant_id=tenant_id, repo=repo, sha=resolved_sha, map_text=result.index_md
            )
        verdict = verify_map(result.index_md, clone, exclusions=em)
        return MapBuildOutcome(
            built=True, ready=verdict.ready, sha=resolved_sha,
            reasons=None if verdict.ready else list(verdict.reasons),
        )
    except Exception as exc:  # noqa: BLE001 - Rule 6: a map-build fault never fails the connect flow
        return MapBuildOutcome(built=False, reasons=[f"map-build error: {type(exc).__name__}: {exc}"])


def _scanned(clone: Path) -> ExclusionManager:
    em = ExclusionManager()
    if clone.exists():
        em.scan_after_clone(clone)
    return em


__all__ = ["MapBuildOutcome", "run_map_build_for_clone"]
