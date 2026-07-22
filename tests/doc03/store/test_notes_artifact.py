"""AC-STORE gcs:objects oracles + the pure generation/URL discipline.

The gcs:objects integration oracles (write/list/read against a real versioned
bucket) are written against the real ``scribe.notes_artifact`` seam and skipped
via ``requires_gcs`` — a real bucket is not available this session and stubbing
the gcs seam is forbidden. The PURE surface — the NotesGenerationConflictError
mapping, the never-float generation discipline (AC-STORE-11), and the v4/10-min/
GET signed-URL params + no-base64 audit (AC-STORE-14/-14-NEG) — runs
unconditionally: it needs no bucket and it is where the load-bearing correctness
of the seam lives.
"""
from __future__ import annotations

import ast
import inspect
import os
from datetime import timedelta

import pytest

from scribe import notes_artifact
from scribe.notes_artifact import (
    NOTES_OBJECT_TEMPLATE,
    SIGNED_URL_EXPIRATION,
    SIGNED_URL_METHOD,
    SIGNED_URL_VERSION,
    NotesGenerationConflictError,
    _normalise_generation,
    read_notes_version,
    write_finalized_notes,
)

from .conftest import requires_gcs


def _module_code_without_docstrings(module: object) -> str:
    """Return a module's source with EVERY docstring stripped.

    The no-base64 audit (AC-STORE-14-NEG) must inspect executable code, not the
    prose that says "never inline base64" — otherwise the test would fail on its
    own explanation (a wrong-reason failure). This walks the AST, drops each
    docstring node (module + every def/class), and unparses the rest.
    """
    src = inspect.getsource(module)  # type: ignore[arg-type]
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Constant
            ) and isinstance(body[0].value.value, str):
                node.body = body[1:]
    return ast.unparse(tree)


# ===========================================================================
# PURE — run unconditionally (no bucket needed).
# ===========================================================================

# -- AC-STORE-11 -- generation stored/passed as str or int, NEVER float --------
def test_store_11_generation_normaliser_rejects_float() -> None:
    """T-STORE-11: a float generation is rejected — uint64 >= 2^53 is corrupted by
    float rounding, so the seam never lets one through."""
    with pytest.raises(TypeError):
        _normalise_generation(9007199254740992.0)


def test_store_11_generation_normaliser_preserves_int_and_str() -> None:
    """T-STORE-11: an int/str generation round-trips EXACTLY (no lossy coercion)."""
    big = 1719000000000000
    assert _normalise_generation(big) == big
    assert isinstance(_normalise_generation(big), int)
    assert not isinstance(_normalise_generation(big), float)
    assert _normalise_generation(str(big)) == str(big)
    assert isinstance(_normalise_generation(str(big)), str)


def test_store_11_write_rejects_float_precondition() -> None:
    """T-STORE-11: write_finalized_notes refuses a float if_generation_match before
    it ever touches GCS (the float can never reach the wire)."""
    with pytest.raises(TypeError):
        write_finalized_notes(
            object(), "m1", "notes", if_generation_match=9007199254740992.0
        )


# -- AC-STORE-11-NEG -- float rounding corrupts a generation >= 2^53 -----------
def test_store_11neg_float_rounds_a_generation_above_2_53() -> None:
    """T-STORE-11-NEG: 9007199254740993 -> float -> 9007199254740992.0 != original.
    Storing the rounded value as if_generation_match would draw a 412 from GCS —
    which is exactly why floats are banned (this documents the corruption)."""
    gen = 9007199254740993
    rounded = float(gen)
    assert int(rounded) != gen                 # the round-trip is LOSSY
    # And the seam refuses to pass the rounded value as a precondition:
    with pytest.raises(TypeError):
        _normalise_generation(rounded)


# -- AC-STORE-10 -- NotesGenerationConflictError shape / 412 mapping ------------
def test_store_10_conflict_error_shape() -> None:
    """T-STORE-10: the typed conflict carries the meeting + expected generation and
    the preserved cross-doc name (their SpecGenerationConflictError -> ours)."""
    err = NotesGenerationConflictError("m-42", 0)
    assert err.name == "NotesGenerationConflictError"
    assert err.meeting_id == "m-42"
    assert err.expected_generation == 0
    assert "m-42" in str(err) and "generation 0" in str(err)


def test_store_10_write_maps_precondition_failed_to_conflict() -> None:
    """T-STORE-10 static: write_finalized_notes catches PreconditionFailed (HTTP
    412) and raises NotesGenerationConflictError — NO other exception type on 412."""
    src = inspect.getsource(write_finalized_notes)
    assert "PreconditionFailed" in src
    assert "raise NotesGenerationConflictError" in src
    # The create-only semantics live in the docstring/contract (0 = create-only).
    assert "if_generation_match" in src


def test_store_10_object_key_is_per_meeting() -> None:
    """T-STORE-10 support: every write targets the same per-meeting key, so Object
    Versioning stacks generations rather than making distinct objects."""
    assert NOTES_OBJECT_TEMPLATE.format(meeting_id="M") == "meetings/M/notes.md"


