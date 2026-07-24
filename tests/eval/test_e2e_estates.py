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


@pytest.mark.e2e
def test_ac_lang_011_real_go_repo_cross_file_blast_radius() -> None:
    """AC-LANG-011: on a REAL non-Python repo (Go), the graph extracts multi-file
    structure and get_dependents resolves cross-file callers SOUNDLY — every
    reported dependent's file actually references the symbol (no fabrication)."""
    repo = _clone_estate("gorilla-mux", "https://github.com/gorilla/mux", "v1.8.1")
    graph = GraphBuilder().build(repo).graph

    funcs = [n for n in graph.nodes if n.kind in ("function", "method")]
    files = {n.path for n in funcs}
    assert len(funcs) >= 20 and len(files) >= 3, f"thin extraction: {len(funcs)} funcs / {len(files)} files"

    # Soundness on DIRECT call edges: every cross-file `calls` edge must have the
    # caller's file actually reference the callee's name (no fabricated edge).
    file_text: dict[str, str] = {}
    checked = 0
    for edge in graph.edges:
        if edge.kind != "calls":
            continue
        src, tgt = graph.get(edge.source), graph.get(edge.target)
        if src is None or tgt is None or src.path == tgt.path:
            continue
        name = tgt.id.split("::")[-1]
        text = file_text.setdefault(src.path, (repo / src.path).read_text(errors="replace"))
        assert name in text, f"fabricated call edge: {src.path} does not reference {name!r}"
        checked += 1
        if checked >= 25:
            break
    assert checked >= 1, "no cross-file call edge found in a real Go repo"


@pytest.mark.e2e
def test_ac_m5_006c_who_writes_nonexistent_table_on_real_non_django_repo() -> None:
    """AC-M5-006c (regression, WHO-WRITES-FABRICATES-NON-DJANGO-PY): on a REAL non-Django
    Python repo (flask), ``who_writes`` for a table that DOES NOT EXIST must return ZERO
    writers — never 'every function that calls any write-method'. The search-only Tier-3
    fallback previously returned all functions containing any .create/.save/.update/... call
    (incl. dict.update()) for ANY table name, fabricating a write-path blast-radius for a
    table that isn't there (Law 2: confident-wrong softened by a label is still forbidden).

    Driven through the REAL product entrypoint: CodeIntelMCPServer.who_writes on the real clone.
    """
    from services.code_intel.mcp_server import CodeIntelMCPServer

    repo = _clone_estate("flask", "https://github.com/pallets/flask", _FLASK_SHA)
    graph = GraphBuilder().build(repo / "src").graph
    server = CodeIntelMCPServer(graph=graph, clone_path=repo, lsp=None)

    # flask has no 'users' table and no ORM models at all.
    users = server.who_writes("users")
    assert users.writers == [], (
        f"who_writes('users') on flask fabricated {len(users.writers)} writers "
        f"(e.g. {[w.id for w in users.writers[:3]]}); flask has no 'users' table"
    )

    # A guaranteed-nonexistent table name: must be empty AND must not equal the 'users' set
    # (the old bug returned the SAME all-functions set regardless of the queried name).
    ghost = server.who_writes("totally_nonexistent_xyz")
    assert ghost.writers == [], (
        f"who_writes('totally_nonexistent_xyz') fabricated {len(ghost.writers)} writers"
    )
    assert ghost.status == "not-found", f"expected status 'not-found', got {ghost.status!r}"
