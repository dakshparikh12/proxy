"""Secret / excluded-path management + read-path redaction (PM-CLONE-04 / PM-VERIFY-03).

Two containment layers (Law 1/2 — no raw secret in the map, in a tool result, or in a log):

* **Exclusion** — files that are secret-bearing *by path* (``.env``, ``secrets.*``, keys, plus
  caller policy globs) are dropped from every read path and every result. ``scan_after_clone``
  runs after each clone/pull so a planted secret is caught before any read.
* **Redaction** — secret *values* found inline in otherwise-legitimate source are replaced with
  ``[REDACTED]`` on every read path (the toolbelt's read/grep/batch_read).

This is the SAME containment code_intel used; it is ported verbatim into premeeting so the
map-build read path and the live toolbelt share one secret boundary, and so the graph-build
modules can be deleted without taking the secret boundary with them.
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
    # Inline credential in a connection URI (``scheme://USER:PASSWORD@host``) — a UNIVERSAL secret
    # shape the name-anchored pattern above misses, because the variable name (``MONGODB_URL``,
    # ``DATABASE_URL``) carries none of the ``secret|token|password`` keywords. The captured group is
    # the WHOLE userinfo ``user:password`` (both halves are credential material — a DB username is not
    # public), so collecting + redacting it scrubs the credential from the symbol map / read paths AND
    # the verified comprehension prose, leaving ``scheme://[REDACTED]@host`` (host/db still legible).
    # Found by the WS6 obscure-repo certification (a hard-coded ``mongodb://user:pass@host`` at
    # models.py:1 was surfaced verbatim). Requires the ``:`` (a userinfo password) so a bare
    # ``scheme://host`` (no credential) never matches.
    re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://([^\s/@]+:[^\s/@]+)@"),
)

_REDACTION = "[REDACTED]"

# A ``secret|token|...=<rhs>`` capture whose RHS is a plain lowercase CODE IDENTIFIER (a snake_case
# / lowercase symbol name) is a source symbol, not a secret literal — e.g. click's public
# ``token_normalize_func`` parameter matched the ``token…=…`` pattern and, being named in the map,
# falsely tripped "secret value leaked" and failed verify on a perfectly good map (BUG 4). The guard
# fires ONLY when the value is letters+underscores AND carries a LOWERCASE letter: a real credential
# is either high-entropy (digits / ``/+=-``) or an ALL-CAPS blob (``AKIAABCDEFGHIJKLMNOP``), neither
# of which is a lowercase source symbol — so a genuine secret value is still collected + redacted
# (verified by ``test_pm_verify_03``). This narrows ONLY the identifier false-positive.
_CODE_IDENTIFIER_RX = re.compile(r"^[A-Za-z][A-Za-z_]*$")


def _is_code_identifier(value: str) -> bool:
    """True iff ``value`` is a lowercase-bearing letters/underscores source symbol (never a secret).

    Requires a lowercase letter so an ALL-CAPS credential blob (``AKIAABCDEFGHIJKLMNOP``) is NOT
    treated as an identifier and is still collected as a secret, while ``token_normalize_func`` is."""
    if not _CODE_IDENTIFIER_RX.match(value):
        return False
    return any(c.islower() for c in value)


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
                if value and not _is_code_identifier(value):
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

    def secret_values(self) -> set[str]:
        """The set of inline secret VALUES collected on the scan — verify uses this to prove
        no secret value leaked into the map (PM-VERIFY-03)."""
        return set(self._secret_values)

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
