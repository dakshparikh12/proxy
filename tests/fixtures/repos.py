"""Doc 01 · repository fixtures for the code_intel evidence layer.

Every ``*_fixture()`` here is HERMETIC and DETERMINISTIC:

* Repos that flow through ``run_full_pipeline`` / ``Cloner.clone`` are **real
  local git repos** built in a temp dir via subprocess ``git`` (init/add/commit)
  with pinned author + commit dates, so ``git rev-parse HEAD`` is stable across a
  session and no network is ever touched.
* Synthetic *graph* fixtures (known-reference-graph, reads-edge-chain, …) also
  build a real repo AND expose an explicit ``.graph_spec`` (nodes + edges with
  the ids the acceptance tests assert on) so the product's ``from_fixture`` seam
  can load a known graph directly. Node ids for these synthetic graphs are the
  bare symbol name the tests hard-code (``"A"``…``"F"``); table nodes use the
  canonical ``table::<name>`` form (spec §12.6).

None of this module imports product code — the fixtures are pure test input, so
the module imports clean before ``services.code_intel`` exists and every test
that couples a fixture to the (absent) product FAILS red.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Deterministic real-git-repo builder                                         #
# --------------------------------------------------------------------------- #

_CACHE_DIR = Path(tempfile.gettempdir()) / "proxy_doc01_fixtures"
_FIXED_ENV = {
    "GIT_AUTHOR_NAME": "Proxy Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@proxy.test",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00 +0000",
    "GIT_COMMITTER_NAME": "Proxy Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@proxy.test",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00 +0000",
}


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, **_FIXED_ENV}
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def build_git_repo(name: str, files: dict) -> tuple:
    """Build (once) a real local git repo named ``name`` from ``files``.

    Returns ``(repo_path, head_sha)``. Idempotent: a rebuild is skipped when the
    repo already exists, so the HEAD sha is stable for the whole session.
    """
    repo = _CACHE_DIR / name
    if (repo / ".git").is_dir():
        sha = _run_git(repo, "rev-parse", "HEAD").stdout.strip()
        return repo, sha

    repo.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", _FIXED_ENV["GIT_AUTHOR_EMAIL"])
    _run_git(repo, "config", "user.name", _FIXED_ENV["GIT_AUTHOR_NAME"])
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "fixture: initial commit")
    sha = _run_git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, sha


# --------------------------------------------------------------------------- #
# Fixture value objects                                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RepoFixture:
    """A real-git-repo fixture: url is a local path git can clone."""

    url: str
    clone_path: Path
    expected_sha: str
    expected_file_list: list
    all_files: list
    graph_spec: dict = None
    known_symbol: str = "helper"
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GraphFixture:
    """A synthetic in-memory graph fixture (also backed by a real repo)."""

    url: str
    clone_path: Path
    expected_sha: str
    graph_spec: dict


# --------------------------------------------------------------------------- #
# Canonical synthetic graphs (single source of truth for graph goldens)       #
# --------------------------------------------------------------------------- #

# known-reference-graph: caller -> callee edges (kind="calls").
KNOWN_REFERENCE_GRAPH: dict = {
    "nodes": ["A", "B", "C", "D", "E", "F"],
    "edges": [
        ("A", "B"),
        ("A", "C"),
        ("B", "D"),
        ("C", "D"),
        ("C", "E"),
        ("D", "F"),
        ("E", "F"),
    ],
    "edge_kind": "calls",
}

# reads-edge-chain: reader -> read edges (kind="reads"); traversed depth-1 only.
READS_EDGE_CHAIN: dict = {
    "nodes": ["A", "B", "C", "D"],
    "edges": [("A", "B"), ("B", "C"), ("C", "D")],
    "edge_kind": "reads",
}


def _graph_spec_from(defn: dict) -> dict:
    kind = defn["edge_kind"]
    nodes = [
        {"id": n, "kind": "function", "path": f"graph/{n.lower()}.py", "line": 1}
        for n in defn["nodes"]
    ]
    edges = [{"source": s, "target": t, "kind": kind} for (s, t) in defn["edges"]]
    return {"nodes": nodes, "edges": edges}


def _graph_repo_files(defn: dict) -> dict:
    """Render the synthetic graph as real Python so a real parse reproduces it."""
    files = {"graph/__init__.py": ""}
    callees = {n: [] for n in defn["nodes"]}
    for s, t in defn["edges"]:
        callees[s].append(t)
    for n in defn["nodes"]:
        body_calls = "".join(f"    {t.lower()}()\n" for t in callees[n]) or "    pass\n"
        imports = "".join(
            f"from graph.{t.lower()} import {t.lower()}\n" for t in callees[n]
        )
        files[f"graph/{n.lower()}.py"] = (
            f"{imports}\n\ndef {n.lower()}():\n{body_calls}"
        )
    return files


# --------------------------------------------------------------------------- #
# small-repo — the workhorse real repo                                         #
# --------------------------------------------------------------------------- #

_SMALL_REPO_FILES: dict = {
    "pkg/__init__.py": "",
    "pkg/util.py": (
        "def helper(x):\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "def unused_util():\n"
        "    return None\n"
    ),
    "pkg/a.py": (
        "from pkg.util import helper\n"
        "\n"
        "\n"
        "def some_fn(x):\n"
        "    return helper(x)\n"
    ),
    "pkg/b.py": (
        "from pkg.util import helper\n"
        "\n"
        "\n"
        "def b_fn(x):\n"
        "    return helper(x) + helper(x)\n"
    ),
    "pkg/c.py": (
        "from pkg.a import some_fn\n"
        "\n"
        "\n"
        "def c_fn():\n"
        "    return some_fn(1)\n"
    ),
    "pkg/d.py": "def d_fn():\n    return 4\n",
    "pkg/e.py": "def e_fn():\n    return 5\n",
    "pkg/f.py": "def f_fn():\n    return 6\n",
    "pkg/g.py": "def g_fn():\n    return 7\n",
    "pkg/h.py": "def h_fn():\n    return 8\n",
    "README.md": "# small repo fixture\n\nDeterministic fixture for code_intel.\n",
    "pyproject.toml": '[project]\nname = "small-repo-fixture"\nversion = "0.0.0"\n',
}


def small_repo_fixture() -> RepoFixture:
    repo, sha = build_git_repo("small-repo", _SMALL_REPO_FILES)
    tracked = sorted(_SMALL_REPO_FILES.keys())
    return RepoFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        expected_file_list=tracked,
        all_files=tracked,
        known_symbol="helper",
    )


# --------------------------------------------------------------------------- #
# Synthetic graph fixtures                                                     #
# --------------------------------------------------------------------------- #


def known_reference_graph_fixture() -> GraphFixture:
    repo, sha = build_git_repo(
        "known-reference-graph", _graph_repo_files(KNOWN_REFERENCE_GRAPH)
    )
    return GraphFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        graph_spec=_graph_spec_from(KNOWN_REFERENCE_GRAPH),
    )


def reads_edge_chain_fixture() -> GraphFixture:
    repo, sha = build_git_repo(
        "reads-edge-chain", _graph_repo_files(READS_EDGE_CHAIN)
    )
    return GraphFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        graph_spec=_graph_spec_from(READS_EDGE_CHAIN),
    )


@dataclass(frozen=True)
class LargeFanFixture:
    url: str
    clone_path: Path
    expected_sha: str
    graph_spec: dict
    total_dependents: int


def large_dependency_fan_fixture(total_dependents: int = 60) -> LargeFanFixture:
    # node "T" plus N distinct dependents dep_000.. each with a strictly
    # descending pagerank so ranking + capping is deterministic.
    nodes = [{"id": "T", "kind": "function", "path": "fan/t.py", "line": 1}]
    edges = []
    files = {"fan/__init__.py": "", "fan/t.py": "def t():\n    return 0\n"}
    for i in range(total_dependents):
        nid = f"dep_{i:03d}"
        nodes.append(
            {
                "id": nid,
                "kind": "function",
                "path": f"fan/{nid}.py",
                "line": 1,
                "pagerank": float(total_dependents - i),
            }
        )
        edges.append({"source": nid, "target": "T", "kind": "calls"})
        files[f"fan/{nid}.py"] = (
            f"from fan.t import t\n\n\ndef {nid}():\n    return t()\n"
        )
    repo, sha = build_git_repo(f"large-fan-{total_dependents}", files)
    return LargeFanFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        graph_spec={"nodes": nodes, "edges": edges},
        total_dependents=total_dependents,
    )


# --------------------------------------------------------------------------- #
# ORM / table fixtures                                                         #
# --------------------------------------------------------------------------- #

_DJANGO_FILES: dict = {
    "orders/__init__.py": "",
    "orders/models.py": (
        "from django.db import models\n"
        "\n"
        "\n"
        "class Order(models.Model):\n"
        "    total = models.IntegerField()\n"
        "\n"
        "    class Meta:\n"
        "        db_table = 'orders'\n"
        "\n"
        "\n"
        "class OrderItem(models.Model):\n"
        "    order = models.ForeignKey(Order, on_delete=models.CASCADE)\n"
    ),
    "orders/service.py": (
        "from orders.models import Order\n"
        "\n"
        "\n"
        "def create_order(total):\n"
        "    return Order.objects.create(total=total)\n"
        "\n"
        "\n"
        "def cancel_order(order):\n"
        "    order.total = 0\n"
        "    order.save()\n"
    ),
    "orders/readonly.py": (
        "from orders.models import Order\n"
        "\n"
        "\n"
        "def list_orders():\n"
        "    return list(Order.objects.all())\n"
    ),
}


def django_model_fixture() -> RepoFixture:
    repo, sha = build_git_repo("django-model", _DJANGO_FILES)
    tracked = sorted(_DJANGO_FILES.keys())
    return RepoFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        expected_file_list=tracked,
        all_files=tracked,
        extra={"orm": "django", "tier": "tier-1"},
    )


_NON_TIER1_FILES: dict = {
    "app/__init__.py": "",
    "app/db.py": (
        "import mystery_orm\n"
        "\n"
        "\n"
        "def write_orders(total):\n"
        "    return mystery_orm.table('orders').insert(total=total)\n"
    ),
}


def non_tier1_orm_fixture() -> RepoFixture:
    repo, sha = build_git_repo("non-tier1-orm", _NON_TIER1_FILES)
    tracked = sorted(_NON_TIER1_FILES.keys())
    return RepoFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        expected_file_list=tracked,
        all_files=tracked,
        extra={"orm": "mystery_orm", "tier": "non-tier-1"},
    )


_CROSS_MODULE_FILES: dict = {
    "billing/__init__.py": "",
    "billing/charge.py": (
        "from db.models import Payment\n"
        "\n"
        "\n"
        "def charge(amount):\n"
        "    return Payment.objects.create(amount=amount)\n"
    ),
    "reporting/__init__.py": "",
    "reporting/audit.py": (
        "from db.models import Payment\n"
        "\n"
        "\n"
        "def audit():\n"
        "    return list(Payment.objects.all())\n"
    ),
    "db/__init__.py": "",
    "db/models.py": (
        "from django.db import models\n"
        "\n"
        "\n"
        "class Payment(models.Model):\n"
        "    amount = models.IntegerField()\n"
        "\n"
        "    class Meta:\n"
        "        db_table = 'payments'\n"
    ),
}


def cross_module_table_access_fixture() -> RepoFixture:
    repo, sha = build_git_repo("cross-module-table", _CROSS_MODULE_FILES)
    tracked = sorted(_CROSS_MODULE_FILES.keys())
    return RepoFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        expected_file_list=tracked,
        all_files=tracked,
    )


# --------------------------------------------------------------------------- #
# Ownership fixtures                                                           #
# --------------------------------------------------------------------------- #

_CODEOWNERS_FILES: dict = {
    "CODEOWNERS": (
        "# ownership rules\n"
        "payments/ @payments-team\n"
        "*.md @docs-team\n"
    ),
    "payments/__init__.py": "",
    "payments/refund.py": "def issue():\n    return 'refund'\n",
    "README.md": "# codeowners fixture\n",
}


@dataclass(frozen=True)
class CodeownersFixture:
    url: str
    clone_path: Path
    expected_sha: str
    owned_path: str
    expected_owner: str


def codeowners_fixture() -> CodeownersFixture:
    repo, sha = build_git_repo("codeowners", _CODEOWNERS_FILES)
    return CodeownersFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        owned_path="payments/refund.py",
        expected_owner="@payments-team",
    )


_NO_CODEOWNERS_FILES: dict = {
    "src/__init__.py": "",
    "src/thing.py": "def thing():\n    return 1\n",
}


@dataclass(frozen=True)
class NoCodeownersFixture:
    url: str
    clone_path: Path
    expected_sha: str
    any_path: str


def no_codeowners_fixture() -> NoCodeownersFixture:
    repo, sha = build_git_repo("no-codeowners", _NO_CODEOWNERS_FILES)
    return NoCodeownersFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        any_path="src/thing.py",
    )


# --------------------------------------------------------------------------- #
# Coverage / language fixtures                                                 #
# --------------------------------------------------------------------------- #


def incomplete_coverage_fixture() -> RepoFixture:
    files = {
        "pkg/__init__.py": "",
        "pkg/mod.py": "def mod():\n    return 1\n",
        "pkg/other.py": "def other():\n    return 2\n",
    }
    repo, sha = build_git_repo("incomplete-coverage", files)
    tracked = sorted(files.keys())
    return RepoFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        expected_file_list=tracked,
        all_files=tracked,
    )


@dataclass(frozen=True)
class UnsupportedLanguageFixture:
    url: str
    clone_path: Path
    expected_sha: str
    unsupported_file: str
    unique_content: str


def unsupported_language_file_fixture() -> UnsupportedLanguageFixture:
    unique = "ZZ_UNSUPPORTED_MARKER_9137"
    files = {
        "pkg/__init__.py": "",
        "pkg/mod.py": "def mod():\n    return 1\n",
        # COBOL: no tree-sitter grammar shipped -> must be flagged, still grep-able.
        "legacy/report.cob": (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. REPORT.\n"
            f"      * {unique}\n"
            "       PROCEDURE DIVISION.\n"
            "           DISPLAY 'HELLO'.\n"
        ),
    }
    repo, sha = build_git_repo("unsupported-language", files)
    return UnsupportedLanguageFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        unsupported_file="legacy/report.cob",
        unique_content=unique,
    )


@dataclass(frozen=True)
class IsolatedSymbolFixture:
    url: str
    clone_path: Path
    expected_sha: str
    isolated_symbol: str
    graph_spec: dict


def isolated_symbol_fixture() -> IsolatedSymbolFixture:
    # "loner" is defined but nothing references it -> zero dependents.
    files = {
        "pkg/__init__.py": "",
        "pkg/loner.py": "def loner():\n    return 'alone'\n",
        "pkg/other.py": "def other():\n    return 1\n",
    }
    repo, sha = build_git_repo("isolated-symbol", files)
    spec = {
        "nodes": [
            {"id": "loner", "kind": "function", "path": "pkg/loner.py", "line": 1},
            {"id": "other", "kind": "function", "path": "pkg/other.py", "line": 1},
        ],
        "edges": [],
    }
    return IsolatedSymbolFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        isolated_symbol="loner",
        graph_spec=spec,
    )
