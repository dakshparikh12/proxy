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
        # The wake-word / address token — every reactive ask is spoken TO Proxy
        # ("Proxy, where is url_for?"). "Proxy" is the product's own name, never a
        # code symbol in a tenant's repo, so it must be stripped before symbol
        # extraction — otherwise the CamelCase address token shadows the real
        # symbol and every grounded lookup misroutes to a search for "Proxy".
        "proxy",
    }
)

# Ask-shape → primary tool. Order matters: the first matching intent wins, so a
# "who writes X" ask routes to who_writes before the generic dependents fallback.
_INTENT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bwho\s+(?:writes|writer|mutat|updates?|inserts?)", "who_writes"),
    (r"\bwrites?\s+(?:to\s+)?(?:the\s+)?\w+\s+table", "who_writes"),
    (r"\b(?:shares?|sharing)\b.*\btable\b", "shares_table"),
    (r"\b(?:who|what|which)\b.*\b(?:also|else)\b.*\btable\b", "shares_table"),
    # A "where is X used/referenced" ask wants the CALL SITES → find_references.
    (r"\bwhere\b.*\b(?:used|referenced|calls?|called|imports?|imported)\b", "find_references"),
    (r"\b(?:reference|references|referenced)\b", "find_references"),
    # A locate-question — "where is X", "where is X defined", "where does X live",
    # "where's X", "where can I find X", "which file is X in" — wants the
    # DEFINITION of X (its defining node's file:line), NOT a caller. This MUST
    # precede the generic get_dependents fallback so a bare "where is url_for?"
    # returns the def in src/flask/helpers.py, never a random caller
    # (D01-DIRECT-ANSWER-WHERE-MISROUTE).
    (r"\bwhere(?:'s|\s+is|\s+are|\s+does|\s+can|\s+do)\b", "find_definition"),
    (r"\bwhere\b.*\b(?:defined|declared|lives?|located|find)\b", "find_definition"),
    (r"\b(?:defined|declaration|definition)\b", "find_definition"),
    (r"\bwhich\s+file\b", "find_definition"),
    (r"\b(?:depends?|dependent|dependents|calls?|callers?|uses?|imports?)\b", "get_dependents"),
    (r"\bentry\s*points?\b", "list_entry_points"),
    (r"\bowns?\b|\bowner\b", "owner"),
)

# A file-PATH ask ("who owns src/flask/app.py?") names a path, not a dotted
# symbol. ``owner`` resolves a repo-relative PATH, so the extractor must hand it
# the FULL path — splitting on '/' and passing the trailing ``app.py`` token made
# ``owner('app.py')`` miss (D01-OWNER-BLAME-UNKNOWN). These are the source/config
# extensions whose bare filename (no slash) is still unambiguously a file path.
_PATH_EXTENSIONS = (
    ".py", ".pyi", ".go", ".ts", ".tsx", ".js", ".jsx", ".rb", ".java", ".rs",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".kt", ".swift", ".php", ".scala",
    ".sql", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".json", ".md", ".rst",
    ".txt", ".sh", ".proto", ".tf",
)
# A path token: a slash-joined path, or a bare filename ending in a known
# extension. Anchored to path-ish characters (word chars, '/', '.', '-').
_PATH_TOKEN = re.compile(r"[\w./\-]*[\w\-](?:/[\w./\-]+|\.[A-Za-z][A-Za-z0-9]*)")

# Doc/changelog suffixes never carry a code DEFINITION — a find_references hit in
# one of these is a text mention (a changelog line, a doc paragraph), so it must
# never outrank a real source definition when answering a locate-question (Law 2).
_DOC_SUFFIXES = (".rst", ".md", ".txt", ".rst.txt", ".changes", ".changelog")


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


def _looks_like_path(token: str) -> bool:
    """True when ``token`` names a FILE PATH rather than a dotted code symbol.

    A path either contains a directory separator (``src/flask/app.py``) or is a
    bare filename ending in a known source/config extension (``app.py``). A bare
    dotted symbol (``flask.url_for``, ``Flask``) is NOT a path. This is what lets
    the owner route pass the FULL ``src/flask/app.py`` to ``owner()`` instead of a
    trailing ``app.py`` token (D01-OWNER-BLAME-UNKNOWN)."""
    if "/" in token:
        return True
    low = token.lower()
    return any(low.endswith(ext) for ext in _PATH_EXTENSIONS)


