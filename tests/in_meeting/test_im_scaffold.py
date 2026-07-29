"""Scaffold acceptance test for services/in-meeting (Task F1).

Asserts:
- ``import in_meeting`` succeeds and the package has the expected docstring.
- Package metadata is correct (name, version, requires-python).
- The workspace dependency seams are importable:
    - ``db.Database``       from libs/db
    - ``call_external``     from libs/http (via its filesystem path, avoiding stdlib ``http`` shadow)
- The provider seam is reachable at its current home in harness.
"""
from __future__ import annotations

import importlib.metadata


def test_import_in_meeting() -> None:
    """``import in_meeting`` must succeed."""
    import in_meeting  # noqa: PLC0415

    assert in_meeting.__doc__ is not None
    assert "in-meeting" in in_meeting.__doc__ or "in_meeting" in in_meeting.__doc__


def test_package_metadata() -> None:
    """Package metadata must match pyproject.toml."""
    meta = importlib.metadata.metadata("in-meeting")
    assert meta["Name"] == "in-meeting"
    assert meta["Version"] == "0.0.0"
    assert meta["Requires-Python"] == ">=3.12"


def test_db_seam_importable() -> None:
    """``from db import Database`` must succeed (libs/db workspace dep)."""
    from db import Database  # noqa: PLC0415

    assert Database is not None


def test_http_seam_importable() -> None:
    """``call_external`` from libs/http must be importable (via filesystem path; avoids stdlib shadow)."""
    from libs.http.src.http.external import call_external  # noqa: PLC0415

    assert call_external is not None


def test_provider_seam_reachable() -> None:
    """``ClaudeAgentProvider`` must be importable from its current harness home."""
    from harness.provider import ClaudeAgentProvider  # noqa: PLC0415

    assert ClaudeAgentProvider is not None
