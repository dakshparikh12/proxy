"""GCS Object-Versioned draft store (local filesystem stand-in for the MVP).

The full draft body is written to durable object storage the moment it is
proposed and referenced by ``artifact_ref``. Reads survive the Workroom sandbox
teardown because they hit this store, never a dead in-memory review session.
Production swaps this for GCS with Object Versioning; the interface is unchanged.
"""
from __future__ import annotations

import hashlib
import pathlib
import tempfile

_BASE = pathlib.Path(tempfile.gettempdir()) / "proxy-object-store"


def _path_for(ref: str) -> pathlib.Path:
    digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()  # object path key, not security
    return _BASE / digest


def put(ref: str, content: str) -> str:
    """Durably store ``content`` at ``ref`` (a new object version). Returns ref."""
    _BASE.mkdir(parents=True, exist_ok=True)
    _path_for(ref).write_text(content, encoding="utf-8")
    return ref


def get(ref: str) -> str | None:
    """Read the object at ``ref`` from durable storage (None if absent)."""
    path = _path_for(ref)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
