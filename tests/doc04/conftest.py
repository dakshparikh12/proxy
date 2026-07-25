"""Shared pytest config for the Doc 04 (Orchestrator) test suite."""
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "static_: static-analysis oracle tests")
    config.addinivalue_line("markers", "contract: contract-schema oracle tests")
    config.addinivalue_line("markers", "integration: assembled-tree integration tests")
