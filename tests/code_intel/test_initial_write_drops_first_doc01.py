"""G6 · defense-in-depth — the INITIAL run_full_pipeline write is drop-before-insert.

The gap: ``run_full_pipeline`` wrote the first graph.db with ``drop_first=False``,
relying on ``Cloner.clone()`` having rmtree'd the repo_dir for a clean DB. But
``graph_edges`` uses a plain ``INSERT`` (not INSERT OR REPLACE, graph_store.py),
so if a stale ``graph.db`` ever survived at ``repo_dir/graph.db`` the first write
would accumulate duplicate edges and orphan nodes. The schema test asserts the
drop-before-insert rebuild invariant; this test asserts it holds on the FIRST
build too — proven THROUGH run_full_pipeline on real flask, via the same
``db_operation_counter`` seam AC-M4-009 uses for the rebuild path — so a clean DB
is guaranteed independent of cloner behaviour.
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


@pytest.mark.integration
def test_ac_m4_009b_initial_write_drops_before_insert_on_real_flask() -> None:
    """The FIRST run_full_pipeline write drops before it inserts (real flask)."""
    from tests.fixtures.stubs import DBOperationCounter
    from services.code_intel.pipeline import run_full_pipeline

    repo = _clone_estate("flask", "https://github.com/pallets/flask", _FLASK_SHA)

    db_counter = DBOperationCounter()
    pipeline = run_full_pipeline(
        tenant_id="t-drop-initial",
        repo_url=str(repo),
        db_operation_counter=db_counter,
    )

    # graph.db must exist and be well-formed.
    db_path = pipeline.graph_db_path
    assert db_path.exists(), f"graph db not written at {db_path}"

    ops = db_counter.recorded_operations
    drop_idx = next(
        (i for i, op in enumerate(ops) if op.type in ("DROP", "DELETE_ALL", "TRUNCATE")),
        None,
    )
    insert_idx = next((i for i, op in enumerate(ops) if op.type == "INSERT"), None)

    assert drop_idx is not None, (
        f"initial run_full_pipeline write recorded NO DROP before insert: "
        f"{[op.type for op in ops]}"
    )
    assert insert_idx is not None, "initial write recorded no INSERT"
    assert drop_idx < insert_idx, (
        "initial write must DROP before INSERT so a stale graph.db cannot "
        f"accumulate duplicate edges/orphan nodes: {[op.type for op in ops]}"
    )

    # Data consequence: a clean write persists EXACTLY the builder's graph — no
    # accumulation from a plain INSERT over a pre-existing table. The persisted
    # edge-row count equals the in-memory graph edge count, and node ids are
    # unique (no orphan ids from a prior build). Proven on real flask rows.
    # graph_edges is a plain INSERT (no PK dedup), so a clean write persists
    # EXACTLY the builder's edge count. A stale surviving table would push this
    # ABOVE the builder count — that is the bug this defends against.
    n_graph_edges = len(pipeline.graph.edges)
    # graph_nodes is INSERT OR REPLACE keyed by id, so persisted node rows equal
    # the builder's DISTINCT node ids. A stale table with ids no longer built
    # would leave ORPHAN rows above that distinct set.
    builder_node_ids = {n.id for n in pipeline.graph.nodes}
    conn = sqlite3.connect(str(db_path))
    try:
        n_edge_rows = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        assert n_edge_rows == n_graph_edges, (
            f"persisted edge rows {n_edge_rows} != builder edges {n_graph_edges} "
            "(a stale table would have accumulated extra INSERTs)"
        )

        n_node_rows = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        n_distinct = conn.execute(
            "SELECT COUNT(DISTINCT id) FROM graph_nodes"
        ).fetchone()[0]
        assert n_node_rows > 0 and n_node_rows == n_distinct == len(builder_node_ids), (
            f"orphan/duplicate node ids: rows={n_node_rows} distinct={n_distinct} "
            f"builder_distinct={len(builder_node_ids)}"
        )
        # No persisted node id is outside the current build (no orphans).
        persisted_ids = {r[0] for r in conn.execute("SELECT id FROM graph_nodes")}
        assert persisted_ids == builder_node_ids, (
            f"orphan node ids not in current build: "
            f"{list(persisted_ids - builder_node_ids)[:5]}"
        )
    finally:
        conn.close()
