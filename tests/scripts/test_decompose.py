"""Tests for scripts/decompose.py."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def _run_decompose(doc_id: str, out_dir) -> None:
    """Run decompose into an ISOLATED out_dir (never the live slices ledger).

    Redirecting only the output path keeps this unit test hermetic: it reads the
    real bundle but must not mutate slices/<id>/tasks.json, which would wipe the
    verified passes:true flags the DONE predicate depends on.
    """
    import decompose  # noqa: PLC0415
    _orig = decompose.slice_dir
    decompose.slice_dir = lambda _id: out_dir  # type: ignore[assignment]
    try:
        decompose.decompose(doc_id)
    finally:
        decompose.slice_dir = _orig  # type: ignore[assignment]


def test_decompose_doc00(tmp_path) -> None:
    out_dir = tmp_path / "slices" / "00"
    _run_decompose("00", out_dir)

    out = out_dir / "tasks.json"
    assert out.exists(), f"tasks.json not written at {out}"

    data = json.loads(out.read_text())
    assert data["spec"] == "00"
    tasks = data["tasks"]
    assert len(tasks) > 0

    first = tasks[0]
    assert len(first["criterion_ids"]) > 0, "first task must have non-empty criterion_ids"
    assert first["passes"] is False
    assert "acceptance" in first

    # A3: at least one task's acceptance cmd contains "pytest -q -k" with a lowercased fragment
    cmds = [t["acceptance"].get("cmd") for t in tasks if t["acceptance"].get("cmd")]
    assert any("pytest -q -k" in (cmd or "") for cmd in cmds), "no task has a pytest -q -k acceptance cmd"
    # A3 correction: fragment must be lowercased (e.g. cmp_001), NOT AC_CMP_001
    joined = " ".join(cmd for cmd in cmds if cmd)
    assert "cmp_" in joined, f"no lowercased 'cmp_' fragment found in acceptance cmds; got: {joined[:200]}"
    assert "AC_CMP" not in joined, "acceptance cmds must NOT contain 'AC_CMP' (that is the wrong format)"

    # B5: mode field present
    assert data.get("mode") == "verify", "tasks.json must have top-level mode==verify for doc00"