# -- AC-STORE-14 / -14-NEG -- screenshared image via v4 signed URL, never base64 -
def test_store_14_signed_url_params_are_v4_10min_get() -> None:
    """T-STORE-14: the grounding image is handed to Claude as a v4 signed URL with
    a 10-minute expiry and GET method (pinned constants a static audit can prove)."""
    assert SIGNED_URL_VERSION == "v4"
    assert SIGNED_URL_EXPIRATION == timedelta(minutes=10)
    assert SIGNED_URL_EXPIRATION.total_seconds() == 600
    assert SIGNED_URL_METHOD == "GET"
    src = inspect.getsource(notes_artifact.signed_url_for_grounding_image)
    assert "generate_signed_url" in src
    assert "version=SIGNED_URL_VERSION" in src
    assert "expiration=SIGNED_URL_EXPIRATION" in src
    assert "method=SIGNED_URL_METHOD" in src


def test_store_14neg_no_base64_in_grounding_path() -> None:
    """T-STORE-14-NEG: the grounding/artifact module NEVER base64-encodes an image
    for the Claude payload (a static grep of the CODE, docstrings stripped, finds
    zero base64 image encoding)."""
    code = _module_code_without_docstrings(notes_artifact).lower()
    assert "b64encode" not in code
    assert "base64" not in code
    assert "data:image" not in code
    assert "import base64" not in code


# ===========================================================================
# INTEGRATION — real GCS bucket, skipped this session (gcs:objects).
# ===========================================================================
def _bucket() -> object:
    from google.cloud import storage  # lazy: SDK only needed for the live tier

    client = storage.Client()
    return client.get_bucket(os.environ["DOC03_STORE_GCS_BUCKET"])


# -- AC-STORE-09 / -NEG -- bucket-level Object Versioning ON --------------------
@requires_gcs
def test_store_09_bucket_versioning_enabled() -> None:
    """T-STORE-09: the finalized-notes bucket has native Object Versioning ON."""
    bucket = _bucket()
    assert bucket.versioning_enabled is True  # type: ignore[attr-defined]


@requires_gcs
def test_store_09neg_versioning_off_loses_prior_generations() -> None:
    """T-STORE-09-NEG: on a no-versioning bucket, overwriting collapses history to
    one generation — the guard that versioning MUST be ON for list/read to work."""
    bucket = _bucket()
    mid = "m-store-09neg"
    write_finalized_notes(bucket, mid, "first", if_generation_match=None)
    write_finalized_notes(bucket, mid, "second", if_generation_match=None)
    versions = notes_artifact.list_notes_versions(bucket, mid)
    assert len(versions) == 1  # prior generation gone (versioning was off)


# -- AC-STORE-10 / -NEG -- create-only + unconditional overwrite ---------------
@requires_gcs
def test_store_10_create_only_then_conflict() -> None:
    """T-STORE-10: create-only write succeeds once; a second create-only raises
    NotesGenerationConflictError and the object still holds the first content."""
    bucket = _bucket()
    mid = "m-store-10"
    gen1 = write_finalized_notes(bucket, mid, "first", if_generation_match=0)
    assert gen1 > 0
    with pytest.raises(NotesGenerationConflictError):
        write_finalized_notes(bucket, mid, "second", if_generation_match=0)
    assert read_notes_version(bucket, mid, gen1) == "first"


@requires_gcs
def test_store_10neg_unconditional_overwrite_does_not_raise() -> None:
    """T-STORE-10-NEG: if_generation_match=None overwrites without raising; a new
    generation is assigned."""
    bucket = _bucket()
    mid = "m-store-10neg"
    g1 = write_finalized_notes(bucket, mid, "first", if_generation_match=None)
    g2 = write_finalized_notes(bucket, mid, "second", if_generation_match=None)
    assert g2 != g1


# -- AC-STORE-11 (integration half) -- generation round-trips as int -----------
@requires_gcs
def test_store_11_generation_roundtrips_as_int_not_float() -> None:
    """T-STORE-11 integration: the generation GCS returns is int (never float) and
    round-trips as an exact if_generation_match precondition."""
    bucket = _bucket()
    mid = "m-store-11"
    gen = write_finalized_notes(bucket, mid, "v1", if_generation_match=0)
    assert isinstance(gen, int) and not isinstance(gen, float)
    # Exact-match write with the stored generation is accepted (no 412).
    gen2 = write_finalized_notes(bucket, mid, "v2", if_generation_match=gen)
    assert isinstance(gen2, int)


# -- AC-STORE-12 / -NEG -- list all generations, read any by generation --------
@requires_gcs
def test_store_12_list_and_read_versions_by_generation() -> None:
    """T-STORE-12: three writes -> three generations; read G1 gives write1, G3 the
    latest."""
    bucket = _bucket()
    mid = "m-store-12"
    g1 = write_finalized_notes(bucket, mid, "write1", if_generation_match=None)
    write_finalized_notes(bucket, mid, "write2", if_generation_match=None)
    g3 = write_finalized_notes(bucket, mid, "write3", if_generation_match=None)
    versions = notes_artifact.list_notes_versions(bucket, mid)
    assert len(versions) == 3
    assert read_notes_version(bucket, mid, g1) == "write1"
    assert read_notes_version(bucket, mid, g3) == "write3"


@requires_gcs
def test_store_12neg_gcs_fault_degrades_honestly() -> None:
    """T-STORE-12-NEG: a real gcs fault at the seam surfaces (no silent proceed)."""
    from google.cloud import storage

    client = storage.Client()
    missing = client.bucket("proxy-doc03-store-nonexistent-bucket-xyz")
    with pytest.raises(Exception):
        notes_artifact.list_notes_versions(missing, "m-x")
