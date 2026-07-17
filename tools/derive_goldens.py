#!/usr/bin/env python3
"""derive_goldens.py — mechanical ground-truth derivation for estate goldens.

Computes the reverse-import blast radius of a target module using ONLY Python
stdlib `ast` — deliberately independent of Proxy's tree-sitter/SCIP/LSP pipeline
(different toolchain = no shared-bug blindness; the maker!=checker rule applied
to eval data). Output is a golden JSON the eval gate grades Proxy against.

Usage:
  python3 derive_goldens.py <repo_root> <src_subdir> <target_module>
  e.g. python3 derive_goldens.py ./flask src flask.app

Golden semantics: "If <target_module> changes, which files in this repo import
it (directly = depth 1, transitively = closure)?" Grader: set-recall against
Proxy's get_dependents answer. Deterministic — no LLM anywhere in derivation.
"""
import ast
import json
import sys
import subprocess
import pathlib
from collections import defaultdict


def module_name(path: pathlib.Path, src_root: pathlib.Path) -> str:
    rel = path.relative_to(src_root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imports_of(path: pathlib.Path, pkg: str) -> set[str]:
    """All absolute in-repo module names imported by this file (stdlib ast only)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == pkg or a.name.startswith(pkg + "."):
                    found.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.module is not None:
                # relative import: anchor is this module's package; for __init__.py
                # the module IS the package, so keep one extra component
                this_pkg = module_name(path, SRC_ROOT).split(".")
                is_init = path.name == "__init__.py"
                keep = len(this_pkg) - node.level + (1 if is_init else 0)
                anchor = this_pkg[: max(keep, 0)]
                mod = ".".join(anchor + [node.module])
                found.add(mod)
            elif node.module and (node.module == pkg or node.module.startswith(pkg + ".")):
                found.add(node.module)
    return found


def main() -> None:
    global SRC_ROOT
    repo = pathlib.Path(sys.argv[1]).resolve()
    SRC_ROOT = repo / sys.argv[2]
    target = sys.argv[3]
    pkg = target.split(".")[0]

    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()

    files = sorted(SRC_ROOT.rglob("*.py"))
    imports: dict[str, set[str]] = {}
    mod_to_file: dict[str, str] = {}
    for f in files:
        m = module_name(f, SRC_ROOT)
        mod_to_file[m] = str(f.relative_to(repo))
        imports[m] = imports_of(f, pkg)

    # reverse edges: importer -> imported  =>  imported -> {importers}
    rdeps: dict[str, set[str]] = defaultdict(set)
    for m, deps in imports.items():
        for d in deps:
            # normalize "from flask.app import X" and "import flask.app" to module granularity
            rdeps[d].add(m)
            # a `from pkg import name` where name is a module also lands here via full path

    direct = sorted(rdeps.get(target, set()))
    # transitive closure
    seen, frontier = set(direct), list(direct)
    while frontier:
        cur = frontier.pop()
        for up in rdeps.get(cur, set()):
            if up not in seen:
                seen.add(up)
                frontier.append(up)
    transitive = sorted(seen)

    golden = {
        "_doc": f"Mechanical blast-radius golden: reverse importers of {target}.",
        "_derivation": "stdlib-ast reverse-import graph (derive_goldens.py) — independent of Proxy's tree-sitter/LSP pipeline",
        "estate": repo.name,
        "pinned_sha": sha,
        "target_module": target,
        "question": f"What in this repo directly imports {target} and would need review if its interface changed?",
        "direct_importers": [
            {"module": m, "file": mod_to_file.get(m, "?")} for m in direct
        ],
        "transitive_importers": [
            {"module": m, "file": mod_to_file.get(m, "?")} for m in transitive
        ],
        "grader": "deterministic:set-recall on direct_importers (threshold in scenarios.json); transitive graded leniently (superset allowed)",
        "known_limits": "static imports only — no dynamic __import__/importlib, no string-routed loading; those gaps are the messy-estate's job, not this golden's",
    }
    print(json.dumps(golden, indent=2))


if __name__ == "__main__":
    main()
