"""Multi-language tag extraction via tree-sitter (§2.2/§3.4).

The dependency graph's nodes and edges are extracted mechanically — no LLM — from
each language's tree-sitter grammar using aider-style *tag queries*: definitions
become nodes (function/method/class/module), call/reference sites and imports
become edges (``calls`` / ``imports``). Python stays on stdlib ``ast`` (exact,
already verified); every other supported grammar flows through here. A grammar we
do not cover, or a per-pattern query error, degrades gracefully (fewer tags, never
a crash) — the file is still coverage-flagged and ripgrep-searchable (§3.4).
"""
from __future__ import annotations

import logging
from typing import Any

from .graph import Edge, Node

logger = logging.getLogger("code_intel.langs")

# suffix -> tree-sitter grammar name (Python handled separately by the ast path)
LANG_BY_SUFFIX: dict[str, str] = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".cs": "csharp",
    ".php": "php",
}

# Per-language capture patterns. Each entry: (capture_kind, pattern). capture_kind
# is 'def' (a declaration -> node), 'call' (a reference -> calls edge), or 'imp'
# (an import -> imports edge). Node kind is inferred from the pattern label suffix.
_PATTERNS: dict[str, list[tuple[str, str, str]]] = {
    "javascript": [
        ("def", "function", "(function_declaration name:(identifier)@x)"),
        ("def", "function", "(generator_function_declaration name:(identifier)@x)"),
        ("def", "class", "(class_declaration name:(identifier)@x)"),
        ("def", "method", "(method_definition name:(property_identifier)@x)"),
        ("def", "function", "(variable_declarator name:(identifier)@x value:(arrow_function))"),
        ("def", "function", "(variable_declarator name:(identifier)@x value:(function_expression))"),
        ("call", "", "(call_expression function:(identifier)@x)"),
        ("call", "", "(call_expression function:(member_expression property:(property_identifier)@x))"),
        ("imp", "", "(import_statement source:(string (string_fragment)@x))"),
    ],
    "typescript": [
        ("def", "function", "(function_declaration name:(identifier)@x)"),
        ("def", "class", "(class_declaration name:(type_identifier)@x)"),
        ("def", "interface", "(interface_declaration name:(type_identifier)@x)"),
        ("def", "method", "(method_definition name:(property_identifier)@x)"),
        ("def", "function", "(variable_declarator name:(identifier)@x value:(arrow_function))"),
        ("call", "", "(call_expression function:(identifier)@x)"),
        ("call", "", "(call_expression function:(member_expression property:(property_identifier)@x))"),
        ("imp", "", "(import_statement source:(string (string_fragment)@x))"),
    ],
    "tsx": [
        ("def", "function", "(function_declaration name:(identifier)@x)"),
        ("def", "class", "(class_declaration name:(type_identifier)@x)"),
        ("def", "interface", "(interface_declaration name:(type_identifier)@x)"),
        ("def", "method", "(method_definition name:(property_identifier)@x)"),
        ("call", "", "(call_expression function:(identifier)@x)"),
        ("imp", "", "(import_statement source:(string (string_fragment)@x))"),
    ],
    "go": [
        ("def", "function", "(function_declaration name:(identifier)@x)"),
        ("def", "method", "(method_declaration name:(field_identifier)@x)"),
        ("def", "class", "(type_declaration (type_spec name:(type_identifier)@x))"),
        ("call", "", "(call_expression function:(identifier)@x)"),
        ("call", "", "(call_expression function:(selector_expression field:(field_identifier)@x))"),
        ("imp", "", "(import_spec path:(interpreted_string_literal)@x)"),
    ],
    "java": [
        ("def", "method", "(method_declaration name:(identifier)@x)"),
        ("def", "class", "(class_declaration name:(identifier)@x)"),
        ("def", "interface", "(interface_declaration name:(identifier)@x)"),
        ("def", "method", "(constructor_declaration name:(identifier)@x)"),
        ("call", "", "(method_invocation name:(identifier)@x)"),
        ("imp", "", "(import_declaration (scoped_identifier)@x)"),
    ],
    "ruby": [
        ("def", "method", "(method name:(identifier)@x)"),
        ("def", "method", "(singleton_method name:(identifier)@x)"),
        ("def", "class", "(class name:(constant)@x)"),
        ("def", "class", "(module name:(constant)@x)"),
        ("call", "", "(call method:(identifier)@x)"),
    ],
    "rust": [
        ("def", "function", "(function_item name:(identifier)@x)"),
        ("def", "class", "(struct_item name:(type_identifier)@x)"),
        ("def", "class", "(enum_item name:(type_identifier)@x)"),
        ("def", "interface", "(trait_item name:(type_identifier)@x)"),
        ("call", "", "(call_expression function:(identifier)@x)"),
        ("call", "", "(macro_invocation macro:(identifier)@x)"),
    ],
    "c": [
        ("def", "function", "(function_definition declarator:(function_declarator declarator:(identifier)@x))"),
        ("call", "", "(call_expression function:(identifier)@x)"),
        ("imp", "", "(preproc_include path:(string_literal)@x)"),
        ("imp", "", "(preproc_include path:(system_lib_string)@x)"),
    ],
    "cpp": [
        ("def", "function", "(function_definition declarator:(function_declarator declarator:(identifier)@x))"),
        ("def", "class", "(class_specifier name:(type_identifier)@x)"),
        ("call", "", "(call_expression function:(identifier)@x)"),
        ("imp", "", "(preproc_include path:(string_literal)@x)"),
    ],
    "csharp": [
        ("def", "method", "(method_declaration name:(identifier)@x)"),
        ("def", "class", "(class_declaration name:(identifier)@x)"),
        ("def", "interface", "(interface_declaration name:(identifier)@x)"),
        ("call", "", "(invocation_expression function:(identifier)@x)"),
        ("imp", "", "(using_directive (qualified_name)@x)"),
    ],
    "php": [
        ("def", "function", "(function_definition name:(name)@x)"),
        ("def", "method", "(method_declaration name:(name)@x)"),
        ("def", "class", "(class_declaration name:(name)@x)"),
        ("call", "", "(function_call_expression function:(name)@x)"),
    ],
}

