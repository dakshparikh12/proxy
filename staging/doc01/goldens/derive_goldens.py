#!/usr/bin/env python3
"""Mechanical golden derivation for Doc 01 code_intel fixtures.

Independent of Proxy's tree-sitter/LSP/SCIP pipeline — uses ONLY the Python
stdlib (``ast`` + regex + a hand-rolled power-iteration PageRank). Different
toolchain than the product = no shared-bug blindness (maker != checker). Reads
the *real* fixture git repos built by ``tests/fixtures/repos.py`` and emits the
answer-key JSONs the acceptance tests grade the product against.

Run from the repo root:  python3 <this>
Writes under staging/doc01/goldens/ (promoted to fixtures/goldens/ by a conductor).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve()
REPO_ROOT = Path("staging/doc01").resolve().parent.parent
STAGING = Path("staging/doc01")
GOLD = STAGING / "goldens"
REPOS_PY = STAGING / "tests" / "fixtures" / "repos.py"


def _load_repos_module():
    spec = importlib.util.spec_from_file_location("_doc01_repos", REPOS_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = mod  # dataclasses resolves annotations via sys.modules
    spec.loader.exec_module(mod)
    return mod


def _write(rel: str, obj: dict) -> None:
    path = GOLD / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")
    print(f"wrote {path}")


def _py_files(repo: Path):
    return sorted(p for p in repo.rglob("*.py") if ".git" not in p.parts)


# --------------------------------------------------------------------------- #
# Declarations (small-repo)                                                    #
# --------------------------------------------------------------------------- #


def derive_declarations(repo: Path) -> dict:
    decls = []
    for f in _py_files(repo):
        rel = f.relative_to(repo).as_posix()
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decls.append({"id": f"{rel}::{node.name}", "kind": "function",
                              "file": rel, "line": node.lineno})
            elif isinstance(node, ast.ClassDef):
                decls.append({"id": f"{rel}::{node.name}", "kind": "class",
                              "file": rel, "line": node.lineno})
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        decls.append({
                            "id": f"{rel}::{node.name}.{sub.name}",
                            "kind": "method", "file": rel, "line": sub.lineno,
                        })
    decls.sort(key=lambda d: d["id"])
    return {
        "_derivation": "stdlib ast top-level/method declaration walk (maker!=checker)",
        "declarations": decls,
    }


def derive_find_references(repo: Path, symbol: str) -> dict:
    pat = re.compile(rf"\b{re.escape(symbol)}\b")
    refs = []
    for f in _py_files(repo):
        rel = f.relative_to(repo).as_posix()
        for i, line in enumerate(f.read_text().splitlines(), start=1):
            if pat.search(line):
                refs.append({"file": rel, "line": i})
    refs.sort(key=lambda r: (r["file"], r["line"]))
    return {
        "_derivation": f"stdlib regex \\b{symbol}\\b occurrence scan (grep-independent of LSP)",
        "symbol": symbol,
        "references": refs,
    }


# --------------------------------------------------------------------------- #
# Graph goldens (known-reference-graph)                                        #
# --------------------------------------------------------------------------- #


def _reverse_reachable(edges, target):
    """All nodes with a directed path (caller->callee) reaching target."""
    preds = defaultdict(set)
    for s, t in edges:
        preds[t].add(s)
    seen, frontier = set(), [target]
    while frontier:
        cur = frontier.pop()
        for p in preds[cur]:
            if p not in seen:
                seen.add(p)
                frontier.append(p)
    return sorted(seen)


def _pagerank(nodes, edges, damping=0.85, iters=100):
    n = len(nodes)
    out = defaultdict(list)
    for s, t in edges:
        out[s].append(t)
    rank = {v: 1.0 / n for v in nodes}
    for _ in range(iters):
        dangling = sum(rank[v] for v in nodes if not out[v])
        new = {}
        for v in nodes:
            new[v] = (1.0 - damping) / n + damping * dangling / n
        for u in nodes:
            deg = len(out[u])
            if deg:
                share = damping * rank[u] / deg
                for w in out[u]:
                    new[w] += share
        rank = new
    return rank


def derive_known_graph() -> tuple:
    repos = _load_repos_module()
    g = repos.KNOWN_REFERENCE_GRAPH

    # dependents-F: bare-id reverse reachability (matches from_fixture ids).
    dependents_f = {
        "_derivation": "stdlib reverse-reachability over the declared call graph",
        "target": "F",
        "expected_ids": _reverse_reachable(g["edges"], "F"),
    }

    # pagerank: over canonical file::name ids (matches the real run_full_pipeline parse).
    def cid(x):
        return f"graph/{x.lower()}.py::{x.lower()}"

    nodes = [cid(x) for x in g["nodes"]]
    edges = [(cid(s), cid(t)) for (s, t) in g["edges"]]
    ranks = _pagerank(nodes, edges)
    ordered = sorted(nodes, key=lambda v: (-ranks[v], v))
    pagerank = {
        "_derivation": "stdlib power-iteration PageRank (d=0.85, 100 iters) over canonical ids",
        "top5": [{"id": v, "score": round(ranks[v], 6)} for v in ordered[:5]],
        "all": [{"id": v, "score": round(ranks[v], 6)} for v in ordered],
    }
    return dependents_f, pagerank


# --------------------------------------------------------------------------- #
# ORM goldens                                                                  #
# --------------------------------------------------------------------------- #


def derive_who_writes(repo: Path, table: str) -> dict:
    """A function is a writer if its body contains an ORM write on the model."""
    write_markers = (".objects.create", ".save(", ".objects.update", ".bulk_create")
    writers = []
    for f in _py_files(repo):
        rel = f.relative_to(repo).as_posix()
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = ast.get_source_segment(f.read_text(), node) or ""
                if any(m in seg for m in write_markers):
                    writers.append({"id": f"{rel}::{node.name}", "file": rel,
                                    "line": node.lineno, "confidence": "resolved"})
    writers.sort(key=lambda w: w["id"])
    return {
        "_derivation": "stdlib ast scan for ORM write markers on a tier-1 stack",
        "table": table,
        "writers": writers,
    }


def derive_shares_table(repo: Path, table: str) -> dict:
    """Top-level modules whose files reference the shared model."""
    marker = "Payment"
    modules = set()
    for f in _py_files(repo):
        rel = f.relative_to(repo).as_posix()
        if rel.endswith("models.py"):
            continue
        if marker in f.read_text():
            modules.add(rel.split("/")[0])
    return {
        "_derivation": "stdlib scan: top-level modules referencing the shared table model",
        "table": table,
        "expected_module_ids": sorted(modules),
    }


# --------------------------------------------------------------------------- #
# Ownership golden                                                             #
# --------------------------------------------------------------------------- #


def derive_owner(repo: Path, owned_path: str) -> dict:
    owner = None
    for line in (repo / "CODEOWNERS").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pattern, *owners = line.split()
        pat = pattern.rstrip("/")
        if owned_path.startswith(pat) or owned_path.startswith(pattern):
            owner = owners[0]
    return {
        "_derivation": "stdlib CODEOWNERS longest-prefix match",
        "path": owned_path,
        "owner": owner,
        "confidence": "resolved",
    }


# --------------------------------------------------------------------------- #
# Abstention golden (estate-messy) — documented expected labels                #
# --------------------------------------------------------------------------- #


def derive_abstention() -> dict:
    return {
        "_derivation": "hand-specified dynamic-dispatch abstention contract (no real repo here)",
        "_note": "symbols reachable only via dynamic routing must abstain honestly",
        "cases": [
            {"symbol": "dispatch_target", "expected_label": "not-found-by-this-method"},
            {"symbol": "reflectively_called", "expected_label": "lower-bound"},
        ],
        "forbidden": {"fabricated_node": True, "fabricated_citation": True},
    }


def main() -> None:
    repos = _load_repos_module()

    small = repos.small_repo_fixture()
    _write("fixture-small-repo/declarations.json",
           derive_declarations(small.clone_path))
    _write("fixture-small-repo/find-references.json",
           derive_find_references(small.clone_path, small.known_symbol))

    dep_f, pagerank = derive_known_graph()
    _write("fixture-known-reference-graph/dependents-F.json", dep_f)
    _write("fixture-known-reference-graph/pagerank.json", pagerank)

    django = repos.django_model_fixture()
    _write("fixture-django-model/who-writes-orders.json",
           derive_who_writes(django.clone_path, "orders"))

    cross = repos.cross_module_table_access_fixture()
    _write("fixture-cross-module-table-access/shares-table-payments.json",
           derive_shares_table(cross.clone_path, "payments"))

    owners = repos.codeowners_fixture()
    _write("fixture-codeowners/owner.json",
           derive_owner(owners.clone_path, owners.owned_path))

    _write("estate-messy/abstention-cases.json", derive_abstention())
    print("goldens derived OK")


if __name__ == "__main__":
    main()
