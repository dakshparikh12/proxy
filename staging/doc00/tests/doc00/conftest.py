"""Pytest configuration for the Doc 00 evidence suite.

Puts the repo root and this package dir on ``sys.path`` so that (a) sibling
``import _support`` works and (b) in-body product imports (``import libs`` /
``import services``) resolve from the repo root once the product exists. No
product code is imported here, so collection stays clean pre-build.
"""

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent

_root = _HERE
for _parent in (_HERE, *_HERE.parents):
    if (_parent / ".git").exists():
        _root = _parent
        break

for _p in (str(_HERE), str(_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: cheap/fast subset for per-pass runs")
    for _m in (
        "contract", "static", "deployment", "integration", "model_stateful",
        "security_adversarial", "fault_injection", "performance", "workflow",
        "unit_property", "unit_example",
    ):
        config.addinivalue_line("markers", f"{_m}: doc00 evidence class {_m}")
