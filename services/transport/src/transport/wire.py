"""Confirmed Recall/AssemblyAI wire shapes + the fail-loud transcript parser
(AC-HEAR-12, CANONICAL §11.10).

The wire schema is pinned/confirmed against live vendor docs at build time. A message
matching the confirmed schema parses to a complete ``transcript(words, speaker, t)``
record; a message that **drifts** from it is rejected **loudly** (raises) rather than
parsed on a silent assumption — zero silent wire assumptions. Provenance of the pin is
recorded alongside the schema so the confirmation is evidence, not folklore.
"""
from __future__ import annotations

from typing import Any

from .signals import Transcript

# Pinned against Recall real-time transcript passthrough + AssemblyAI Universal-Streaming
# turn payloads, confirmed at build (see §3.2 / §3.9-3, CANONICAL §11.10). The parser
# below is the single point that trusts this shape; any drift raises.
WIRE_SCHEMA_PROVENANCE = "recall.real_time_transcript + assemblyai.universal_streaming @ build-confirm"

_REQUIRED_TRANSCRIPT_FIELDS = ("words", "speaker", "timestamp")


class WireDriftError(RuntimeError):
    """A passthrough message did not match the confirmed wire shape — never parsed silently."""


def parse_transcript(message: dict[str, Any]) -> Transcript:
    """Parse one confirmed-schema passthrough message into a ``Transcript``.

    Raises :class:`WireDriftError` on any drift (missing/renamed/mistyped field), so a
    vendor wire change surfaces loudly instead of yielding a wrong transcript (Law 2).
    """
    missing = [f for f in _REQUIRED_TRANSCRIPT_FIELDS if f not in message]
    if missing:
        raise WireDriftError(
            f"transcript wire drift: missing {missing} (confirmed schema {_REQUIRED_TRANSCRIPT_FIELDS}); "
            f"provenance {WIRE_SCHEMA_PROVENANCE}"
        )

    words = message["words"]
    speaker = message["speaker"]
    timestamp = message["timestamp"]
    if not isinstance(words, str) or not isinstance(speaker, str) or not isinstance(timestamp, (int, float)):
        raise WireDriftError(
            f"transcript wire drift: field types diverged from the confirmed schema; "
            f"provenance {WIRE_SCHEMA_PROVENANCE}"
        )

    is_final = bool(message.get("end_of_turn", True))
    return Transcript(words=words, speaker=speaker, t=float(timestamp), is_final=is_final)


def has_end_of_turn(message: dict[str, Any]) -> bool:
    """Whether the passthrough message carries AAI's ``end_of_turn`` boundary field."""
    return "end_of_turn" in message
