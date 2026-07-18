"""services namespace.

Exposes the harness-hosted ``control_plane`` deployable-assembly (webhooks,
accept, authz, boot server) at the ``services.control_plane`` import path
without a sixth ``services/`` directory — AC-REPO-006 fixes services/* to
exactly {harness, code_intel, transport, scribe, workroom}. The assembly code
lives under services/harness/src/control_plane.
"""
from __future__ import annotations

import os as _os

__path__.append(_os.path.join(_os.path.dirname(__file__), "harness", "src"))
