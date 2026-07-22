"""Make the sibling ``_fixtures`` helper importable under --import-mode=importlib.

importlib mode does not prepend the test file's directory to sys.path; this
conftest (imported before collection of this dir) adds just THIS directory so the
sibling ``_fixtures`` module resolves for the qgate unit tests. Scoped to this
folder; touches nothing outside tests/doc03/qgate.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
