"""Tests for scripts/decompose.py."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def _run_decompose(doc_id: str) -> None:
    """Run decompose programmatically via its main() function."""
    import decompose  # noqa: PLC0415
    decompose.decompose(doc_id)


def test_decompose_doc00() -> None:
    _run_decompose("00")

    out = ROOT / "slices" / "00" / "tasks.json"
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
