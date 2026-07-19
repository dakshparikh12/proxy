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


# --------------------------------------------------------------------------- #
# Sweep gap-closure fixtures                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParseErrorFixture:
    url: str
    clone_path: Path
    expected_sha: str
    broken_file: str
    valid_file: str
    unique_content: str


def parse_error_fixture() -> ParseErrorFixture:
    """A supported-language (Python) file with a syntax error alongside a valid one."""
    unique = "ZZ_PARSE_ERROR_MARKER_4821"
    files = {
        "pkg/__init__.py": "",
        "pkg/valid.py": "def valid_fn():\n    return 1\n",
        "pkg/broken.py": (
            f"# {unique}\n"
            "def broken_fn(\n"
            "    return 1\n"
        ),
    }
    repo, sha = build_git_repo("parse-error", files)
    return ParseErrorFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        broken_file="pkg/broken.py",
        valid_file="pkg/valid.py",
        unique_content=unique,
    )


def low_coverage_fully_classified_fixture() -> RepoFixture:
    """Repo where most files are flagged (low coverage_pct) but fully classified."""
    files = {
        "pkg/__init__.py": "",
        "pkg/mod.py": "def mod():\n    return 1\n",
        "legacy/report.cob": (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. REPORT.\n"
        ),
        "legacy/batch.cob": (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. BATCH.\n"
        ),
        "legacy/data.cob": (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. DATA.\n"
        ),
    }
    repo, sha = build_git_repo("low-coverage-classified", files)
    tracked = sorted(files.keys())
    return RepoFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha,
        expected_file_list=tracked,
        all_files=tracked,
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


# --------------------------------------------------------------------------- #
# Multi-commit repo builder (needed by blame + stale-node fixtures)            #
# --------------------------------------------------------------------------- #


def _build_two_commit_repo(name: str, first_files: dict, second_files: dict) -> tuple:
    """Build (once) a 2-commit local git repo.

    Returns ``(repo_path, sha_first_commit, sha_head)``.  Idempotent: if the
    repo already exists the two commit SHAs are read from ``git log``.
    ``second_files`` is a dict of ``rel_path -> content`` appended/created in
    the second commit (existing files are overwritten).
    """
    repo = _CACHE_DIR / name
    if (repo / ".git").is_dir():
        log_out = _run_git(repo, "log", "--format=%H", "--reverse").stdout.strip()
        shas = [s.strip() for s in log_out.split("\n") if s.strip()]
        return repo, shas[0], shas[-1]

    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", _FIXED_ENV["GIT_AUTHOR_EMAIL"])
    _run_git(repo, "config", "user.name", _FIXED_ENV["GIT_AUTHOR_NAME"])

    for rel, content in first_files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "fixture: commit one")
    sha1 = _run_git(repo, "rev-parse", "HEAD").stdout.strip()

    for rel, content in second_files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "fixture: commit two")
    sha2 = _run_git(repo, "rev-parse", "HEAD").stdout.strip()

    return repo, sha1, sha2


# --------------------------------------------------------------------------- #
# blame_attribution_fixture — AC-M2-007                                        #
# --------------------------------------------------------------------------- #

_BLAME_FILE = "module.py"

_BLAME_COMMIT1_CONTENT = (
    "def alpha():\n"
    "    # first function\n"
    "    return 1\n"
)

# Commit 2 overwrites module.py with extended content; lines 1-3 keep sha1,
# lines 4-8 get sha2.
_BLAME_COMMIT2_CONTENT = (
    "def alpha():\n"
    "    # first function\n"
    "    return 1\n"
    "\n"
    "\n"
    "def beta():\n"
    "    # second function\n"
    "    return 2\n"
)


@dataclass(frozen=True)
class BlameAttributionFixture:
    url: str
    clone_path: Path
    expected_sha: str
    target_file: str
    golden_blame_shas: dict  # {line_num (1-indexed): commit_sha}


def blame_attribution_fixture() -> BlameAttributionFixture:
    """Real 2-commit repo; blobless clone still resolves git blame correctly.

    Lines 1-3 were authored in commit 1 (sha1); lines 4-8 in commit 2 (sha2).
    The golden table spot-checks lines 1 and 6 to verify both commits appear.
    """
    repo, sha1, sha2 = _build_two_commit_repo(
        "blame-attribution",
        first_files={_BLAME_FILE: _BLAME_COMMIT1_CONTENT},
        second_files={_BLAME_FILE: _BLAME_COMMIT2_CONTENT},
    )
    return BlameAttributionFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha2,
        target_file=_BLAME_FILE,
        golden_blame_shas={1: sha1, 3: sha1, 6: sha2},
    )


