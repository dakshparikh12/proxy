"""The ONE canonical wake-turn direct-answer resolver (Doc 00 · AC-HOST-007; Law 1/2).

This is the single canonical home for ``answer_direct`` (G4-DUPLICATE-ANSWER-DIRECT-
ENTRYPOINTS). It lives in the ``code_intel`` layer because it composes THIS
service's own structural tools and imports nothing from any upper layer — the
harness (Doc 03) re-exports it *downward* (``harness.direct_answer``), never the
reverse. There is no parallel stub and no second copy of the resolver logic.

A reactive transcript ask ("who writes the users table?", "what depends on
``Flask``?", "where is ``dispatch_request`` referenced?") is answered *directly*
— no E2B sandbox, no Workroom session (those are reserved for asked WORK, Doc 05).
This module is the seam the product's whole reason-to-exist rests on: it composes
the REAL ``code_intel`` structural tools (the ones already built on
:class:`~code_intel.mcp_server.CodeIntelMCPServer`) into a grounded reply that
cites a real ``file:line`` drawn from an actual file READ at the meeting's pinned
SHA.

Grounding discipline (the five standing laws, as code):

  * **Law 1 (grounded or silent)** — the citation is produced ONLY after a
    ``batch_read`` of the candidate file at the pinned SHA confirms the line
    exists in the clone. If nothing resolves, we say so ("not found by this
    method") rather than inventing a location. The citation is drawn from the
    READ, never from a graph edge.
  * **Law 2 (never overstate)** — a citation confirmed by a single unambiguous
    referent + a successful read is tagged ``resolved``; a search-/grep-derived
    or multi-candidate answer is tagged ``lower-bound``.
  * **Law 3 (human control)** — the direct answer is read-only; it stages
    nothing and touches no world-facing seam.
  * **Law 4 (dynamic)** — the routing here is deterministic *physics* (which
    tool a shaped ask maps to); the tools themselves own the judgement. No
    situation-specific answer is hard-coded.

The public entrypoint keeps the AC-HOST-007 call shape
(``ask, tenant, sha, e2b, workroom``) so existing callers do not break, and
additionally accepts a live ``session`` (:class:`code_intel.meeting.MeetingSession`,
the SHA-pinned per-meeting handle) or a raw ``code_intel`` server. When a live
handle is supplied the answer is real; the ``e2b`` / ``workroom`` seams are
accepted only so a caller can PROVE the direct path invokes neither.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Words that never name a code symbol/table — stripped before symbol extraction.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "what", "who", "which", "where", "when", "how", "why",
        "does", "do", "did", "is", "are", "was", "were", "will", "would", "can",
        "could", "should", "this", "that", "these", "those", "of", "to", "in",
        "on", "for", "and", "or", "with", "by", "from", "into", "at", "as",
        "function", "method", "class", "module", "table", "field", "column",
        "return", "returns", "returned", "call", "calls", "called", "use",
        "uses", "used", "using", "depend", "depends", "dependent", "dependents",
        "reference", "references", "referenced", "write", "writes", "writer",
        "writers", "read", "reads", "share", "shares", "sharing", "entry",
        "point", "points", "owner", "owns", "own", "it", "its", "me", "please",
        "tell", "show", "find", "get", "give", "about", "here", "there", "we",
        "you", "i", "our", "your", "my",
    }
)

# Ask-shape → primary tool. Order matters: the first matching intent wins, so a
# "who writes X" ask routes to who_writes before the generic dependents fallback.
_INTENT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bwho\s+(?:writes|writer|mutat|updates?|inserts?)", "who_writes"),
    (r"\bwrites?\s+(?:to\s+)?(?:the\s+)?\w+\s+table", "who_writes"),
    (r"\b(?:shares?|sharing)\b.*\btable\b", "shares_table"),
    (r"\b(?:who|what|which)\b.*\b(?:also|else)\b.*\btable\b", "shares_table"),
    (r"\b(?:depends?|dependent|dependents|calls?|callers?|uses?|imports?)\b", "get_dependents"),
    (r"\b(?:reference|references|referenced|where.*(?:used|defined|referenced))\b", "find_references"),
    (r"\bentry\s*points?\b", "list_entry_points"),
    (r"\bowns?\b|\bowner\b", "owner"),
)


@dataclass(frozen=True)
class DirectAnswer:
    """A grounded direct answer with a real ``file:line`` citation from a read."""

    text: str
    citation: str | None
    confidence: str  # "resolved" | "lower-bound" | "not-found"
    tool: str
    provisioned_e2b: bool = False
    dispatched_workroom: bool = False
    read_confirmed: bool = False
    candidates: list[str] = field(default_factory=list)

    # Back-compat alias for the older DirectAnswer.grounded_citation attribute
    @property
    def grounded_citation(self) -> str | None:
        return self.citation

    # A dict view keeps parity with the code_intel.direct.answer_direct façade
    # return shape (path/tenant/sha/ask/answer/provisioned_e2b/dispatched_workroom).
    def as_dict(self, *, tenant: str, sha: str, ask: str) -> dict[str, Any]:
        return {
            "path": "direct",
            "tenant": tenant,
            "sha": sha,
            "ask": ask,
            "answer": self.text,
            "citation": self.citation,
            "confidence": self.confidence,
            "tool": self.tool,
            "provisioned_e2b": self.provisioned_e2b,
            "dispatched_workroom": self.dispatched_workroom,
        }


def _extract_symbol(ask: str) -> str | None:
    """Pull the most likely code symbol / table name out of a natural ask.

    Prefers a back-ticked / quoted token (an explicit identifier), then a
    dotted.path or CamelCase / snake_case token, else the last content word.
    """
    m = re.search(r"[`'\"]([A-Za-z_][\w.]*)[`'\"]", ask)
    if m:
        return m.group(1)
    # dotted paths and identifiers, longest first
    idents: list[str] = re.findall(r"[A-Za-z_][\w.]*", ask)
    content: list[str] = [w for w in idents if w.lower() not in _STOPWORDS]
    if not content:
        return None
    # Prefer a CamelCase / dotted / snake_case token (looks like an identifier).
    for w in content:
        if "." in w or "_" in w or (w[:1].isupper() and w != w.upper()) or any(c.isupper() for c in w[1:]):
            return w
    return content[-1]


def _classify(ask: str) -> str:
    low = ask.lower()
    for pattern, tool in _INTENT_PATTERNS:
        if re.search(pattern, low):
            return tool
    return "get_dependents"


def _server_of(handle: Any) -> Any:
    """The underlying CodeIntelMCPServer behind a MeetingSession or raw server."""
    return getattr(handle, "_server", None) or handle


def _call(handle: Any, tool: str, **args: Any) -> Any:
    """Invoke a structural tool on a MeetingSession (``tool_call``) or raw server.

    The MeetingSession path is the pinned-SHA product path; a raw server is the
    same tools without meeting pinning (used only when no session exists). Tools
    whose args are unhashable (``batch_read`` takes a list of paths) bypass the
    session's tuple-keyed cache and hit the server directly — a file read is not
    a cacheable structural query anyway.
    """
    if tool == "batch_read":
        server = _server_of(handle)
        return server.batch_read(**args)
    if hasattr(handle, "tool_call"):
        return handle.tool_call(tool, **args)
    fn = getattr(handle, tool, None)
    if fn is None:
        return None
    return fn(**args)


def _clone_path(handle: Any) -> Path | None:
    server = getattr(handle, "_server", None) or handle
    cp = getattr(server, "clone_path", None)
    return Path(cp) if cp is not None else None


def _confirm_read(handle: Any, file: str, line: int) -> tuple[bool, str]:
    """READ the candidate file at the pinned SHA and confirm ``line`` exists.

    Returns ``(confirmed, snippet)``. This is what makes the citation grounded
    in an actual file rather than a graph edge (Law 1): the answer only cites a
    ``file:line`` we have just read out of the clone.
    """
    result = _call(handle, "batch_read", paths=[file], max_lines_per_file=None)
    files = getattr(result, "files", None) or []
    for bf in files:
        content = getattr(bf, "content", None)
        if getattr(bf, "error", None) is not None or content is None:
            continue
        lines = content.splitlines()
        if 1 <= line <= len(lines):
            return True, lines[line - 1].strip()
    return False, ""


def _first_hit(result: Any) -> tuple[str, int, str] | None:
    """Extract (file, line, confidence) from any tool result shape."""
    items = getattr(result, "results", None)
    if items is None:
        items = getattr(result, "writers", None)
    if not items:
        return None
    top = items[0]
    file = getattr(top, "file", None) or getattr(top, "path", None)
    line = getattr(top, "line", None)
    conf = getattr(top, "confidence", "resolved")
    if file is None or line is None:
        return None
    return str(file), int(line), str(conf)


def answer_direct(
    *,
    ask: str,
    tenant: str = "",
    sha: str = "",
    e2b: object | None = None,
    workroom: object | None = None,
    session: Any = None,
    code_intel: Any = None,
) -> DirectAnswer:
    """Resolve a reactive transcript ask into a grounded direct answer.

    Routing (deterministic physics; the model/tools own judgement):

      1. classify the ask → primary tool (who_writes / shares_table /
         get_dependents / find_references / list_entry_points / owner);
      2. ``lookup_referent`` the extracted symbol to detect ambiguity;
      3. run the primary tool against the pinned graph/clone;
      4. ``batch_read`` the top hit's file at the pinned SHA to CONFIRM the
         ``file:line`` — the citation is drawn from that read, never the edge;
      5. tag ``resolved`` (single referent + confirmed read) or ``lower-bound``
         (search-derived / ambiguous), or ``not-found`` (Law 1 abstention).

    ``e2b`` / ``workroom`` are NEVER called — they are accepted only so a caller
    can prove the direct path provisions no sandbox and dispatches no session.
    """
    handle = session if session is not None else code_intel
    if handle is None:
        # No live code_intel handle: honest abstention, not a fabricated citation.
        return DirectAnswer(
            text=f"Not found by this method (no code_intel index bound): {ask}",
            citation=None,
            confidence="not-found",
            tool="none",
        )

    tool = _classify(ask)
    symbol = _extract_symbol(ask)

    # Ambiguity probe: a single unambiguous referent is a precondition for
    # 'resolved' honesty tiering (Law 2). Multiple / zero → cap at lower-bound.
    referent = None
    if symbol is not None:
        referent = _call(handle, "lookup_referent", symbol=symbol)

    # Run the primary tool. Tables/paths take the raw token; symbol tools too.
    result = _run_tool(handle, tool, symbol)
    hit = _first_hit(result) if result is not None else None

    if hit is None:
        # Grounded silence — Law 1: we do not invent a location.
        return DirectAnswer(
            text=(
                f"Not found by this method ({tool}"
                + (f" for '{symbol}'" if symbol else "")
                + ")."
            ),
            citation=None,
            confidence="not-found",
            tool=tool,
        )

    file, line, tool_conf = hit
    confirmed, snippet = _confirm_read(handle, file, line)

    # Honesty tiering (Law 2): a citation is 'resolved' only when a single
    # unambiguous referent was found AND the read confirmed the line AND the
    # tool itself reported 'resolved'. Anything search-derived is 'lower-bound'.
    if confirmed and tool_conf == "resolved" and referent is not None:
        confidence = "resolved"
    else:
        confidence = "lower-bound"

    citation = f"{file}:{line}"
    subject = f"'{symbol}'" if symbol else "the ask"
    if confirmed:
        text = (
            f"Grounded answer for {subject} via {tool}: {citation}"
            f" [{confidence}] — {snippet}"
        )
    else:
        # The tool named a location the read could not confirm — degrade to a
        # lower-bound and say the read did not confirm the line (never overstate).
        confidence = "lower-bound"
        text = (
            f"Grounded answer for {subject} via {tool}: {citation}"
            f" [{confidence}] (read did not confirm the exact line)"
        )

    return DirectAnswer(
        text=text,
        citation=citation,
        confidence=confidence,
        tool=tool,
        read_confirmed=confirmed,
        candidates=[c for c in ([str(referent)] if referent else []) if c],
    )


def _run_tool(handle: Any, tool: str, symbol: str | None) -> Any:
    """Dispatch the classified primary tool with the right argument name."""
    try:
        if tool == "who_writes":
            return _call(handle, "who_writes", table=symbol or "")
        if tool == "shares_table":
            return _call(handle, "shares_table", table=symbol or "")
        if tool == "list_entry_points":
            return _call(handle, "list_entry_points")
        if tool == "owner":
            return _call(handle, "owner", path=symbol or "")
        if tool == "find_references":
            return _call(handle, "find_references", symbol=symbol or "")
        # default: get_dependents
        return _call(handle, "get_dependents", symbol=symbol or "")
    except Exception:
        return None
