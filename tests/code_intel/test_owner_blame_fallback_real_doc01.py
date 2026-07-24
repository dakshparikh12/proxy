"""Doc 01 . gap D01-OWNER-BLAME-UNKNOWN -- on a real repo with no CODEOWNERS the
``owner`` tool must return the top recent git authors of the path (tagged
'lower-bound'), never ``owner='(unknown)'``; and the direct-answer route for a
file-path ask ("who owns src/flask/app.py?") must pass the FULL path to owner().

Real-data failure reproduced on real Flask (pallets/flask @ 36e4a824, which ships
NO CODEOWNERS file):

  1. ``owner('src/flask/app.py')`` returned ``owner='(unknown)'`` because the
     git-blame fallback pointed ``--git-dir`` at ``clone_path/.git`` -- but the
     production Cloner materialises the work-tree at ``.../repos/<repo>/checkout``
     with its git metadata ONE LEVEL UP (``.../repos/<repo>/.git``), so that gitdir
     never exists and ``git log`` returned an empty author. The "who owns this?"
     meeting question therefore never yielded a real answer on a real repo.

  2. The direct-answer route for "who owns src/flask/app.py?" extracted the symbol
     as ``app.py`` (the ask was split on '/' by ``_extract_symbol``), so ``owner()``
     was called with ``path='app.py'`` -- a path that does not exist -- and returned
     not-found even after (1) was fixed.

FIX under test:
  (a) ``orm.owner`` implements the git-blame fallback by DISCOVERING the repo from
      inside the clone (``git -C <clone> shortlog``) so it works on the split-clone
      layout, and returns the top recent author(s) of the path tagged 'lower-bound';
  (b) ``direct_answer._extract_symbol`` recognises a file-path ask (a token that
      contains '/' or a known source extension) and returns the FULL path, so the
      owner tool is called with ``src/flask/app.py`` not ``app.py``.

Drives the PRODUCT path: ``run_full_pipeline`` -> the real ``CodeIntelMCPServer``
-> ``server.owner`` and the canonical ``answer_direct`` resolver on a real on-disk
Flask clone. No doubles.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("PROXY_ESTATE_CACHE", "/tmp/proxy_estates")  # noqa: S108

_FLASK_URL = "https://github.com/pallets/flask"
_FLASK_SHA = "36e4a824f340fdee7ed50937ba8e7f6bc7d17f81"  # real HEAD


@pytest.fixture(scope="module")
def flask_server():
    from services.code_intel.pipeline import run_full_pipeline

    try:
        pipeline = run_full_pipeline(tenant_id="t-owner", repo_url=_FLASK_URL, sha=_FLASK_SHA)
    except Exception as exc:  # pragma: no cover - infra (no network / no git)
        pytest.skip(f"could not clone/build real Flask: {exc}")
    return pipeline.server


def test_owner_blame_fallback_returns_real_author_on_real_repo(flask_server) -> None:
    """``owner('src/flask/app.py')`` on real Flask (no CODEOWNERS) returns a REAL
    recent author tagged 'lower-bound', never '(unknown)'."""
    r = flask_server.owner("src/flask/app.py")

    assert r is not None, "owner() abstained entirely on a real tracked path"
    assert r.owner not in ("(unknown)", "", None), (
        f"owner() returned no real author: owner={r.owner!r} -- the git-blame "
        f"fallback is not reading real authors on the split-clone layout"
    )
    # CODEOWNERS is absent on this SHA, so the blame fallback is a HINT (Law 2).
    assert r.confidence == "lower-bound", (
        f"blame-derived ownership must be tagged lower-bound, got {r.confidence!r}"
    )
    # src/flask/app.py's top recent authors are the real Flask maintainers.
    assert any(
        name in r.owner for name in ("David Lord", "pgjones", "davidism")
    ), f"owner={r.owner!r} does not name a real recent author of src/flask/app.py"


def test_extract_symbol_keeps_full_path_for_file_owner_ask() -> None:
    """A file-path ask keeps the FULL path -- ``owner()`` must be called with
    ``src/flask/app.py``, never the trailing ``app.py`` token."""
    from services.code_intel.direct_answer import _extract_symbol

    sym = _extract_symbol("who owns src/flask/app.py?")
    assert sym == "src/flask/app.py", (
        f"file-path ask extracted {sym!r} (split on '/'); owner() would be called "
        f"with the wrong path"
    )


def test_who_owns_file_yields_real_owner_through_direct_answer(flask_server) -> None:
    """End-to-end on the real product path: "who owns src/flask/app.py?" resolves
    through ``answer_direct`` -> ``owner`` to a grounded real author, not not-found."""
    from services.code_intel.direct_answer import answer_direct

    ans = answer_direct(
        ask="who owns src/flask/app.py?",
        tenant="t-owner",
        sha="",
        e2b=None,
        workroom=None,
        code_intel=flask_server,
    )

    assert ans.tool == "owner", f"'who owns X' misrouted to {ans.tool!r}"
    assert ans.confidence != "not-found", (
        f"'who owns src/flask/app.py?' abstained: {ans.text!r}"
    )
    assert any(
        name in ans.text for name in ("David Lord", "pgjones", "davidism")
    ), f"direct answer named no real owner: {ans.text!r}"