def _extract_symbol(ask: str) -> str | None:
    """Pull the most likely code symbol / table name out of a natural ask.

    A file-PATH ask ("who owns src/flask/app.py?") is recognised FIRST and the
    FULL path is returned — the owner tool resolves a repo-relative path, so
    splitting on '/' and passing the trailing token would miss. Otherwise prefers
    a back-ticked / quoted token (an explicit identifier), then a dotted.path or
    CamelCase / snake_case token, else the last content word.
    """
    # A back-ticked / quoted PATH keeps its slashes (an explicit file reference).
    mq = re.search(r"[`'\"]([\w./\-]+)[`'\"]", ask)
    if mq and _looks_like_path(mq.group(1)):
        return mq.group(1)
    # A bare (unquoted) file path anywhere in the ask — pick the longest.
    path_hits: list[str] = [t for t in _PATH_TOKEN.findall(ask) if _looks_like_path(t)]
    if path_hits:
        return max(path_hits, key=len)
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
    if items is None:
        # shares_table exposes its file:line leads as ``touchers`` (co-accessor
        # functions), so a "who shares this table" ask mints a real citation.
        items = getattr(result, "touchers", None)
    if not items:
        return None
    top = items[0]
    file = getattr(top, "file", None) or getattr(top, "path", None)
    line = getattr(top, "line", None)
    conf = getattr(top, "confidence", "resolved")
    if file is None or line is None:
        return None
    return str(file), int(line), str(conf)


def _is_doc(path: str) -> bool:
    return path.lower().endswith(_DOC_SUFFIXES)


def _def_is_indented(handle: Any, file: str, line: int) -> bool:
    """True when the declaration at ``file:line`` is INDENTED (a method / nested
    def) rather than a module-level definition (column 0). Read from the real
    clone (Law 1) — a module-level ``def url_for`` outranks the indented method
    ``Flask.url_for`` of the same name when answering "where is url_for?". A read
    that cannot confirm the line is treated as module-level (indented=False) so a
    read miss never demotes an otherwise-canonical definition."""
    result = _call(handle, "batch_read", paths=[file], max_lines_per_file=None)
    for bf in getattr(result, "files", None) or []:
        content = getattr(bf, "content", None)
        if getattr(bf, "error", None) is not None or content is None:
            continue
        lines = content.splitlines()
        if 1 <= line <= len(lines):
            raw = str(lines[line - 1])
            return bool(raw[:1].isspace())
    return False


