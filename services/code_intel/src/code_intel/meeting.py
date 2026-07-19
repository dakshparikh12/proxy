"""Per-meeting session: SHA pin + tool cache + advance notices (M5/M7/M11).

A session pins the head SHA at start and answers every tool call against that
retained graph version — a mid-meeting push advances the head and emits a "repo
advanced N commits" notice but never mutates the pinned answers (AC-M7-004/005/006).
The per-meeting cache returns stored results for identical args and is invalidated
on push (AC-M5-014/015).
"""
from __future__ import annotations

from typing import Any

from .results import Notification


class MeetingSession:
    def __init__(
        self,
        server: Any = None,
        pipeline: Any = None,
        pinned_sha: str | None = None,
    ) -> None:
        self._server = server if server is not None else (pipeline.server if pipeline is not None else None)
        self._pipeline = pipeline if pipeline is not None else (
            server.pipeline if server is not None else None
        )
        self.pinned_sha: str = pinned_sha if pinned_sha is not None else self._resolve_pin()
        self.notifications: list[Notification] = []
        self._cache: dict[tuple[Any, ...], Any] = {}
        self._cache_gen = self._server.cache_generation if self._server is not None else 0
        if self._pipeline is not None and hasattr(self._pipeline, "register_pin"):
            self._pipeline.register_pin(self)

    def _resolve_pin(self) -> str:
        if self._pipeline is not None:
            return str(self._pipeline.current_sha)
        if self._server is not None:
            return str(self._server.current_sha)
        return ""

    @classmethod
    def start(
        cls,
        pipeline: Any = None,
        server: Any = None,
        event_log: Any = None,
        pr_number: int | None = None,
    ) -> MeetingSession:
        pinned_sha: str | None = None
        if pr_number is not None and pipeline is not None:
            pinned_sha = _pr_head_sha(pipeline, pr_number)
        session = cls(server=server, pipeline=pipeline, pinned_sha=pinned_sha)
        drift = getattr(pipeline, "_drift", None) if pipeline is not None else None
        if drift is not None and event_log is not None:
            event_log.record("pull")
            event_log.record("graph_rebuild")
            event_log.record("readiness_confirmed")
        return session

    # -- cache-aware tool dispatch --------------------------------------- #
    def tool_call(self, tool: str, **args: Any) -> Any:
        gen = self._server.cache_generation if self._server is not None else 0
        if gen != self._cache_gen:
            self._cache.clear()
            self._cache_gen = gen
        key = (tool, tuple(sorted(args.items())))
        if key in self._cache:
            return self._cache[key]
        result = self._invoke(tool, args)
        self._cache[key] = result
        return result

    def _invoke(self, tool: str, args: dict[str, Any]) -> Any:
        server = self._server
        if server is None:
            raise RuntimeError("MeetingSession has no server bound")
        graph = None
        sha = None
        if self._pipeline is not None and hasattr(self._pipeline, "graph_for"):
            graph = self._pipeline.graph_for(self.pinned_sha)
            sha = self.pinned_sha
        if tool == "get_dependents":
            return server.get_dependents(args["symbol"], args.get("limit"), _graph=graph, _sha=sha)
        if tool == "list_entry_points":
            return server.list_entry_points(_graph=graph, _sha=sha)
        if tool == "who_writes":
            return server.who_writes(args["table"], _graph=graph, _sha=sha)
        if tool == "shares_table":
            return server.shares_table(args["table"], _graph=graph, _sha=sha)
        if tool == "owner":
            return server.owner(args["path"], _graph=graph, _sha=sha)
        if tool == "find_references":
            return server.find_references(args["symbol"])
        if tool == "batch_read":
            return server.batch_read(args["paths"], args.get("max_lines_per_file"))
        raise ValueError(f"unknown tool {tool!r}")

    # -- freshness hooks -------------------------------------------------- #
    def on_repo_advanced(self, num_commits: int) -> None:
        self.invalidate()
        self.notifications.append(Notification(text=f"repo advanced {num_commits} commits"))

    def invalidate(self) -> None:
        self._cache.clear()
        if self._server is not None:
            self._cache_gen = self._server.cache_generation

    def end(self) -> None:
        if self._pipeline is not None and hasattr(self._pipeline, "unregister_pin"):
            self._pipeline.unregister_pin(self)


def _pr_head_sha(pipeline: Any, pr_number: int) -> str | None:
    """Resolve the tip SHA for a PR branch from the clone's git metadata."""
    from pathlib import Path

    from .gitio import run_git

    clone_path = getattr(pipeline, "clone_path", None)
    if not isinstance(clone_path, Path) or not clone_path.exists():
        return None
    gitdir = clone_path.parent / ".git"
    if not gitdir.exists():
        return None
    # Bare clone stores remote branches as refs/heads/<branch>; try common patterns.
    for ref in (
        f"refs/heads/feature/pr-{pr_number}",
        f"refs/heads/pr/{pr_number}",
        f"refs/remotes/origin/feature/pr-{pr_number}",
        f"refs/remotes/origin/pr/{pr_number}",
    ):
        res = run_git(["--git-dir", str(gitdir), "rev-parse", ref], check=False)
        if res.returncode == 0:
            sha = res.stdout.strip()
            if sha:
                return sha
    return None
