"""pytest config for the Doc 01 staged evidence (marker registration only)."""
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: cheap/fast subset for per-pass runs")
    config.addinivalue_line(
        "markers", "workflow: end-to-end simulation workflow (multi-step chains)"
    )
