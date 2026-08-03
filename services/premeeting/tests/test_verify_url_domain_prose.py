"""BUG 2 — verify_map must not reject a correct real map that names URLs / domains / a framework.

``extract_named_paths`` / ``_looks_like_path`` classified path-SHAPED prose tokens — the product
domain (``cal.com``), a framework (``Next.js``), a scheme-relative host (``//cal.com/docs``), and a
cited repo URL (``github.com/calcom/cal.com``) — as repo paths that must EXIST in the clone. None
exist, so ``hallucinated`` was non-empty and a faithful real subscription map was falsely marked
``not_ready`` → the ``repos`` bind (gated on ready) was skipped → ``POST /meetings`` 404'd.

The fix drops URL-ish tokens and bare-domain / framework names from the hallucination check while
keeping genuine-path detection: a real cited path (``packages/lib/x.ts``) passes and a genuinely
FABRICATED nested path (``foo/bar.ts``) still flags.
"""
from __future__ import annotations

from typing import Any

from premeeting.cloner import Cloner
from premeeting.exclusions import ExclusionManager
from premeeting.verify import _is_path_claim, _looks_like_path, verify_map

# A realistic map that mentions the product, a framework, a URL, and a real cited path — the
# shape of any real cal.com subscription map. The clone has a matching real ``packages/lib/x.ts``.
_REALISTIC_MAP = """# Repo Map — cal.com @ abc

## What this is
cal.com is the scheduling app, built on Next.js. Homepage: //cal.com/docs and the source is at
github.com/calcom/cal.com — see https://cal.com for the product.

## Where things live
- packages/ — the shared libraries
- src/ — the app code

## Entry points
- packages/lib/x.ts — a shared helper

## Key models / domain
- src/models.py — the domain types

## Conventions
pytest + ruff; the framework is Next.js.

## Notes
Single monorepo. See github.com/calcom/cal.com for issues.
"""


def _clone_fixture(make_git_repo: Any) -> tuple[Any, ExclusionManager]:
    src, _sha = make_git_repo(
        {
            "packages/lib/x.ts": "export const x = 1;\n",
            "src/models.py": "class Thing: ...\n",
            "README.md": "# cal.com\n",
        }
    )
    em = ExclusionManager()
    checkout = Cloner(exclusion_manager=em).clone("tenant-a", src.as_uri())
    return checkout, em


def test_realistic_map_with_prose_urls_and_domains_is_ready(make_git_repo: Any) -> None:
    """A faithful map that names the product/framework/a URL + one real path verifies READY."""
    checkout, em = _clone_fixture(make_git_repo)
    res = verify_map(_REALISTIC_MAP, checkout, exclusions=em)
    assert res.ready, res.reasons
    assert res.status == "ready"


def test_genuinely_missing_nested_path_still_flags(make_git_repo: Any) -> None:
    """A fabricated nested path (``foo/bar.ts``) is still caught — genuine detection intact."""
    checkout, em = _clone_fixture(make_git_repo)
    bad = _REALISTIC_MAP.replace(
        "- packages/lib/x.ts — a shared helper",
        "- packages/lib/x.ts — a shared helper\n- foo/bar.ts — a fabricated file",
    )
    res = verify_map(bad, checkout, exclusions=em)
    assert not res.ready
    assert any("not in the clone" in r and "foo/bar.ts" in r for r in res.reasons)
    # The prose tokens must NOT appear in the flagged set (they are not path claims).
    joined = " ".join(res.reasons)
    assert "cal.com" not in joined
    assert "Next.js" not in joined
    assert "github.com" not in joined


def test_looks_like_path_drops_url_ish_tokens() -> None:
    """URL-ish tokens (``://`` or leading ``//``) are never treated as path-shaped."""
    assert not _looks_like_path("//cal.com/docs")
    assert not _looks_like_path("https://cal.com/docs")
    # A genuine path is still path-shaped.
    assert _looks_like_path("packages/lib/x.ts")
    assert _looks_like_path("server.py")


def test_leading_dot_attribute_ref_is_not_a_path_claim() -> None:
    """A decorator/attribute ref written path-like (``/.group``, ``cli/.group``) is prose, not a
    fabricated file. The live-meeting sim caught verify falsely rejecting a genuinely-good 13KB click
    map on ``/.group`` (leading slash + ``.group`` with an empty stem). Two general rules fix it:
    a leading-``/`` token is never a relative repo-path claim, and the source-extension branch
    requires a non-empty stem — while real hallucination detection stays intact."""
    top = {"src", "tests", "README.md"}
    # leading-slash absolute-looking tokens → never a relative repo-path claim
    assert not _is_path_claim("/.group", top)
    assert not _is_path_claim("/usr/local/bin", top)
    # leading-dot attribute refs under a (fabricated) dir → empty stem → prose, not a file
    assert not _is_path_claim("cli/.group", top)
    assert not _is_path_claim("commands/.option", top)
    # detection intact: a real file under a real top dir, and a genuinely-missing nested file
    assert _is_path_claim("src/click/core.py", top)
    assert _is_path_claim("foo/bar.ts", top)


def test_real_file_cited_by_imprecise_dir_is_grounded_not_hallucinated(make_git_repo: Any) -> None:
    """Repo-diversity regression (gin/Go): a map that names a REAL file at a slightly-wrong directory
    — dropping a parent dir, e.g. a Go internal package cited as ``bytesconv/bytesconv.go`` when the
    file is ``internal/bytesconv/...``, or a monorepo abbreviation — is grounded, NOT a hallucination:
    the file exists in the repo and the agent reads the actual file live. Only a name that resolves
    NOWHERE in the repo is a fabrication."""
    checkout, em = _clone_fixture(make_git_repo)  # has packages/lib/x.ts, src/models.py, README.md
    # cite the REAL packages/lib/x.ts by an imprecise path (dropped the packages/ prefix) → grounded
    ok = _REALISTIC_MAP.replace(
        "- packages/lib/x.ts — a shared helper",
        "- lib/x.ts — a shared helper (cited without the packages/ prefix)",
    )
    res = verify_map(ok, checkout, exclusions=em)
    assert res.ready, res.reasons
    # a truly fabricated file (basename exists NOWHERE) still flags — detection intact
    bad = ok + "\n- foo/nope.ts — a fabricated file\n"
    res2 = verify_map(bad, checkout, exclusions=em)
    assert not res2.ready
    assert any("nope.ts" in r for r in res2.reasons)


def test_map_with_group_decorator_prose_verifies_ready(make_git_repo: Any) -> None:
    """Regression (live-meeting sim): a faithful map whose prose contains a ``/.group``-shaped token
    must still verify READY — not be intermittently rejected because the LLM prose happened to emit
    an attribute ref that looks path-like."""
    checkout, em = _clone_fixture(make_git_repo)
    m = _REALISTIC_MAP + (
        "\n## Extra\nCommands register under the group root /.group; decorators like @cli.group and "
        "the .option ref build the tree.\n"
    )
    res = verify_map(m, checkout, exclusions=em)
    assert res.ready, res.reasons
