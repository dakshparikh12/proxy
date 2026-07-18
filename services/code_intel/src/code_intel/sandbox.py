"""E2B sandbox preparation + tool manifest (M3/M10).

Excluded (secret-bearing) paths are never copied into the sandbox. The sandbox
tool surface is disjoint from the host surface and carries no LSP/reference tools
(precise navigation is host-side only, AC-SANDBOX-001/002).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .results import ToolManifest

SANDBOX_TOOL_NAMES = ("run_shell", "apply_patch", "write_file", "list_dir")


def get_sandbox_tool_manifest() -> ToolManifest:
    return ToolManifest(tool_names=list(SANDBOX_TOOL_NAMES))


class Sandbox:
    def __init__(self, pipeline: Any) -> None:
        self._pipeline = pipeline

    def file_list(self) -> list[str]:
        clone: Path = self._pipeline.clone_path
        if not clone or not clone.exists():
            return []
        excluded = self._pipeline.exclusion_manager.get_excluded_paths(clone)
        files: list[str] = []
        for p in clone.rglob("*"):
            if not p.is_file() or ".git" in p.parts:
                continue
            rel = str(p.relative_to(clone))
            if rel in excluded:
                continue
            files.append(rel)
        return files


def prepare_sandbox(pipeline: Any) -> Sandbox:
    return Sandbox(pipeline)
