"""Shared pytest config for the Doc 05 (Workroom) test suite."""
import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "static_: static-analysis oracle tests")
    config.addinivalue_line("markers", "isolation: SDK-isolation-triad safety tests")
