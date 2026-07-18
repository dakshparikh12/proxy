"""read_and_redact — the read-path secret redaction seam (AC-INV-008).

A file read on the code-intel path passes through here first: any detected secret
is replaced by a redaction marker BEFORE the content reaches the index, the model
context, the sandbox, or any log. The secret value is never logged at any level —
only the path and a redaction count are ever emitted. This closes the read-path
leak where a checked-in credential would otherwise flow into a prompt or a log
line.
"""
from __future__ import annotations

import logging
import re

_LOG = logging.getLogger("libs.ops.redaction")

_REDACTION_MARKER = "[secret REDACTED]"

# Assignments whose right-hand side is a credential value. The key half names a
# secret-ish field (secret / key / token / password / credential / api_key); the
# value half is the token we must never surface.
_ASSIGNMENT_RX = re.compile(
    r"(?P<lead>(?:[A-Za-z0-9_.\-]*"
    r"(?:secret|passwd|password|pwd|token|api[_-]?key|access[_-]?key|credential)"
    r"[A-Za-z0-9_.\-]*)\s*[:=]\s*)"
    r"(?P<val>[\"']?[^\s\"']+[\"']?)",
    re.IGNORECASE,
)

# Standalone high-signal credential shapes (AWS-style access keys, long opaque
# tokens) that can appear without a labelled assignment.
_TOKEN_RX = re.compile(r"\b(?:AKIA|ASIA)[A-Za-z0-9_]{8,}\b")


def read_and_redact(*, content: str, path: str) -> str:
    """Return ``content`` with every detected secret replaced by a marker.

    The returned text is what may be indexed / fed to the model / written to a
    sandbox. The raw secret is never logged; only ``path`` and the redaction count
    are emitted.
    """
    redactions = 0

    def _mask_assignment(match: re.Match[str]) -> str:
        nonlocal redactions
        redactions += 1
        return f"{match.group('lead')}{_REDACTION_MARKER}"

    def _mask_token(match: re.Match[str]) -> str:
        nonlocal redactions
        redactions += 1
        return _REDACTION_MARKER

    redacted = _ASSIGNMENT_RX.sub(_mask_assignment, content)
    redacted = _TOKEN_RX.sub(_mask_token, redacted)

    # Log ONLY the path + count — never the content, never the secret.
    _LOG.debug("read_path_redaction path=%s redactions=%d", path, redactions)

    return redacted