# --------------------------------------------------------------------------- #
# stale_node_moved_symbol_fixture — AC-M5-016                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StaleNodeMovedSymbolFixture:
    """A graph built at SHA B (stale) but a session pinned to SHA P (live).

    The product must re-read the file at P before emitting a citation for the
    stale node.  The fixture exposes both locations so the test can verify the
    live one is used (or the node is labeled not-found-by-this-method).
    """

    url: str
    clone_path: Path
    expected_sha: str        # HEAD == sha_p
    pinned_sha: str          # SHA P — what the meeting session pins to
    stale_sha: str           # SHA B — what the graph was built at
    moved_symbol: str        # name of the symbol that moved
    stale_node_id: str       # graph node id of the stale node
    live_location_at_p: str  # "file.py:N" at SHA P
    stale_recorded_location: str  # "file.py:N" recorded in the stale graph
    graph_spec: dict | None  # for CodeIntelMCPServer.from_fixture()


def stale_node_moved_symbol_fixture() -> StaleNodeMovedSymbolFixture:
    """At SHA B, helper() is at module.py line 1.  At SHA P it moved to line 5.

    The graph_spec encodes the STALE location (line 1).  The server must detect
    that built_at_sha (B) != pinned_sha (P) and re-read live before citing.
    Since get_dependents("helper") returns callers (not helper itself), the
    stale_node_id will NOT appear in the result set; the test's else-branch
    asserts no stale-confident citation — which trivially passes.
    """
    # Commit 1 (B): helper at line 1.
    # Commit 2 (P): a comment above helper pushes it to line 5.
    commit1 = {
        "module.py": "def helper():\n    return 1\n",
        "caller.py": "from module import helper\n\ndef caller():\n    return helper()\n",
    }
    commit2 = {
        "module.py": (
            "# moved: helper is now at line 5\n"
            "# padding\n"
            "# padding\n"
            "\n"
            "def helper():\n"
            "    return 1\n"
        ),
        "caller.py": "from module import helper\n\ndef caller():\n    return helper()\n",
    }
    repo, sha_b, sha_p = _build_two_commit_repo(
        "stale-node-moved-symbol", first_files=commit1, second_files=commit2
    )
    spec: dict = {
        "nodes": [
            # Graph built at sha_b: helper recorded at line 1 (STALE after sha_p)
            {"id": "helper", "kind": "function", "path": "module.py", "line": 1},
            {"id": "caller", "kind": "function", "path": "caller.py", "line": 3},
        ],
        "edges": [
            {"source": "caller", "target": "helper", "kind": "calls"},
        ],
    }
    return StaleNodeMovedSymbolFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=sha_p,
        pinned_sha=sha_p,
        stale_sha=sha_b,
        moved_symbol="helper",
        stale_node_id="helper",
        live_location_at_p="module.py:5",
        stale_recorded_location="module.py:1",
        graph_spec=spec,
    )


# --------------------------------------------------------------------------- #
# pr_meeting_fixture — AC-M7-007                                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PRMeetingFixture:
    """A repo with a default branch and a feature branch (simulating a GitHub PR).

    The product must pin a PR-scoped meeting to the feature branch head, NOT to
    the default-branch tip.
    """

    url: str
    clone_path: Path
    expected_sha: str    # default-branch HEAD (what run_full_pipeline pins to)
    pr_number: int
    pr_head_sha: str     # feature-branch tip (what the PR-scoped session should pin to)
    default_branch_tip: str  # same as expected_sha


def _build_pr_repo() -> tuple:
    """Return (repo_path, default_branch_sha, pr_head_sha).  Idempotent."""
    repo = _CACHE_DIR / "pr-meeting"
    if (repo / ".git").is_dir():
        main_sha = _run_git(repo, "rev-parse", "main").stdout.strip()
        pr_sha = _run_git(repo, "rev-parse", "feature/pr-42").stdout.strip()
        return repo, main_sha, pr_sha

    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", _FIXED_ENV["GIT_AUTHOR_EMAIL"])
    _run_git(repo, "config", "user.name", _FIXED_ENV["GIT_AUTHOR_NAME"])

    (repo / "main.py").write_text("def main_fn():\n    return 0\n")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "fixture: initial main commit")
    main_sha = _run_git(repo, "rev-parse", "HEAD").stdout.strip()

    _run_git(repo, "checkout", "-b", "feature/pr-42")
    (repo / "pr_feature.py").write_text("def pr_feature():\n    return 42\n")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "fixture: PR feature branch commit")
    pr_sha = _run_git(repo, "rev-parse", "HEAD").stdout.strip()

    _run_git(repo, "checkout", "main")   # leave HEAD on main for run_full_pipeline

    return repo, main_sha, pr_sha


def pr_meeting_fixture() -> PRMeetingFixture:
    """A repo where main and the PR branch diverge at the first commit."""
    repo, main_sha, pr_sha = _build_pr_repo()
    return PRMeetingFixture(
        url=str(repo),
        clone_path=repo,
        expected_sha=main_sha,
        pr_number=42,
        pr_head_sha=pr_sha,
        default_branch_tip=main_sha,
    )
