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


# URL TLDs a dotted LAST segment can carry — a cited host (``github.com/calcom/cal.com`` → last
# segment ``cal.com`` → ``com``) is a URL the map cites in prose, never a source file whose
# extension proves a path claim. Excluding these from the "ends in a source extension" test stops a
# cited repo URL from false-flagging as a fabricated path (BUG 2/4). A URL-TLD list, NOT a
# file-extension denylist.
_DOMAIN_TLDS = frozenset(
    {"com", "org", "io", "net", "dev", "co", "ai", "app", "gg", "sh", "xyz", "me", "so"}
)


# Conventional TOOLING dot-dirs a navigation map is NOT required to cover: they carry CI / editor /
# dev-container config, not the code Proxy navigates in a meeting. Requiring the map to name every
# one of them (``.devcontainer`` / ``.vscode`` / …) failed verify on a genuinely good map (BUG 4) —
# a hallucinated REFERENCE to one is still caught by the path-existence check; this exemption only
# relaxes the COVERAGE requirement for non-navigational config dirs.
_COVERAGE_EXEMPT_DIRS = frozenset(
    {".github", ".devcontainer", ".vscode", ".idea", ".circleci", ".husky", ".changeset"}
)


def _is_source_file_ext(last: str) -> bool:
    """True iff the last path segment ends in a real FILE extension — a lowercase, short, alpha suffix
    (``core.py``→``py``, ``x.ts``→``ts``, ``go.mod``→``mod``). NOT a code SYMBOL reference the map
    cites in prose (``json.API``, ``bytesconv.StringToBytes``, ``Engine.ServeHTTP`` — package/type
    ``.Symbol``, which is CamelCase/UPPER, not a file), a leading-dot attribute ref (``.group`` —
    empty stem), a version (``3.11`` — non-alpha), or a URL TLD (``cal.com`` — a cited host). File
    extensions are lowercase; a symbol suffix is not — that one rule separates real files from the
    code symbols a codebase map naturally references."""
    if "." not in last:
        return False
    stem, ext = last.rsplit(".", 1)
    return (
        bool(stem)          # a real file has a NAME before the ext (drops leading-dot attr refs)
        and ext.isalpha()
        and ext.islower()   # file exts are lowercase; CamelCase/UPPER (.API, .ServeHTTP) = a symbol
        and 1 <= len(ext) <= 5
        and ext not in _DOMAIN_TLDS
    )


def _is_path_claim(tok: str, top_entries: set[str]) -> bool:
    """True iff ``tok`` is a genuine repo FILE/DIR path CLAIM the hallucination check should verify —
    NOT the map's path-shaped PROSE (class enumerations ``BaseCommand/Command``, versions
    ``3.10/3.11``, shorthands ``read/write``, cited URLs ``github.com/…``, bare domains ``cal.com``,
    frameworks ``Next.js``) and NOT a code SYMBOL reference (``codec/json.API``,
    ``internal/bytesconv.StringToBytes`` — a ``package/path.Symbol`` a codebase map naturally names).
    Each of these false-flagged a genuinely-good map and blocked onboarding — found incrementally by
    the live-meeting sim (``/.group`` on click; ``json.API`` / ``bytesconv.StringToBytes`` on gin)."""
    # An absolute-looking token (leading ``/``) is never a RELATIVE repo-path claim — a map's paths
    # are relative to the clone root (``/.group``, ``/usr/local`` are prose/absolute).
    if tok.startswith("/"):
        return False
    if "/" in tok:
        first = tok.split("/", 1)[0]
        last = tok.rsplit("/", 1)[-1]
        if "." in last:
            # A dotted last segment is a FILE claim ONLY if it ends in a real (lowercase) file
            # extension. A ``package/path.Symbol`` reference (``codec/json.API``,
            # ``internal/bytesconv.StringToBytes``) is a CODE SYMBOL the map cites — NOT a file, even
            # when its first segment IS a real dir — and a cited URL ends in a domain TLD. Neither is
            # a fabricated file. A genuinely-missing ``foo/bar.ts`` (real ext) still flags.
            return _is_source_file_ext(last)
        # No dot in the last segment → a directory / extensionless-file path: a claim only when
        # anchored on a real top-level entry (``internal/bytesconv``, ``src/click``,
        # ``.github/workflows``). The map's slash-bearing prose (``BaseCommand/Command``,
        # ``read/write``, ``bash/zsh``) has a non-top-entry first segment, so it is not a claim.
        return first in top_entries
    # A slash-less token is a claim ONLY if it IS a real top-level entry (a real top file
    # ``server.py`` / ``go.mod``). A bare domain (``cal.com``) or framework (``Next.js``) is not a
    # top entry, so it is prose — never a fabricated file.
    return tok in top_entries


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
        # A cited file that EXISTS in the repo — even at a slightly different directory than named
        # (Go internal packages cited as ``bytesconv/bytesconv.go`` when the file is
        # ``internal/bytesconv/…``, or a monorepo package abbreviation) — is grounded, NOT a
        # hallucination: the map named a REAL file, and the agent reads the actual file live. Only a
        # name that resolves NOWHERE in the repo is a fabrication (Law 1). So a path is hallucinated
        # iff neither its exact path NOR its basename resolves to a tracked file. (Found by the
        # repo-diversity sim: gin/Go false-flagged real ``internal/`` files.)
        tracked_names = {rel.rsplit("/", 1)[-1] for rel in (list_tracked_files(clone_path) or [])}
        hallucinated = sorted(
            p
            for p in named
            if _is_path_claim(p, top_entries)
            and not excluded(p)
            and not _path_exists_in_clone(clone_path, p)
            and p.rsplit("/", 1)[-1] not in tracked_names
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
            if top in _COVERAGE_EXEMPT_DIRS:
                continue  # non-navigational tooling dot-dir — not required in the map (BUG 4)
            top_dirs.add(top)
    return {d for d in top_dirs if not _dir_mentioned(d, map_text)}


def _dir_mentioned(name: str, map_text: str) -> bool:
    """True iff a top-level dir ``name`` is named anywhere in the map (dot-dir safe).

    A ``\\b<name>\\b`` regex CANNOT match a DOT-DIR (``.github`` / ``.devcontainer``): ``\\b``
    requires a word/non-word transition, but ``.`` is itself a non-word char, so ``\\b\\.github``
    never anchors — the map named ``.github/workflows/`` yet the dir read as "uncovered", falsely
    failing verify (BUG 4). Anchor on the LAST word run of the name instead (``github`` for
    ``.github``), bounded by a word boundary on the trailing side, so a dot-dir the map mentions is
    correctly seen as covered while a mere substring inside a longer word still does not count."""
    core = name.lstrip(".")
    if not core:
        return name in map_text
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(core)}\b", map_text) is not None


__all__ = ["VerifyResult", "extract_named_paths", "verify_map"]
