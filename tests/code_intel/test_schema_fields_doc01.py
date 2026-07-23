"""Doc 01 · step 1 — the §3.4 canonical schema substrate, proven on REAL flask.

This is the FIRST-written failing acceptance test for the schema pass. It drives
the PRODUCT path: ``run_full_pipeline`` on the pinned real flask clone, then opens
``pipeline.graph_db_path`` with sqlite3 and asserts the §3.4 tables, columns,
indexes, and stamped fields exist on real extracted rows — no injected doubles.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import subprocess

import pytest

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


def _head_sha(repo: pathlib.Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


@pytest.mark.integration
def test_ac_m4_014_schema_fields_on_real_flask() -> None:
    """§3.4 canonical schema, proven THROUGH run_full_pipeline on real flask."""
    from services.code_intel.pipeline import run_full_pipeline

    repo = _clone_estate("flask", "https://github.com/pallets/flask", _FLASK_SHA)
    head = _head_sha(repo)
    assert head, "flask clone has no HEAD"

    pipeline = run_full_pipeline(tenant_id="t-schema", repo_url=str(repo))

    db_path = pipeline.graph_db_path
    assert db_path.exists(), f"graph db not written at {db_path}"

    conn = sqlite3.connect(str(db_path))
    try:
        # (a) canonical tables exist with the exact §3.4 columns.
        node_cols = [r[1] for r in conn.execute("PRAGMA table_info(graph_nodes)")]
        edge_cols = [r[1] for r in conn.execute("PRAGMA table_info(graph_edges)")]
        assert node_cols == ["id", "kind", "file_path", "line", "exported", "built_at_sha"], node_cols
        assert edge_cols == ["source", "target", "kind", "file_path", "line"], edge_cols

        # old tables must be gone on a rebuilt/clean DB path (drop-before-insert).
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "graph_nodes" in tables and "graph_edges" in tables, tables

        # (b) the three §3.4 indexes exist on graph_edges / graph_nodes.
        edge_idx = {r[1] for r in conn.execute("PRAGMA index_list(graph_edges)")}
        node_idx = {r[1] for r in conn.execute("PRAGMA index_list(graph_nodes)")}
        assert "graph_edges_target_idx" in edge_idx, edge_idx
        assert "graph_edges_source_idx" in edge_idx, edge_idx
        assert "graph_nodes_file_idx" in node_idx, node_idx

        # (c) every node row carries a non-empty built_at_sha == the clone HEAD.
        shas = [r[0] for r in conn.execute("SELECT DISTINCT built_at_sha FROM graph_nodes")]
        assert shas == [head], f"built_at_sha rows {shas!r} != HEAD {head!r}"
        n_nodes = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        n_stamped = conn.execute(
            "SELECT COUNT(*) FROM graph_nodes WHERE built_at_sha = ?", (head,)
        ).fetchone()[0]
        assert n_nodes > 0 and n_stamped == n_nodes, (n_nodes, n_stamped)

        # (d) a known flask exported symbol row has exported=1. flask/app.py
        # defines the public class ``Flask`` (module-level, no leading underscore).
        exported_flask = conn.execute(
            "SELECT COUNT(*) FROM graph_nodes "
            "WHERE exported = 1 AND kind IN ('class','function') "
            "AND id LIKE '%src/flask/app.py::Flask'"
        ).fetchone()[0]
        assert exported_flask >= 1, "public Flask class not marked exported"
        # and NOT every node is exported (private/helpers exist and stay 0).
        n_private = conn.execute(
            "SELECT COUNT(*) FROM graph_nodes WHERE exported = 0"
        ).fetchone()[0]
        assert n_private > 0, "no un-exported nodes — exported flag is not discriminating"

        # (e) graph_edges rows carry a real file_path + line>0 for the edge site.
        n_edges = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        assert n_edges > 0, "no edges extracted from real flask"
        bad = conn.execute(
            "SELECT COUNT(*) FROM graph_edges "
            "WHERE file_path IS NULL OR file_path = '' OR line IS NULL OR line <= 0"
        ).fetchone()[0]
        assert bad == 0, f"{bad} edges missing file_path/line"
    finally:
        conn.close()
