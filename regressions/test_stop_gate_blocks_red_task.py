"""Regression: stop_gate.sh must BLOCK when done-check reports a non-green task.

Root cause (fixed 2026-07-22, Phase-5 smoke): in stop_gate.sh the line

    CHECK_OUT=$(./done-check.sh --task "$ID" "$TASK_ID" 2>&1) || true
    CHECK_RC=$?

made ``CHECK_RC`` capture the exit of ``true`` (always 0), so the Stop gate
saw *every* task as green and never emitted a block decision — the loop's
self-repair was silently disabled. The fix drops the ``|| true`` (errexit is
not set, so a nonzero done-check does not abort the hook).

This test drives the real stop_gate.sh + real done-check.sh in a throwaway git
repo with a ``passes:false`` task and asserts the block decision is emitted.
Because done-check --task returns nonzero *before* running any acceptance cmd
when passes:false, the test needs no uv/pytest and stays hermetic.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_stop_gate_blocks_on_passes_false(tmp_path: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    shutil.copy(ROOT / "done-check.sh", tmp_path / "done-check.sh")
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy(ROOT / ".claude/hooks/stop_gate.sh", hooks / "stop_gate.sh")

    slice_dir = tmp_path / "slices" / "x"
    slice_dir.mkdir(parents=True)
    (slice_dir / "tasks.json").write_text(
        json.dumps(
            {
                "spec": "x",
                "tasks": [
                    {
                        "task_id": "T",
                        "acceptance": {"cmd": "pytest -q does_not_matter"},
                        "passes": False,
                    }
                ],
            }
        )
    )
    (slice_dir / ".current").write_text("x:T")

    r = subprocess.run(
        ["bash", str(hooks / "stop_gate.sh")],
        cwd=tmp_path,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
    )

    assert '"decision":"block"' in r.stdout, (
        "stop_gate must block a passes:false task; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
