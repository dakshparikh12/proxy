"""Repo-root pytest configuration.

Keeps ``services`` a namespace package (no ``services/__init__.py``) so importing
a service never writes ``services/__pycache__`` — which would otherwise show up
as a sixth entry under ``services/`` and break the exact-set check in
``tests/doc00/test_m01_repo.py::test_repo_006``.

The harness-hosted ``control_plane`` deployable-assembly (webhooks, accept,
authz, boot server) is exposed at the ``services.control_plane`` import path by
extending the ``services`` namespace ``__path__`` to include
``services/harness/src`` (where the assembly lives). AC-REPO-006 fixes
``services/*`` to exactly five directories, so ``control_plane`` can never be a
sixth ``services/`` directory — it is a package-config exposure only.
"""
from __future__ import annotations

import os


def _wire_control_plane() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    harness_src = os.path.join(root, "services", "harness", "src")
    if not os.path.isdir(harness_src):
        return
    import services  # namespace package (no __init__.py)

    current = list(getattr(services, "__path__", []))
    if harness_src not in current:
        services.__path__ = current + [harness_src]  # type: ignore[attr-defined]


_wire_control_plane()
