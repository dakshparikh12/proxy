"""Secret / excluded-path management + read-path redaction (M3).

Two containment layers (invariant 3 — no raw secret in graph, results, sandbox,
or logs):

* **Exclusion** — files that are secret-bearing *by path* (``.env``, ``secrets.*``,
  keys, plus caller policy globs) are dropped from the graph, every tool result,
  and the sandbox. Gitleaks is run on the changed set after each clone/pull as the
  scan trigger; the deterministic path/content scan decides membership.
* **Redaction** — secret *values* found inline in otherwise-legitimate source are
  replaced with ``[REDACTED]`` on every read path (batch_read, find_references).
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Protocol

_DEFAULT_SECRET_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.env",
    "secrets.*",
    "credentials",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_rsa.*",
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(
        r"(?i)(?:secret|token|password|api[_-]?key|access[_-]?key|apikey)\w*"
        r"\s*[=:]\s*['\"]?([A-Za-z0-9/+=_\-]{8,})['\"]?"
    ),
)

_REDACTION = "[REDACTED]"


class GitleaksLike(Protocol):
    def record(self, paths: Any = ...) -> None: ...


def _matches_glob(rel: str, glob: str) -> bool:
    if glob.endswith("/"):
        return rel == glob.rstrip("/") or rel.startswith(glob)
    base = rel.rsplit("/", 1)[-1]
    return fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(base, glob)


class ExclusionManager:
    def __init__(
        self,
        gitleaks: GitleaksLike | None = None,
        policy_globs: list[str] | None = None,
    ) -> None:
        self._gitleaks = gitleaks
        self._policy_globs = list(policy_globs or [])
        self._excluded_extra: set[str] = set()
        self._secret_values: set[str] = set()

    def set_policy_globs(self, globs: list[str] | None) -> None:
        if globs:
            self._policy_globs = list(globs)

    # -- scans ------------------------------------------------------------ #
    def scan_after_clone(self, clone_path: Path) -> None:
        changed = [str(p.relative_to(clone_path)) for p in clone_path.rglob("*") if p.is_file()]
        self._scan(clone_path, changed)

    def scan_after_pull(self, clone_path: Path, changed_files: list[str] | None) -> None:
        changed = list(changed_files or [])
        # A changed secret-bearing config file is excluded even before it lands.
        for rel in changed:
            if self._is_secret_path(rel):
                self._excluded_extra.add(rel)
        self._scan(clone_path, changed)

    def _scan(self, clone_path: Path, changed: list[str]) -> None:
        if self._gitleaks is not None:
            self._gitleaks.record(changed)
        for rel in changed:
            f = clone_path / rel
            if not f.is_file():
                continue
            if self._is_secret_path(rel):
                self._excluded_extra.add(rel)
                continue
            self._collect_secret_values(f)

    def _collect_secret_values(self, f: Path) -> None:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for pat in _SECRET_PATTERNS:
            for m in pat.finditer(text):
                value = m.group(m.lastindex) if m.lastindex else m.group(0)
                if value:
                    self._secret_values.add(value)

    # -- queries ---------------------------------------------------------- #
    def _is_secret_path(self, rel: str) -> bool:
        globs = (*_DEFAULT_SECRET_GLOBS, *self._policy_globs)
        return any(_matches_glob(rel, g) for g in globs)

    def get_excluded_paths(self, clone_path: Path) -> set[str]:
        excluded = set(self._excluded_extra)
        if clone_path.exists():
            for p in clone_path.rglob("*"):
                if not p.is_file():
                    continue
                rel = str(p.relative_to(clone_path))
                if self._is_secret_path(rel):
                    excluded.add(rel)
        return excluded

    def is_excluded(self, rel: str) -> bool:
        return rel in self._excluded_extra or self._is_secret_path(rel)

    def redact(self, text: str | None) -> str | None:
        if not text:
            return text
        out = text
        for value in self._secret_values:
            out = out.replace(value, _REDACTION)
        for pat in _SECRET_PATTERNS:
            out = pat.sub(
                lambda m: (m.group(0).replace(m.group(m.lastindex), _REDACTION) if m.lastindex else _REDACTION),
                out,
            )
        return out
