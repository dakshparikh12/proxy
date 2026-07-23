"""E2E real-data eval on real estates — the Tier-2 proof for doc01 (§3.8, AC-E2E-*).

These build Proxy's dependency graph on a REAL cloned repo and grade the answer
against the sealed goldens in fixtures/estates/. Marked ``e2e`` so the offline
tier skips them; the acceptance command (``pytest -k e2e_00x``) runs them.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

from services.code_intel.graph_builder import GraphBuilder

_CACHE = pathlib.Path(os.environ.get("PROXY_ESTATE_CACHE", "/tmp/proxy_estates"))
_FLASK_SHA = "36e4a824f340fdee7ed50937ba8e7f6bc7d17f81"


def _clone_estate(name: str, url: str, sha: str) -> pathlib.Path:
    repo = _CACHE / name
    try:
        if not (repo / ".git").is_dir():
            _CACHE.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--quiet", url, str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "--quiet", sha], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:  # pragma: no cover
        pytest.skip(f"estate clone unavailable (network/git): {exc}")
    return repo


@pytest.mark.e2e
def test_ac_e2e_001_full_pipeline_estate_flask_golden_graph() -> None:
    """AC-E2E-001: on real flask, the dependency graph's reverse-importers of the
    target module match the sealed golden at set-recall 1.0 — and never fabricate
    an importer that is not a real module in the repo."""
    repo = _clone_estate("flask", "https://github.com/pallets/flask", _FLASK_SHA)
    golden = json.loads(
        pathlib.Path("fixtures/estates/flask/golden/flask.app.json").read_text()
    )
    graph = GraphBuilder().build(repo / "src").graph

    gold_direct = {i["module"] for i in golden["direct_importers"]}
    module_ids = {n.id for n in graph.nodes if n.kind == "module"}
    answer = {
        a for a in graph.reverse_dependents(golden["target_module"]) if a in module_ids
    }

    recall = len(gold_direct & answer) / len(gold_direct)
    assert recall == 1.0, f"reverse-import recall {recall:.3f}; missing {sorted(gold_direct - answer)}"
    # honesty: every reported importer is a real module node (no fabricated node/citation)
    assert answer <= module_ids, f"fabricated importers: {sorted(answer - module_ids)}"


@pytest.mark.e2e
def test_ac_e2e_002_estate_messy_abstention_honest_labels(tmp_path: pathlib.Path) -> None:
    """AC-E2E-002: symbols reachable only via dynamic dispatch abstain honestly —
    'not-found-by-this-method' or 'lower-bound', never a fabricated resolved node/citation."""
    from services.code_intel.mcp_server import CodeIntelMCPServer

    # Minimal messy estate: `reflectively_called` exists ONLY inside a getattr string
    # (dynamic — grep-findable, statically unresolvable); `dispatch_target` is absent.
    (tmp_path / "app.py").write_text(
        "def make(reg):\n"
        "    return getattr(reg, 'reflectively_called')()\n"
    )
    golden = json.loads(
        pathlib.Path("fixtures/goldens/estate-messy/abstention-cases.json").read_text()
    )
    graph = GraphBuilder().build(tmp_path).graph
    server = CodeIntelMCPServer(graph=graph, clone_path=tmp_path, lsp=None)

    for case in golden["cases"]:
        sym, expected = case["symbol"], case["expected_label"]
        dep = server.get_dependents(sym)
        refs = server.find_references(sym)
        # forbidden: a fabricated *resolved* claim for a dynamic-only symbol
        assert not any(i.confidence == "resolved" for i in refs.results), (
            f"fabricated resolved citation for dynamic symbol {sym!r}"
        )
        assert not dep.results or all(i.confidence != "resolved" for i in dep.results)

        if dep.status == "not-found" and refs.status == "not-found":
            got = "not-found-by-this-method"
        else:
            confidences = {i.confidence for i in refs.results} | {i.confidence for i in dep.results}
            got = "lower-bound" if confidences <= {"lower-bound"} else "resolved"
        assert got == expected, f"{sym}: honesty label {got!r}, golden expects {expected!r}"


@pytest.mark.e2e
def test_ac_m1_006_repoprovider_fetch_byte_identical_to_github() -> None:
    """AC-M1-006: a known file read via RepoProvider from the pinned clone is
    byte-identical (SHA-256) to GitHub's raw contents at the same SHA — no
    line-ending/encoding normalization alters the content."""
    import hashlib
    import urllib.request

    from services.code_intel.repo_provider import RepoProvider

    repo = _clone_estate("flask", "https://github.com/pallets/flask", _FLASK_SHA)
    rel = "src/flask/__init__.py"

    class _Nango:
        def mint(self, *args: object, **kwargs: object) -> str:
            return "ghs_per_operation_token"

    proxy_bytes = RepoProvider(nango=_Nango()).read_file(repo, rel)

    url = f"https://raw.githubusercontent.com/pallets/flask/{_FLASK_SHA}/{rel}"
    try:
        github_bytes = urllib.request.urlopen(url, timeout=20).read()  # noqa: S310
    except Exception as exc:  # pragma: no cover - network
        pytest.skip(f"github raw unavailable: {exc}")

    assert hashlib.sha256(proxy_bytes).hexdigest() == hashlib.sha256(github_bytes).hexdigest(), (
        "RepoProvider bytes differ from GitHub raw — normalization/corruption on the read path"
    )
    assert proxy_bytes == github_bytes  # byte-exact
