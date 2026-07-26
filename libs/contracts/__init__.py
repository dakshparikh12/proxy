"""libs.contracts — a TRUE alias of the canonical ``contracts`` package.

Real code lives under ``src/contracts`` (src-layout, AC-REPO-002) and is installed
as the top-level workspace package ``contracts``. Historically this facade re-exported
names ``from .src.contracts``, which executed that source a SECOND time under the
``libs.contracts.src.contracts.*`` module path — giving the running product TWO live
copies of every contract type and TWO copies of the import-time registry
(``CHANNEL_REGISTRY`` / ``MESSAGE_FIELD_CONSUMERS`` / ``assert_registry_closed``). That
split the seam: ``isinstance`` across the two aliases failed, and the closure / field-diff
gate saw only one of the two registries — the exact re-drift Doc 09 §2 exists to forbid.

The fix keeps ``libs/contracts`` the ONE declaration site: this module binds itself to
the already-imported canonical ``contracts`` package object, so ``libs.contracts`` and
``contracts`` are the SAME module — one execution of the source, one registry, one class
identity per wire shape. Every ``from libs.contracts import X`` therefore returns the
identical object as ``from contracts import X`` (CANONICAL §11.5 — a shared type is
described once, in one place).
"""
from __future__ import annotations

import sys as _sys

import contracts as _contracts

# Bind ``libs.contracts`` to the canonical package object itself: every attribute access
# and submodule import now hits the single ``contracts`` module tree (one registry, one
# class per wire shape). No second execution of ``src/contracts`` occurs.
_sys.modules[__name__] = _contracts
