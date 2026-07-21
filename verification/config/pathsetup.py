"""Shared product-import bootstrap for the verification system.

The repo installs each uv-workspace member editable, but the generated ``.pth``
finder files are unreliable (see the root ``conftest.py`` note), so product
top-level imports (``contracts``, ``code_intel``, ``transport`` …) only resolve
when every member's ``src`` directory is on ``sys.path``. Any verification layer
that imports product code calls :func:`bootstrap` first; the ``pytest`` conftest
under ``verification/`` calls it at collection time.

This is deliberately identical in spirit to the root conftest so the verification
harness sees exactly the same import surface the sealed arbiter does — no more, no
less. It is pure path manipulation: it never imports, mutates, or executes product
code, so it is safe to call repeatedly and from any layer.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# verification/config/pathsetup.py -> repo root is two parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]


def member_src_dirs() -> list[Path]:
    """Every workspace member's ``src`` dir (``libs/*/src`` + ``services/*/src``)."""
    dirs: list[Path] = []
    for group in ("libs", "services"):
        base = REPO_ROOT / group
        if not base.is_dir():
            continue
        for member in sorted(base.iterdir()):
            src = member / "src"
            if src.is_dir():
                dirs.append(src)
    return dirs


def bootstrap() -> Path:
    """Put the repo root and every member ``src`` dir on ``sys.path``.

    Returns the repo root. Idempotent. Also exports ``PROXY_VERIFICATION=1`` so
    product code (and DeepEval callbacks) can tell they are running under the
    verification harness rather than the sealed arbiter.
    """
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    for src in member_src_dirs():
        s = str(src)
        if s not in sys.path:
            sys.path.append(s)
    os.environ.setdefault("PROXY_VERIFICATION", "1")
    return REPO_ROOT


def pythonpath() -> str:
    """A ``PYTHONPATH`` string for spawning subprocesses that import product code."""
    parts = [str(REPO_ROOT)] + [str(p) for p in member_src_dirs()]
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)


if __name__ == "__main__":
    # `python -m config.pathsetup` prints the PYTHONPATH — used by shell layers.
    print(pythonpath())
