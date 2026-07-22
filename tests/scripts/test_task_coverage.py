"""Tests for scripts/task_coverage.py."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def test_task_coverage_doc00_exits_zero() -> None:
    """After decompose("00"), task_coverage("00") must exit 0 (no gaps)."""
    import decompose  # noqa: PLC0415
    import task_coverage  # noqa: PLC0415

    decompose.decompose("00")
    rc = task_coverage.task_coverage("00")
    assert rc == 0, "task_coverage must exit 0 when all criteria are covered and no dangling refs"
