"""Doc 01 . gap D01-DIRECT-ANSWER-WHERE-MISROUTE -- a bare "where is X?" locate-question
must return the DEFINITION of X (its defining node's real file:line), never a caller.

Real-data failure reproduced on real Flask (pallets/flask @ 36e4a824): the reactive
wake turn "where is url_for?" was classified to ``get_dependents`` (the default
fallback, because ``_INTENT_PATTERNS`` only routed ``where...used/defined/referenced``,
not a bare ``where is X``) and returned a random CALLER --
``examples/tutorial/flaskr/blog.py`` ``def update(id)`` -- presented as the grounded
answer to a user asking where ``url_for`` LIVES. Even routing to ``find_references``
was wrong: its top grep hit was ``CHANGES.rst`` (a changelog line), not
``src/flask/helpers.py`` where ``url_for`` is defined.

FIX under test:
  (a) a bare "where is / where does X live / where is X defined" routes to a new
      definition-lookup that resolves X's defining node (graph_nodes / lookup_referent)
      and cites its real file:line -- the DEFINITION, not a caller;
  (b) when a defining node is not in the graph, the definition answer ranks a real
      SOURCE definition (``src/flask/...py``) above ``.rst``/``.md``/``CHANGES`` text.

Drives the PRODUCT path: ``run_full_pipeline`` -> the real ``CodeIntelMCPServer`` via
the canonical ``answer_direct`` resolver on a real on-disk Flask clone. No doubles.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("PROXY_ESTATE_CACHE", "/tmp/proxy_estates")  # noqa: S108

_FLASK_URL = "https://github.com/pallets/flask"
_FLASK_SHA = "36e4a824f340fdee7ed50937ba8e7f6bc7d17f81"  # real HEAD (prompt gave short 36e4a824)


@pytest.fixture(scope="module")
def flask_server():
    from services.code_intel.pipeline import run_full_pipeline

    try:
        pipeline = run_full_pipeline(tenant_id="t-where", repo_url=_FLASK_URL, sha=_FLASK_SHA)
    except Exception as exc:  # pragma: no cover - infra (no network / no git)
        pytest.skip(f"could not clone/build real Flask: {exc}")
    return pipeline.server


def _answer(server, ask: str):
    from services.code_intel.direct_answer import answer_direct

    return answer_direct(
        ask=ask, tenant="t-where", sha="", e2b=None, workroom=None, code_intel=server
    )


def test_bare_where_is_routes_to_definition_not_dependents(flask_server) -> None:
    """"where is url_for?" must NOT fall through to get_dependents (a caller)."""
    from services.code_intel.direct_answer import _classify

    tool = _classify("where is url_for?")
    assert tool != "get_dependents", (
        f"bare 'where is X' fell through to the dependents fallback (a caller), got {tool!r}"
    )
    assert tool == "find_definition", tool


def test_where_is_url_for_cites_the_definition(flask_server) -> None:
    """The grounded answer for "where is url_for?" cites url_for's DEFINITION --
    src/flask/helpers.py -- never a caller (examples/.../blog.py) and never a
    changelog (CHANGES.rst)."""
    ans = _answer(flask_server, "where is url_for?")

    assert ans.citation is not None, f"locate-question abstained: {ans.text}"
    cited_file = ans.citation.rsplit(":", 1)[0]

    # The definition of url_for lives in src/flask/helpers.py -- the answer must
    # cite THAT file, not a caller and not a changelog / doc file.
    assert cited_file.endswith("src/flask/helpers.py"), (
        f"'where is url_for?' cited {ans.citation} (expected src/flask/helpers.py "
        f"-- the DEFINITION). text={ans.text}"
    )
    assert not cited_file.endswith((".rst", ".md")), (
        f"locate-question cited a doc/changelog file {ans.citation}, not source"
    )
    # And the read must confirm the cited line actually contains the def.
    assert "url_for" in ans.text
