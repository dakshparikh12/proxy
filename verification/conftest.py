"""Pytest bootstrap for the verification harness.

Runs *before* any property test collects, so product top-level imports resolve
and the chosen Hypothesis profile is active. Kept separate from the sealed root
``conftest.py`` so verification never perturbs the arbiter's collection.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `config` importable as a package regardless of pytest's rootdir.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from config import pathsetup  # noqa: E402

pathsetup.bootstrap()

from hypothesis import settings  # noqa: E402

import config.hypothesis_profiles  # noqa: E402,F401  (registers profiles on import)

settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
