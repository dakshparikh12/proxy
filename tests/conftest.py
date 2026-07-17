"""Shared pytest configuration for the Doc 01 test suite."""
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: cheap/fast subset for per-pass runs")
