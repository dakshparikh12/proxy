"""Deterministic, model-free map verification — the readiness gate (PM-VERIFY-01..04).

The map is trustworthy ONLY if every claim in it is real. This gate is deterministic (no model)
and fail-closed: it emits ``ready`` ONLY on a full pass, and any failure yields ``not_ready``
NAMING the gap (Law 1/2 — never a silent pass). The five checks:

  1. non-empty + has ALL six required sections (a shell is not a map);
  2. every file/dir PATH it names EXISTS in the clone — no hallucinated path (Law 1, PM-VERIFY-01);
  3. every top-level TRACKED directory is covered by the map (PM-VERIFY-02);
  4. no secret value / secret-path leaks into the map (PM-VERIFY-03);
  5. ``ready`` only on a clean pass; each failure names its reason (PM-VERIFY-04).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .exclusions import ExclusionManager
from .gitio import list_tracked_files
from .map_build import REQUIRED_SECTIONS

# A "path-shaped token" in the map: a slash-bearing or dotted-extension run, or a bare top-level
# name mentioned as a path. We extract candidate paths conservatively (only tokens that LOOK like
# repo paths) so prose never trips a false hallucination flag, but any real named path is checked.
_PATH_TOKEN_RX = re.compile(r"[A-Za-z0-9_.\-/]+")
# Tokens we never treat as a path claim (prose words, urls, section words).
_NON_PATH = frozenset({"e.g", "i.e", "etc", "vs", "http", "https"})


@dataclass
class VerifyResult:
    """The gate outcome — ``ready`` iff every check passed; else the NAMED gaps (Law 1/2)."""

    ready: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "ready" if self.ready else "not_ready"


def _looks_like_path(tok: str) -> bool:
    """True iff ``tok`` looks like a repo path claim (has a ``/`` or a file extension)."""
    if tok in _NON_PATH:
        return False
    # URL-ish tokens are never repo paths: a scheme-relative ``//cal.com/docs`` or an embedded
    # ``://`` is a URL the map cites in prose (``//cal.com``, ``github.com/calcom/cal.com``),
    # not a file/dir claim — treating it as one flags every real map that names a link (BUG 2).
    if "://" in tok or tok.startswith("//"):
        return False
    if "/" in tok:
        return True
    # a dotted name with a short, alpha extension (``server.py``, ``go.mod``) — but not a version
    # (``2.11``) or a sentence-ending word (``thing.``).
    if "." in tok:
        ext = tok.rsplit(".", 1)[-1]
        return bool(ext) and ext.isalpha() and 1 <= len(ext) <= 5
    return False


def extract_named_paths(map_text: str) -> set[str]:
    """The set of path-shaped tokens the map names (candidate paths to check for existence).

    Strips a trailing ``/`` (a dir named ``src/``) and a trailing punctuation so ``src/,`` and
    ``server.py.`` normalise to the real path. Only path-SHAPED tokens are returned — prose is
    ignored so a faithful map never false-flags (PM-VERIFY-01)."""
    named: set[str] = set()
    # ignore fenced code / the title line's ``@ <sha>`` etc — scan every token, keep path-shaped.
    for raw in _PATH_TOKEN_RX.findall(map_text):
        tok = raw.strip().rstrip("/").rstrip(".,;:)")
        tok = tok.lstrip("(")
        if not tok or not _looks_like_path(tok):
            continue
        named.add(tok)
    return named


def _path_exists_in_clone(clone_path: Path, rel: str) -> bool:
    """True iff ``rel`` (a map-named path) resolves to a real file/dir inside the clone.

    Confined to the clone (a ``..`` escape or absolute path is never satisfied), so a map cannot
    "prove" a path by naming something outside the tenant volume."""
    root = clone_path.resolve()
    candidate = (root / rel).resolve()
    if root != candidate and root not in candidate.parents:
        return False
    return candidate.exists()


def _top_level_entries(clone_path: Path) -> set[str]:
    """The names of the top-level files/dirs in the clone (an anchor for a slash-path claim)."""
    try:
        return {p.name for p in clone_path.iterdir()}
    except OSError:
        return set()


# Last-segment values a DOTTED token can carry that mean it is a URL/domain/framework name, not a
# repo path — a bare-domain TLD (``cal.com`` → ``com``) or a domain-shaped framework (``Next.js`` →
# ``js`` but the token is a domain-style CamelCase name, handled by the top-level anchor below).
# The map's prose routinely cites the product/framework/a bare host, so these must never be treated
# as fabricated file paths (BUG 2). This is a URL-TLD list, NOT a file-extension denylist.
_DOMAIN_TLDS = frozenset(
    {"com", "org", "io", "net", "dev", "co", "ai", "app", "gg", "sh", "xyz", "me", "so"}
)


def _is_path_claim(tok: str, top_entries: set[str]) -> bool:
    """True iff ``tok`` is a genuine repo-path CLAIM the hallucination check should verify (BUG 2).

    Two false-positive families the old ``named ⇒ must-exist`` rule wrongly flagged, now excluded:

    * a bare DOMAIN name — a dotted token whose last segment is a URL TLD (``cal.com`` → ``com``,
      ``github.com`` → ``com``). Its last segment is a TLD, not a file extension, so it is a host
      the map cites, never a file that must exist. (URL forms with ``://``/``//`` were already
      dropped upstream in :func:`_looks_like_path`.)
    * a SLASH token whose FIRST segment is a bare domain — ``github.com/calcom/cal.com`` (first
      segment ``github.com`` is a domain, not a real top-level entry in the clone). A cited repo
      URL is prose, not a fabricated path.

    Genuine-path detection is intact: ``src/server.py`` (first segment ``src`` — a real dir, not a
    domain), a genuinely-missing ``foo/bar.ts`` (first segment ``foo`` — not a domain, so it IS a
    claim and still flags), and a real top-level ``server.py`` all remain claims and are checked.
    """
    if "/" in tok:
        first = tok.split("/", 1)[0]
        # A slash token anchored on a bare domain first-segment is a cited URL, not a path claim —
        # unless that first segment is actually a real top-level entry in the clone (never a domain).
        if _is_bare_domain(first) and first not in top_entries:
            return False
        return True
    # A slash-less token names a TOP-LEVEL entry or nothing: it is a path claim ONLY when it is a
    # real top-level entry in the clone (a real top-level file like ``server.py`` / ``go.mod``).
    # A cited bare domain (``cal.com``) or framework name (``Next.js``) is not a top-level entry, so
    # it is prose, never a fabricated file — this is what stops the false hallucination flag (BUG 2)
    # without weakening nested-path detection (``foo/bar.ts`` above still flags).
    return tok in top_entries


def _is_bare_domain(tok: str) -> bool:
    """True iff ``tok`` is a dotted host/domain name (last segment is a URL TLD, e.g. ``cal.com``)."""
    if "." not in tok:
        return False
    return tok.rsplit(".", 1)[-1].lower() in _DOMAIN_TLDS


def verify_map(
    map_text: str,
    clone_path: Path,
    *,
    exclusions: ExclusionManager | None = None,
) -> VerifyResult:
    """Run the five deterministic checks and return ``ready``/``not_ready`` with named gaps.

    Fail-closed: ``ready`` is emitted ONLY when every check passes; the FIRST failing check adds
    a specific, named reason (never a silent pass, PM-VERIFY-04). Model-free — the same input
    always yields the same verdict."""
    reasons: list[str] = []

    # (1) non-empty + all six sections.
    if not map_text or not map_text.strip():
        return VerifyResult(ready=False, reasons=["map is empty"])
    missing = [s for s in REQUIRED_SECTIONS if f"## {s}" not in map_text]
    if missing:
        reasons.append(f"missing sections: {', '.join(missing)}")

    named = extract_named_paths(map_text)

    # (4) no secret leak — a named excluded/secret PATH, or an inline secret VALUE.
    if exclusions is not None:
        leaked_paths = sorted(p for p in named if exclusions.is_excluded(p))
        if leaked_paths:
            reasons.append(f"secret path leaked into map: {', '.join(leaked_paths)}")
        for value in exclusions.secret_values():
            if value and value in map_text:
                reasons.append("secret value leaked into map")
                break

    # (2) every named path EXISTS in the clone (no hallucination). Excluded paths are handled by
    # (4); here we check the rest resolve to real files/dirs. A cited URL / bare domain / framework
    # name is path-SHAPED but not a path CLAIM (BUG 2) — ``_is_path_claim`` (anchored on the clone's
    # top-level entries) drops those so a faithful map that names ``cal.com`` / ``Next.js`` / a repo
    # URL never false-flags, while a genuinely-missing ``foo/bar.ts`` still does.
    if clone_path.exists():
        excluded = exclusions.is_excluded if exclusions is not None else (lambda _p: False)
        top_entries = _top_level_entries(clone_path)
        hallucinated = sorted(
            p
            for p in named
            if _is_path_claim(p, top_entries)
            and not excluded(p)
            and not _path_exists_in_clone(clone_path, p)
        )
        if hallucinated:
            shown = ", ".join(hallucinated[:10])
            reasons.append(f"map names paths not in the clone: {shown}")

        # (3) every top-level TRACKED directory is covered by the map.
        uncovered = _uncovered_top_dirs(map_text, clone_path, exclusions)
        if uncovered:
            reasons.append(f"top-level dirs not covered by map: {', '.join(sorted(uncovered))}")
    else:
        reasons.append("clone path does not exist")

    return VerifyResult(ready=not reasons, reasons=reasons)


def _uncovered_top_dirs(
    map_text: str, clone_path: Path, exclusions: ExclusionManager | None
) -> set[str]:
    """The top-level TRACKED directories the map fails to mention by name (PM-VERIFY-02).

    The tracked set (``git ls-files``) is the file universe; a top-level dir is "covered" iff its
    name appears anywhere in the map text. Excluded/secret dirs are never required to be covered
    (naming them would be a leak)."""
    tracked = list_tracked_files(clone_path)
    if tracked is None:
        return set()
    top_dirs: set[str] = set()
    for rel in tracked:
        if "/" in rel:
            top = rel.split("/", 1)[0]
            if exclusions is not None and exclusions.is_excluded(rel):
                continue
            top_dirs.add(top)
    return {d for d in top_dirs if not re.search(rf"\b{re.escape(d)}\b", map_text)}


__all__ = ["VerifyResult", "extract_named_paths", "verify_map"]
