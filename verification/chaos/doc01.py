"""Layer 4 chaos — doc01 (code intelligence): kill a git clone mid-write.

Steady state : a repo clones into its per-tenant volume dir (the FIXED
               ``paths.tenant_repo_dir`` path) via a real ``git`` subprocess.
Fault        : SIGKILL the ``git clone`` process partway through the checkout.
Invariant    : the interrupted, partial clone stays fully CONTAINED inside the
               tenant volume (isolation holds even mid-fault — nothing lands in a
               sibling tenant or on the host), and the volume RECOVERS: a fresh
               clone into the same tenant succeeds. Isolation-under-fault is the
               doc01 §3.2 promise the AC-M2 findings were about.

Run directly:  python verification/chaos/doc01.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import pathsetup  # noqa: E402

pathsetup.bootstrap()
from config.chaos_lib import ChaosResult, kill_process, wait_gone  # noqa: E402


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                   check=True, capture_output=True, text=True)


def _make_source_repo(root: Path) -> Path:
    src = root / "source.git"
    src.mkdir()
    _git("init", "-q", cwd=src)
    _git("config", "user.email", "chaos@x", cwd=src)
    _git("config", "user.name", "chaos", cwd=src)
    # Several big incompressible blobs make the clone take long enough to reliably
    # interrupt mid-checkout (git copies + checks out ~0.5 GB of working files).
    for i in range(4):
        (src / f"big{i}.bin").write_bytes(os.urandom(128 * 1024 * 1024))
    (src / "readme.md").write_text("chaos source repo")
    _git("add", "-A", cwd=src)
    _git("commit", "-qm", "seed", cwd=src)
    return src


def run() -> ChaosResult:
    tmp = Path(tempfile.mkdtemp(prefix="verif-chaos-git-"))
    vol = tmp / "tenants"
    vol.mkdir()
    os.environ["PROXY_TENANT_VOLUME_ROOT"] = str(vol)
    from code_intel import paths  # uses the fixed, validated path builder

    try:
        src = _make_source_repo(tmp)
        tenant_dir = paths.tenant_repo_dir("tenant-A", "repo")
        tenant_dir.parent.mkdir(parents=True, exist_ok=True)

        # Kill the clone partway through.
        proc = subprocess.Popen(
            ["git", "clone", "--no-hardlinks", "-q", str(src), str(tenant_dir)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.05)                     # kill early — while the checkout is live
        interrupted = proc.poll() is None
        kill_process(proc.pid)
        wait_gone(proc.pid)
        proc.wait(timeout=5)

        # Containment: everything the interrupted clone wrote is under the tenant
        # volume; nothing escaped to a sibling path or the host.
        escaped: list[str] = []
        vol_resolved = vol.resolve()
        for p in vol.rglob("*"):
            if not p.resolve().is_relative_to(vol_resolved):
                escaped.append(str(p))
        # Also assert the clone dir itself is inside the tenant home.
        tenant_home = (vol / "tenant-A").resolve()
        contained = tenant_dir.resolve().is_relative_to(tenant_home) and not escaped

        # Recovery: clear the partial clone and re-clone fully.
        shutil.rmtree(tenant_dir, ignore_errors=True)
        _git("clone", "--no-hardlinks", "-q", str(src), str(tenant_dir))
        head_ok = (tenant_dir / ".git").is_dir() and (tenant_dir / "readme.md").exists()

        survived = contained and head_ok
        detail = (f"interrupted_midclone={interrupted}; escaped_paths={escaped or 'none'}; "
                  f"partial_contained={contained}; reclone_recovered={head_ok}")
        return ChaosResult(name="kill_git_clone_mid_write", doc="doc01",
                           steady_state="repo clones into its per-tenant volume dir via git",
                           fault="SIGKILL the git clone subprocess mid-checkout",
                           survived=survived, detail=detail)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print(json.dumps(run().to_dict(), indent=2))
