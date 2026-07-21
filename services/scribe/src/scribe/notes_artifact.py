"""Plane 2 — the finalized notes artifact on GCS (§3.3, native Object Versioning).

The polished markdown notes file (the close deliverable, §3.7, and any in-meeting
*finalized* snapshot) is a whole-object write to GCS with **bucket-level Object
Versioning ON**, so history/restore is free and a lost-update race becomes an
explicit typed failure. The close summarizer and a human editing the live notes
doc both write the same object; ``if_generation_match`` turns that
read-modify-write race into a 412 we catch as :class:`NotesGenerationConflictError`
(their ``SpecGenerationConflictError`` → our name, PART VI.2).

Generation discipline (AC-STORE-11): GCS generations are **uint64** (e.g.
``1719000000000000``). A float would silently corrupt any value ≥ 2^53. This
module therefore NEVER coerces a generation through ``float`` — it keeps them as
read (``int``/``str``) and passes them back verbatim.

Import note: ``google.cloud.storage`` is imported *lazily*, inside the functions
that touch a live bucket. That keeps the pure surface — the error type, the
generation-normaliser, the signed-URL params — importable and unit-testable on a
host without the GCS SDK installed (the integration tier supplies the real bucket).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

# The finalized-notes object key (per meeting). A single well-known key per
# meeting means every write targets the same object, so Object Versioning stacks
# the generations (§3.3).
NOTES_OBJECT_TEMPLATE = "meetings/{meeting_id}/notes.md"

# v4 signed-URL parameters for screenshared-image grounding (AC-STORE-14). Pinned
# constants so a static audit can prove them: v4 / 10-minute expiry / GET.
SIGNED_URL_VERSION = "v4"
SIGNED_URL_EXPIRATION = timedelta(minutes=10)
SIGNED_URL_METHOD = "GET"


class NotesGenerationConflictError(Exception):
    """The typed lost-update failure (§3.3): GCS returned HTTP 412 on a write.

    Raised when ``write_finalized_notes`` was given an ``if_generation_match``
    precondition (create-only ``0`` or an exact prior ``<gen>``) and the object's
    server-side generation no longer matches — i.e. someone else wrote since we
    read. The name is preserved on the instance (their ``SpecGenerationConflictError``
    → our ``NotesGenerationConflictError`` mapping, PART VI.2) for cross-doc parity.
    """

    def __init__(self, meeting_id: str, expected_generation: Any) -> None:
        super().__init__(
            f"notes for {meeting_id} changed since generation "
            f"{expected_generation} was read"
        )
        self.name = "NotesGenerationConflictError"
        self.meeting_id = meeting_id
        self.expected_generation = expected_generation


def _normalise_generation(generation: Any) -> int | str:
    """Return a generation as ``int``/``str`` — NEVER ``float`` (AC-STORE-11).

    GCS generations are uint64; a value ≥ 2^53 is silently corrupted by float
    rounding. This is the single choke point that enforces the "as-read (str/int),
    never a rounded float" rule: a float in is rejected (it is already lossy), an
    int/str passes through exactly. Used on every generation this module stores or
    passes back as ``if_generation_match``.
    """
    if isinstance(generation, bool):
        # bool is an int subclass — a precondition value is never a bool.
        raise TypeError("generation must be int or str, not bool")
    if isinstance(generation, float):
        raise TypeError(
            "GCS generation must never be a float — uint64 values >= 2^53 are "
            "corrupted by float rounding; keep them as int/str as read"
        )
    if isinstance(generation, int):
        return generation
    if isinstance(generation, str):
        return generation
    raise TypeError(f"generation must be int or str, got {type(generation).__name__}")


def write_finalized_notes(
    bucket: Any,
    meeting_id: str,
    markdown: str,
    *,
    if_generation_match: int | str | None = None,
) -> int:
    """Whole-object write of the finalized notes to GCS (§3.3, AC-STORE-10/11).

    ``if_generation_match`` semantics:
      * ``None`` — unconditional overwrite (AC-STORE-10-NEG: never raises on
        overwrite; GCS assigns a new generation).
      * ``0``    — create-only: succeeds only if the object does NOT yet exist;
        a second create-only write raises :class:`NotesGenerationConflictError`
        (GCS returns 412) — never a silent overwrite (AC-STORE-10).
      * ``<gen>``— require exactly that prior generation (optimistic concurrency).

    The precondition is passed through the generation-normaliser so a caller can
    NEVER smuggle a float precondition in (AC-STORE-11). Returns the NEW
    generation as an ``int`` (read back from the server, never a float).
    """
    match: int | str | None = (
        None if if_generation_match is None else _normalise_generation(if_generation_match)
    )
    from google.api_core.exceptions import PreconditionFailed  # lazy: SDK optional

    blob = bucket.blob(NOTES_OBJECT_TEMPLATE.format(meeting_id=meeting_id))
    try:
        blob.upload_from_string(
            markdown,
            content_type="text/markdown",
            if_generation_match=match,
        )
    except PreconditionFailed as exc:  # HTTP 412 — someone wrote since we read
        raise NotesGenerationConflictError(meeting_id, if_generation_match) from exc
    blob.reload()
    # blob.generation is a uint64 the SDK surfaces as int — normalise so a
    # float can never leak out of this seam.
    result = _normalise_generation(blob.generation)
    return int(result)


def list_notes_versions(bucket: Any, meeting_id: str) -> list[Any]:
    """History — GCS keeps every prior generation (§3.3, AC-STORE-12).

    Requires bucket-level Object Versioning ON (AC-STORE-09): with versioning off
    only the live generation survives an overwrite, so the list collapses to one.
    Returns the blobs newest-first (by ``updated``).
    """
    blobs = bucket.list_blobs(
        prefix=NOTES_OBJECT_TEMPLATE.format(meeting_id=meeting_id),
        versions=True,
    )
    return sorted(blobs, key=lambda b: b.updated, reverse=True)


def read_notes_version(bucket: Any, meeting_id: str, generation: int | str) -> str:
    """Restore/read any prior version by its exact generation (§3.3, AC-STORE-12).

    ``generation`` is normalised (never a float, AC-STORE-11) and passed to the
    blob handle so GCS returns that specific version's bytes — the first write's
    content for G1, the latest for G3.
    """
    gen = _normalise_generation(generation)
    blob = bucket.blob(
        NOTES_OBJECT_TEMPLATE.format(meeting_id=meeting_id),
        generation=int(gen),
    )
    text: str = blob.download_as_text()
    return text


def signed_url_for_grounding_image(bucket: Any, object_name: str) -> str:
    """Upload-path signed URL for a screenshared image handed to the Claude API.

    The image is a GCS blob (uploaded, NOT held inline / NOT base64, AC-STORE-14)
    and is passed to the Claude API by a **v4 signed URL** with a 10-minute expiry
    and GET method — never inline base64, which would bloat every cached prefix
    read (§3.3). The three params are module constants so a static audit can prove
    version=v4 / expiration=10min / method=GET (AC-STORE-14 / -14-NEG).
    """
    blob = bucket.blob(object_name)
    url: str = blob.generate_signed_url(
        version=SIGNED_URL_VERSION,
        expiration=SIGNED_URL_EXPIRATION,
        method=SIGNED_URL_METHOD,
    )
    return url