def _find_definition(handle: Any, symbol: str | None) -> tuple[str, int, str] | None:
    """Resolve the DEFINITION of ``symbol`` — its defining node's file:line.

    A locate-question ("where is X?") wants where X *lives*, not who calls it. We
    resolve it over the SAME pinned graph the other tools read (no parallel index):

      1. ``resolve_symbol(symbol)`` → the declaration node(s) the builder stamped
         (id ``<file>::<name>``, ``line`` = the ``def``/``class`` line). Prefer a
         real definition kind (function/class/table) in a NON-doc source file,
         ranked by pagerank then id — so the canonical public ``url_for`` in
         ``src/flask/helpers.py`` outranks a same-named method and never a caller.
      2. If the graph has no matching declaration node (e.g. a C-extension symbol,
         or a graph not yet built), fall back to ``find_references`` but rank a
         SOURCE hit ABOVE any ``.rst``/``.md``/changelog text mention (Law 2) so a
         "where defined" answer never cites a changelog line.

    Returns ``(file, line, confidence)`` or ``None`` (honest abstention, Law 1).
    """
    if not symbol:
        return None
    server = _server_of(handle)
    graph = getattr(server, "graph", None)
    if graph is not None and hasattr(graph, "resolve_symbol"):
        nodes = list(graph.resolve_symbol(symbol))
        # A definition is a declaration node — a function/class/table/module —
        # never an edge. Prefer a real symbol decl in a non-doc source file.
        decls = [
            n for n in nodes
            if getattr(n, "kind", "") in ("function", "class", "table")
            and not _is_doc(getattr(n, "path", ""))
        ]
        if decls:
            # Rank the candidate definitions deterministically (Law 4: physics):
            #   1. a MODULE-LEVEL definition (the ``def``/``class`` line starts at
            #      column 0) outranks a METHOD of the same name (an indented ``def``
            #      nested in a class). The graph stamps both as ``<file>::<name>``
            #      with kind "function", so a "where is url_for?" would otherwise
            #      pick the higher-pagerank ``Flask.url_for`` METHOD over the
            #      canonical module-level ``url_for`` in helpers.py — the wrong
            #      "definition". We read the real clone to see the indentation.
            #   2. then highest pagerank (the most-referenced def of that name);
            #   3. then node id, for a fully deterministic tie-break.
            def _rank(n: Any) -> tuple[int, float, str]:
                indented = _def_is_indented(handle, str(n.path), int(n.line))
                return (1 if indented else 0, -getattr(n, "pagerank", 0.0), n.id)

            decls.sort(key=_rank)
            top = decls[0]
            # A single unambiguous declaration → resolved; several same-named
            # declarations → lower-bound (we picked the top-ranked one).
            conf = "resolved" if len(decls) == 1 else "lower-bound"
            return str(top.path), int(top.line), conf

    # Graph miss → grep fallback, but rank SOURCE above doc/changelog text.
    refs_result = _call(handle, "find_references", symbol=symbol)
    items = list(getattr(refs_result, "results", None) or [])
    if not items:
        return None
    source = [r for r in items if not _is_doc(getattr(r, "file", ""))]
    ranked = source or items  # only fall back to a doc hit if there is no source hit
    top = ranked[0]
    file = getattr(top, "file", None)
    line = getattr(top, "line", None)
    if file is None or line is None:
        return None
    # A grep-derived location is never overstated as resolved (Law 2).
    return str(file), int(line), "lower-bound"


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

    # An OWNERSHIP ask ("who owns src/flask/app.py?") names a PERSON, not a code
    # definition line — its answer is the CODEOWNERS match ('resolved') or the top
    # recent git authors of the path ('lower-bound'), cited to the path itself, not
    # a file:line drawn from a read (D01-OWNER-BLAME-UNKNOWN).
    if tool == "owner":
        return _answer_owner(handle, symbol, ask)

    # A locate-question resolves to the DEFINITION over the pinned graph nodes
    # (D01-DIRECT-ANSWER-WHERE-MISROUTE) — not a caller and not a changelog line.
    if tool == "find_definition":
        hit = _find_definition(handle, symbol)
    else:
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
    # find_definition tiers off its OWN confidence (it already resolved a single
    # unambiguous declaration node when 'resolved'), so it does not additionally
    # require the global single-referent probe — a symbol with one def is resolved
    # even though lookup_referent's single-match global gate is a stricter probe.
    if tool == "find_definition":
        confidence = "resolved" if (confirmed and tool_conf == "resolved") else "lower-bound"
    elif confirmed and tool_conf == "resolved" and referent is not None:
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


def _answer_owner(handle: Any, symbol: str | None, ask: str) -> DirectAnswer:
    """Resolve an ownership ask to a grounded owner naming a real person.

    The ``owner`` tool returns an :class:`~code_intel.results.OwnerResult` (a PERSON
    + the source it came from), not a file:line code hit. A CODEOWNERS match is
    'resolved' and cites ``CODEOWNERS``; the git-blame fallback names the top recent
    authors of the path tagged 'lower-bound' and cites the path (Law 2). We abstain
    honestly (Law 1) only when git resolves no author at all ('(unknown)')."""
    if not symbol:
        return DirectAnswer(
            text="Not found by this method (owner: no path named in the ask).",
            citation=None,
            confidence="not-found",
            tool="owner",
        )
    result = _call(handle, "owner", path=symbol)
    owner_name = getattr(result, "owner", None)
    if result is None or not owner_name or owner_name == "(unknown)":
        return DirectAnswer(
            text=f"Not found by this method (owner for '{symbol}').",
            citation=None,
            confidence="not-found",
            tool="owner",
        )
    confidence = getattr(result, "confidence", "lower-bound")
    src_file = getattr(result, "file", None) or symbol
    line = getattr(result, "line", None)
    citation = f"{src_file}:{line}" if line is not None else str(src_file)
    tier = "CODEOWNERS" if confidence == "resolved" else "recent git authors"
    text = (
        f"Owner of '{symbol}' via {tier}: {owner_name} [{confidence}] "
        f"— cited from {citation}"
    )
    return DirectAnswer(
        text=text,
        citation=citation,
        confidence=confidence,
        tool="owner",
        read_confirmed=False,
    )
