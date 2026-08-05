"""SD-3 — the workroom object store uses real GCS when configured, the local FS otherwise.

The store was a ``tempfile`` stub. It is now real GCS Object-Versioned storage (via the ONE
raw-client home ``libs.http.gcs_bucket``, keyed by ``GCS_BUCKET``) with a local-filesystem
fallback so the offline gate + local dev keep working with no GCS dependency. The public
interface (``put(ref, content) -> str`` / ``get(ref) -> str | None``) is UNCHANGED. These proofs
pin both branches:

* with ``GCS_BUCKET`` set + a bucket available, ``put``/``get`` hit the GCS blob API (a fake
  bucket injected at the ``libs.http.gcs_bucket`` seam — never real network in the gate);
* with ``GCS_BUCKET`` unset, ``put``/``get`` round-trip through the local filesystem fallback.
"""
from __future__ import annotations

from typing import Any

from workroom import objectstore


class _FakeBlob:
    """An in-memory stand-in for a GCS blob (upload/download/exists)."""

    def __init__(self, store: dict[str, str], name: str) -> None:
        self._store = store
        self._name = name

    def upload_from_string(self, content: str, content_type: str | None = None) -> None:
        # A new generation lands here; bucket-level Object Versioning retains the prior one
        # (not modelled — the store keeps the CURRENT generation, which is what ``get`` reads).
        self._store[self._name] = content

    def exists(self) -> bool:
        return self._name in self._store

    def download_as_text(self) -> str:
        return self._store[self._name]


class _FakeBucket:
    """An in-memory GCS bucket: records that the GCS branch (not the FS fallback) was taken."""

    def __init__(self) -> None:
        self._objects: dict[str, str] = {}
        self.blobs_requested: list[str] = []

    def blob(self, name: str) -> _FakeBlob:
        self.blobs_requested.append(name)
        return _FakeBlob(self._objects, name)


def test_put_and_get_use_gcs_when_configured(monkeypatch: Any) -> None:
    """With ``GCS_BUCKET`` set + a bucket available, put/get hit the GCS blob API (not the FS)."""
    fake = _FakeBucket()

    monkeypatch.setenv("GCS_BUCKET", "proxy-drafts-test")
    # Inject the fake at the ONE raw-client home the objectstore calls through.
    monkeypatch.setattr(
        "libs.http.src.http.external.gcs_bucket", lambda name: fake, raising=True
    )

    ref = "draft:meeting-1:artifact-7"
    assert objectstore.put(ref, "the durable draft body") == ref
    # The GCS branch ran — a blob was requested against the configured bucket.
    assert fake.blobs_requested, "expected the GCS blob API to be used when GCS_BUCKET is set"

    # And the read comes back off GCS (same body), proving get() reads the GCS branch too.
    assert objectstore.get(ref) == "the durable draft body"

    # A ref with no stored object reads as absent (None), not a raised error.
    assert objectstore.get("draft:never-written") is None


def test_put_and_get_use_local_fallback_when_unconfigured(monkeypatch: Any, tmp_path: Any) -> None:
    """With ``GCS_BUCKET`` unset, put/get round-trip through the local filesystem fallback."""
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    # Redirect the local store base under tmp so the test never touches a shared temp path.
    monkeypatch.setattr(objectstore, "_BASE", tmp_path / "object-store", raising=True)

    # The seam resolves to None (no bucket) — the local branch is what runs.
    assert objectstore._bucket() is None

    ref = "draft:local:1"
    assert objectstore.put(ref, "local body") == ref
    assert objectstore.get(ref) == "local body"
    assert objectstore.get("draft:local:missing") is None
    # The body really landed on the local filesystem (durability without GCS).
    assert (tmp_path / "object-store").exists()


def test_gcs_write_fault_falls_back_to_local(monkeypatch: Any, tmp_path: Any) -> None:
    """A GCS write fault degrades to the local store rather than losing the draft body."""
    monkeypatch.setenv("GCS_BUCKET", "proxy-drafts-test")
    monkeypatch.setattr(objectstore, "_BASE", tmp_path / "object-store", raising=True)

    class _BrokenBucket:
        def blob(self, name: str) -> Any:
            raise RuntimeError("GCS unavailable")

    monkeypatch.setattr(
        "libs.http.src.http.external.gcs_bucket", lambda name: _BrokenBucket(), raising=True
    )

    ref = "draft:degrade:1"
    # put swallows the GCS fault and writes locally; get finds the locally-written body.
    assert objectstore.put(ref, "salvaged body") == ref
    assert objectstore.get(ref) == "salvaged body"
