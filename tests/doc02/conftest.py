"""Shared pytest config for the Doc 02 (Voice & Transport) test suite."""
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "simulation: simulation-trace oracle tests")
    config.addinivalue_line("markers", "latency: latency-measurement oracle tests")
    config.addinivalue_line("markers", "static_: static-analysis oracle tests")
    config.addinivalue_line("markers", "contract: contract-schema oracle tests")
    config.addinivalue_line("markers", "workflow: end-to-end simulation workflows")
