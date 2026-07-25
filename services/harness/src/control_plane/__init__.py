"""control_plane deployable-assembly (harness-hosted): webhooks, connect page,
API, WS gateway, auth. Exposed at the ``services.control_plane`` import path via
services/__init__.py — never a sixth services/ directory (AC-REPO-006).

Re-exports ``create_app`` (the ASGI app factory in :mod:`control_plane.app`) so the
live app — including the §12.9 WS ``/ws`` upgrade gateway mounted there — is importable
as ``services.control_plane.create_app``.
"""
from __future__ import annotations

from .app import app as app
from .app import create_app as create_app

__all__ = ["app", "create_app"]