# Compiled (Query, kind, node_kind) triples per language — built lazily, skipping
# any pattern the installed grammar rejects (graceful degradation, never a crash).
_COMPILED: dict[str, list[tuple[Any, str, str]]] = {}


def _compiled(lang: str) -> list[tuple[Any, str, str]]:
    if lang in _COMPILED:
        return _COMPILED[lang]
    from tree_sitter import Query
    from tree_sitter_language_pack import get_language

    out: list[tuple[Any, str, str]] = []
    try:
        language = get_language(lang)  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover - grammar missing
        logger.warning("no grammar for %s: %s", lang, exc)
        _COMPILED[lang] = out
        return out
    for kind, node_kind, pattern in _PATTERNS.get(lang, []):
        try:
            out.append((Query(language, pattern), kind, node_kind))
        except Exception as exc:  # a pattern the grammar version rejects
            logger.warning("skip %s pattern (%s): %s", lang, node_kind or kind, exc)
    _COMPILED[lang] = out
    return out


def _module_id(rel: str) -> str:
    """A stable pseudo-module id for a non-Python file (path → dotted, no suffix)."""
    stem = rel.rsplit(".", 1)[0]
    return stem.replace("/", ".").replace("\\", ".")


def supported(suffix: str) -> bool:
    return suffix.lower() in LANG_BY_SUFFIX


def extract(rel: str, source: bytes, lang: str) -> tuple[list[Node], list[Edge]] | None:
    """Extract (nodes, edges) for one non-Python source file, or None on parse failure.

    Definitions → ``{rel}::{name}`` nodes; call sites → ``calls`` edges (target is a
    bare name, resolved to a def id by the graph assembler); imports → ``imports``
    edges into the imported module id. A ``module`` node is always emitted so the
    file participates in import blast-radius.
    """
    from tree_sitter import QueryCursor
    from tree_sitter_language_pack import get_parser

    try:
        tree = get_parser(lang).parse(source)  # type: ignore[arg-type]
    except Exception:  # pragma: no cover - parser missing
        return None

    module_id = _module_id(rel)
    nodes: list[Node] = [Node(id=module_id, path=rel, line=1, kind="module")]
    edges: list[Edge] = []
    seen_defs: set[str] = set()

    def _text(node: Any) -> str:
        return (node.text or b"").decode("utf-8", "replace")

    # Definition nodes first, so call edges can be scoped to their enclosing def.
    def_spans: list[tuple[int, int, str]] = []  # (start_byte, end_byte, def_id)
    for query, kind, node_kind in _compiled(lang):
        if kind != "def":
            continue
        for cap_nodes in QueryCursor(query).captures(tree.root_node).values():
            for n in cap_nodes:
                node_id = f"{rel}::{_text(n)}"
                if node_id not in seen_defs:
                    seen_defs.add(node_id)
                    nodes.append(Node(id=node_id, path=rel, line=n.start_point[0] + 1, kind=node_kind))
                def_spans.append((n.start_byte, n.parent.end_byte if n.parent else n.end_byte, node_id))

    def _enclosing(byte: int) -> str:
        best: tuple[int, str] | None = None
        for start, end, did in def_spans:
            if start <= byte < end and (best is None or (end - start) < best[0]):
                best = (end - start, did)
        return best[1] if best else module_id

    for query, kind, _node_kind in _compiled(lang):
        if kind == "call":
            for cap_nodes in QueryCursor(query).captures(tree.root_node).values():
                for n in cap_nodes:
                    edges.append(Edge(source=_enclosing(n.start_byte), target=_text(n), kind="calls"))
        elif kind == "imp":
            for cap_nodes in QueryCursor(query).captures(tree.root_node).values():
                for n in cap_nodes:
                    target = _text(n).strip("\"'`<>").replace("/", ".").lstrip(".")
                    if target:
                        edges.append(Edge(source=module_id, target=target, kind="imports"))
    return nodes, edges
