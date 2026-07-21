"""Make the local ``_fixtures`` helper importable under ``--import-mode=importlib``.

importlib mode does not prepend the test file's directory to ``sys.path`` (that is
its whole point — no basename collisions across the suite). This conftest, which
pytest imports before collecting this directory, adds just THIS directory so the
sibling ``_fixtures`` module resolves for the scribe unit tests. Scoped to this
folder; it touches nothing outside tests/doc03/scribe.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
