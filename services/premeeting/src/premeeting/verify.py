"""Deterministic, model-free map verification — the readiness gate (PM-VERIFY-01..04).

The map is trustworthy ONLY if every claim in it is real. This gate is deterministic (no model)
and fail-closed: it emits ``ready`` ONLY on a full pass, and any failure yields ``not_ready``
NAMING the gap (Law 1/2 — never a silent pass). The stored artifact is EITHER the comprehension-first
resident doc (the qualitative comprehension on top + the compact ``# Navigation map`` beneath) OR,
on an honest degrade, the deterministic symbol map alone
(:func:`premeeting.symbol_map.build_symbol_map`). The shape check accepts whichever marker set is
present — the navigation markers (``# Navigation map`` / ``Where things live`` / ``Entry points``)
or the symbol-map markers (``# Symbol map`` / … / ``Ranked signatures``) — NOT the old six-section
prose shape. The five checks:

  1. non-empty + carries the symbol-map markers (a shell is not a map) — an honest "no parseable
     source symbols" map (a repo with no parseable code) passes: it makes no ungrounded claim;
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
from .symbol_map import REQUIRED_MAP_MARKERS, REQUIRED_NAV_MARKERS

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
    {
        # tooling / CI / editor dot-dirs — config, not navigational code (BUG 4)
        ".github", ".devcontainer", ".vscode", ".idea", ".circleci", ".husky", ".changeset",
        # conventional NON-navigational dirs a nav map need not enumerate: test fixtures, examples,
        # docs, vendored/generated code. The agent still greps them live if asked; requiring the map
        # to name EVERY one false-failed real maps of larger repos (repo-diversity sim: gin/Go has
        # docs/, examples/, testdata/). Real CODE dirs (src, internal, pkg, lib, cmd, app, services,
        # packages, …) are NOT exempt, so a map missing a major code area still flags.
        "testdata", "test-data", "fixtures", "testfixtures", "__fixtures__",
        "examples", "example", "samples", "demo", "demos",
        "docs", "doc", "documentation", "website", "site",
        "vendor", "third_party", "third-party", "node_modules", "dist", "build", "generated",
    }
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
    """True iff ``tok`` is a FABRICATABLE FILE reference the hallucination check should verify.

    SIMPLE, robust rule (replaced a fragile multi-branch path heuristic that kept false-flagging
    good maps — found incrementally by the repo-diversity sim: ``/.group`` on click; then
    ``bytesconv/bytesconv.go``, ``codec/json.API``, ``internal/bytesconv.StringToBytes``, and
    ``render/bind`` on gin/Go). verify's real job is catching a FABRICATED FILE the agent might cite
    (Law 1); the agent navigates the real clone LIVE, so an imprecise DIR, a code SYMBOL
    (``pkg/path.Symbol``), a shorthand (``render/bind``), or path-shaped prose in the map is
    self-correcting, not a fabrication. So:

    * a SLASH path is a file claim ONLY if it ends in a real (lowercase) file extension
      (``src/click/core.py`` ✓; ``render/bind`` ✗ no ext; ``codec/json.API`` ✗ symbol; a cited URL
      ``github.com/…/cal.com`` ✗ domain TLD; ``foo/.group`` ✗ empty stem);
    * a SLASH-LESS token is a claim ONLY if it is a real top-level entry (a real top file
      ``server.py`` / ``go.mod``) — a framework ``Next.js`` / bare domain ``cal.com`` is not, so it's
      prose.

    A genuinely-missing FILE (``foo/bar.ts`` whose basename exists nowhere) still flags — the real
    Law-1 detection stays intact; only DIR/word/symbol/prose false-positives are dropped."""
    if tok.startswith("/"):
        return False  # absolute-looking: never a relative repo-file claim
    if "/" in tok:
        return _is_source_file_ext(tok.rsplit("/", 1)[-1])
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

    # (1) non-empty + carries the symbol-map markers. An honest "no parseable source symbols" map
    # (a repo with no parseable code) is a clean pass — it makes no groundable claim to check.
    if not map_text or not map_text.strip():
        return VerifyResult(ready=False, reasons=["map is empty"])
    if "no parseable source symbols" in map_text:
        return VerifyResult(ready=True)
    # SHAPE: the stored artifact is EITHER the deterministic symbol map (degrade fallback: Part 1
    # alone, ``# Symbol map`` … ``Ranked signatures``) OR the comprehension-first resident doc (the
    # qualitative comprehension on top + the compact ``# Navigation map`` beneath). Accept whichever
    # shape carries all its markers; only if NEITHER does is the artifact a shell (fail-closed).
    missing_symbol = [m for m in REQUIRED_MAP_MARKERS if m not in map_text]
    missing_nav = [m for m in REQUIRED_NAV_MARKERS if m not in map_text]
    if missing_symbol and missing_nav:
        # Neither shape is complete → a shell. Name the gaps of the shape the artifact is CLOSER to
        # (the one missing fewer markers) so the reason is actionable; tie → the symbol-map shape.
        missing = missing_symbol if len(missing_symbol) <= len(missing_nav) else missing_nav
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
