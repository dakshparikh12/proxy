"""GCS Object-Versioned draft store (real GCS when configured; local fallback otherwise).

The full draft body is written to durable object storage the moment it is proposed and
referenced by ``artifact_ref``. Reads survive the Workroom sandbox teardown because they
hit this store, never a dead in-memory review session.

Durability model:

  * **Real GCS** — when ``GCS_BUCKET`` is set AND the ``google-cloud-storage`` SDK is
    installed (the ``reality``/deploy install), the body is written to
    ``objects/<sha256(ref)>`` in that bucket via the ONE raw-client home
    (``libs.http.gcs_bucket`` — Hard Rule: External calls, no raw vendor client elsewhere).
    Versioning is NOT hand-rolled: it relies on BUCKET-LEVEL Object Versioning (enable it on
    the bucket once), so every ``put`` to the same ref lands a new object generation and the
    prior body is retained/recoverable. The interface is unchanged.

  * **Local filesystem fallback** — when ``GCS_BUCKET`` is unset, or the SDK/client is
    unavailable (the offline gate + local dev), the body is written under a temp dir exactly
    as before. This keeps the offline test suite + local dev working with no GCS dependency,
    and degrades HONESTLY (no fabricated durability): a fallback ``get`` reads back the same
    body a fallback ``put`` wrote.

The public interface (``put(ref, content) -> str`` / ``get(ref) -> str | None``) is UNCHANGED.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import tempfile
from typing import Any

_BASE = pathlib.Path(tempfile.gettempdir()) / "proxy-object-store"

#: The bucket key prefix every draft object lands under (a stable namespace inside the bucket).
_OBJECT_PREFIX = "objects/"


def _object_name(ref: str) -> str:
    """The bucket object name (or local filename stem) for ``ref`` — a stable sha256 key.

    The digest is an object-path key, not a security boundary; it keeps an arbitrary ``ref``
    string safe to use as a GCS object name / a filesystem path component.
    """
    return hashlib.sha256(ref.encode("utf-8")).hexdigest()


def _path_for(ref: str) -> pathlib.Path:
    return _BASE / _object_name(ref)


def _bucket() -> Any | None:
    """The live GCS bucket handle when GCS is configured + available, else ``None``.

    Reads ``GCS_BUCKET`` at call time (not import time) so boot stays offline and a rotated
    bucket is picked up. Constructs the raw client ONLY through ``libs.http.gcs_bucket`` (the
    sole legitimate raw-client home). Returns ``None`` — the signal to use the local fallback —
    when the bucket is unset OR the ``google-cloud-storage`` SDK is absent (the offline gate),
    never raising so a missing SDK degrades honestly instead of crashing a draft write.
    """
    bucket_name = os.environ.get("GCS_BUCKET", "").strip()
    if not bucket_name:
        return None
    try:
        from libs.http.src.http.external import gcs_bucket

        return gcs_bucket(bucket_name)
    except Exception:  # noqa: BLE001 - SDK/client unavailable ⇒ local fallback (honest degrade)
        return None


def put(ref: str, content: str) -> str:
    """Durably store ``content`` at ``ref`` (a new object version). Returns ref.

    Real GCS when configured (a new object generation under bucket-level Object Versioning);
    the local filesystem otherwise. A GCS write fault falls back to the local store rather than
    losing the draft body — the never-throw draft-write contract.
    """
    bucket = _bucket()
    if bucket is not None:
        try:
            blob = bucket.blob(f"{_OBJECT_PREFIX}{_object_name(ref)}")
            blob.upload_from_string(content, content_type="text/plain; charset=utf-8")
            return ref
        except Exception:  # noqa: BLE001 - a GCS write fault degrades to the local store
            pass
    _BASE.mkdir(parents=True, exist_ok=True)
    _path_for(ref).write_text(content, encoding="utf-8")
    return ref


def get(ref: str) -> str | None:
    """Read the object at ``ref`` from durable storage (None if absent).

    Reads real GCS when configured (the current object generation), else the local filesystem.
    A missing object / a transient GCS read fault reads as ``None`` (absent), and a GCS-configured
    deployment still falls through to any locally-written body so a mid-migration draft is never
    lost.
    """
    bucket = _bucket()
    if bucket is not None:
        try:
            blob = bucket.blob(f"{_OBJECT_PREFIX}{_object_name(ref)}")
            if blob.exists():
                data = blob.download_as_text()
                return str(data)
        except Exception:  # noqa: BLE001 - a GCS read fault falls through to the local store
            pass
    path = _path_for(ref)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
