"""A compact, GROUNDABLE symbol map of a cloned repo — the resident understanding artifact.

This REPLACES the LLM-authored prose map (:mod:`premeeting.map_build`), whose text carried NO
real ``file:line`` and so could not ground a citation (Law 1: grounded or silent). This module
builds the map **deterministically — no LLM, so no hallucination** — using the proven Aider
``repomap.py`` approach:

1. **Extract** every definition + reference with tree-sitter tag queries (the vendored
   ``queries/tree-sitter-language-pack/*-tags.scm`` set, via ``grep-ast`` +
   ``tree-sitter-language-pack``). Each tag is a :class:`Tag`
   ``(rel_fname, fname, line, name, kind)`` with ``kind ∈ {"def", "ref"}``.
2. **Rank** with a graph: a :class:`networkx.MultiDiGraph` whose nodes are files and whose edges
   run referencer→definer per shared identifier (weight ``~ sqrt(#refs)``, boosted for long /
   interesting identifiers, downweighted for private ``_``-prefixed + >5-definer common names),
   scored by **personalized PageRank**.
3. **Render** the top-ranked symbols as per-file signature snippets carrying real ``file:line``
   (file header + the definition lines, bodies elided — Aider's ``to_tree`` format via
   ``grep-ast``'s ``TreeContext``). A **binary search** over the number of included tags fits a
   token budget (~11k) so the artifact stays resident in a warm session's cached context.

Plus a short **architecture header** (a few hundred tokens): the top-level dir map + the
highest-PageRank entry points, each with real ``file:line`` — deterministic from the same graph.

Not wired into the meeting/provision path here; this is the generator + its evidence only.
"""
from __future__ import annotations

import math
import warnings
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import networkx as nx
from grep_ast import TreeContext, filename_to_lang
from grep_ast.tsl import get_language, get_parser

from .exclusions import ExclusionManager

# The vendored tree-sitter tag queries (Aider's own set, tree-sitter-language-pack flavour).
# Each ``<lang>-tags.scm`` names the ``@name.definition.*`` / ``@name.reference.*`` captures the
# extractor keys on. Bundled in-package so this runs without depending on the aider distribution.
_QUERY_DIR = Path(__file__).parent / "queries" / "tree-sitter-language-pack"

# A source file bigger than this is skipped for tag extraction — a vendored blob / generated
# bundle is not signal and would only cost parse time (a physics floor, Law 4).
_MAX_FILE_BYTES = 1_000_000

# Aider's token estimate: ~1 token per ~4 chars. Used only to size the budget binary search; the
# real tokenizer lives in the model, so this is a deliberate, cheap approximation.
_CHARS_PER_TOKEN = 4

# Aider's ``to_tree`` line clamp: no single rendered line exceeds this many chars (a runaway
# minified line can't blow the budget).
_MAX_LINE_CHARS = 100

# The binary-search tolerance: a rendered map within this fraction UNDER budget is "good enough"
# (Aider's ``ok_err``) — avoids over-iterating for the last few hundred tokens.
_BUDGET_OK_ERR = 0.15

# The section markers a non-empty symbol map always carries (see :func:`_architecture_header`). The
# readiness gate (:mod:`premeeting.verify`) checks for these instead of the old prose sections — a
# map missing them is a shell, not a groundable artifact. NOT the six-section prose shape (that was
# the deprecated LLM map); these are the deterministic map's own headers.
REQUIRED_MAP_MARKERS: tuple[str, ...] = (
    "# Symbol map",
    "Where things live",
    "Entry points",
    "Ranked signatures",
)

# The markers the compact NAVIGATION aid carries (:func:`build_navigation_map`) — no "Ranked
# signatures" body, because the navigation aid is area map + entry points ONLY (the qualitative
# comprehension is the star; this is just the geography beneath it).
REQUIRED_NAV_MARKERS: tuple[str, ...] = (
    "# Navigation map",
    "Where things live",
    "Entry points",
)

# Directory prefixes that are NOT the domain: tests, benchmarks, dev/build scripts, tooling, and
# archived / dead code. The entry-point ranking EXCLUDES these so the "where to go" hints surface the
# real product code (routes, services, core libs, pipelines), not a test helper or an archived
# module. Matched against the top-level path segment of a repo-relative file. A physics floor
# (Law 4) — this is language-agnostic geography, not a situation→action map.
_NON_DOMAIN_TOP_DIRS: frozenset[str] = frozenset(
    {
        "test", "tests", "testing", "__tests__", "spec", "specs", "e2e",
        "bench", "benches", "benchmark", "benchmarks",
        "script", "scripts", "tools", "tooling", "dev", "devtools",
        "example", "examples", "sample", "samples", "demo", "demos", "fixtures",
        "doc", "docs", "documentation",
        "vendor", "third_party", "third-party", "node_modules",
    }
)


def _is_non_domain(rel: str) -> bool:
    """True iff ``rel`` lives under a non-domain top-level area (test/script/bench/doc/archive/…).

    Excludes tests, scripts, benchmarks, docs, vendored code, and any archived/dead-code directory
    (a segment beginning with ``_`` or ``.``, or containing ``archive``/``deprecated``/``old``/
    ``legacy``) so the entry-point ranking favours the real product/domain code."""
    parts = rel.replace("\\", "/").split("/")
    for seg in parts[:-1]:  # directory segments only (the last is the filename)
        low = seg.lower()
        if low in _NON_DOMAIN_TOP_DIRS:
            return True
        if seg.startswith("_") or seg.startswith("."):  # _archive/, .claude/, .github/, …
            return True
        if any(k in low for k in ("archive", "deprecated", "legacy")) or low == "old":
            return True
    return False


class Tag(NamedTuple):
    """One tree-sitter tag: a definition or reference of ``name`` at ``rel_fname:line``.

    ``line`` is 1-based (human ``file:line``). ``kind`` is ``"def"`` or ``"ref"``.
    """

    rel_fname: str
    fname: str
    line: int
    name: str
    kind: str


@dataclass
class _RepoScan:
    """The parsed corpus: the flat tag list + the per-file def/ref index the graph is built from."""

    tags: list[Tag]
    # identifier → set of files that DEFINE it
    defines: dict[str, set[str]]
    # identifier → list of files that REFERENCE it (list, so a file referencing N times counts N)
    references: dict[str, list[str]]
    # (rel_fname, identifier) → the def Tags for that symbol in that file
    definitions: dict[tuple[str, str], list[Tag]]
    # every rel_fname that yielded at least one tag
    files: list[str]


def build_symbol_map(repo_dir: str, *, budget_tokens: int = 11000) -> str:
    """Build a compact, ranked, GROUNDABLE symbol map for the repo at ``repo_dir``.

    Returns the rendered map: a short architecture header (top-level dir map + the
    highest-PageRank entry points with real ``file:line``) followed by the top-ranked per-file
    signature snippets (file header + definition lines with real ``file:line``, bodies elided),
    binary-searched to fit ``budget_tokens``. Deterministic — no model call, so no hallucination.
    """
    root = Path(repo_dir).resolve()
    exclusions = ExclusionManager()
    if root.exists():
        exclusions.scan_after_clone(root)

    scan = _scan_repo(root, exclusions)
    if not scan.tags:
        return f"# Symbol map — {root.name}\n\n(no parseable source symbols found)\n"

    ranked_tags, ranked_files = _rank_tags(scan)

    header = _architecture_header(root, scan, ranked_files, exclusions)
    header_tokens = _estimate_tokens(header)
    body_budget = max(budget_tokens - header_tokens, budget_tokens // 2)

    body = _fit_to_budget(root, ranked_tags, budget_tokens=body_budget)
    return header + "\n" + body


def build_navigation_map(repo_dir: str) -> str:
    """A COMPACT area/entry-point navigation aid — the geography, NOT the ranked-signatures dump.

    This is what sits UNDERNEATH the qualitative comprehension in the resident understanding: the
    top-level area map + the highest-rank DOMAIN entry points (each with a real ``file:line`` so a
    reader can still jump), and NOTHING ELSE. It deliberately drops the giant per-symbol signature
    body :func:`build_symbol_map` renders — that raw code index is exactly what the founder feedback
    said not to shovel into context. A human engineer carries a mental map of WHERE things live, not
    a line-number index; this is that map. Deterministic — no model call, so no hallucination.

    Falls back to :func:`build_symbol_map`'s architecture header shape but under a distinct
    ``# Navigation map`` heading so the readiness gate + the reader can tell it apart. Empty repos
    return a short honest stub.
    """
    root = Path(repo_dir).resolve()
    exclusions = ExclusionManager()
    if root.exists():
        exclusions.scan_after_clone(root)

    scan = _scan_repo(root, exclusions)
    if not scan.tags:
        return f"# Navigation map — {root.name}\n\n(no parseable source symbols found)\n"

    _ranked_tags, ranked_files = _rank_tags(scan)
    return _navigation_header(root, scan, ranked_files, exclusions)


def _navigation_header(
    root: Path,
    scan: _RepoScan,
    ranked_files: list[str],
    exclusions: ExclusionManager,
) -> str:
    """The compact navigation aid: repo name + top-level area map + the highest-rank domain entry
    points, each with a real ``file:line``. No ranked-signatures body (that's the demoted index)."""
    lines: list[str] = [
        f"# Navigation map — {root.name}",
        "(Where to GO at the area/module level — not a code index. The exact file:line is looked "
        "up live; this is the geography.)",
        "",
    ]

    top_dirs = _top_level_map(root, scan, exclusions)
    if top_dirs:
        lines.append("## Where things live (top-level areas)")
        lines.extend(top_dirs)
        lines.append("")

    entry_points = _entry_points(scan, ranked_files, limit=12)
    if entry_points:
        lines.append("## Entry points (highest-rank domain symbols — a starting hint, not exhaustive)")
        lines.extend(entry_points)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ── extraction ───────────────────────────────────────────────────────────────
def _scan_repo(root: Path, exclusions: ExclusionManager) -> _RepoScan:
    """Walk the checkout, extract tags from every parseable, non-excluded source file, and build
    the def/ref index the ranking graph consumes."""
    tags: list[Tag] = []
    defines: dict[str, set[str]] = defaultdict(set)
    references: dict[str, list[str]] = defaultdict(list)
    definitions: dict[tuple[str, str], list[Tag]] = defaultdict(list)
    files: set[str] = set()

    for abs_path in _iter_source_files(root, exclusions):
        rel = str(abs_path.relative_to(root))
        file_tags = list(_tags_for_file(abs_path, rel))
        if not file_tags:
            continue
        files.add(rel)
        for tag in file_tags:
            tags.append(tag)
            if tag.kind == "def":
                defines[tag.name].add(rel)
                definitions[(rel, tag.name)].append(tag)
            elif tag.kind == "ref":
                references[tag.name].append(rel)

    # Aider's fallback: if a language's query yields defs but no refs, treat every DEFINED
    # identifier as also referenced-from-its-own-file, so single-language repos still form a graph.
    if not references:
        for ident, defn_files in defines.items():
            references[ident] = list(defn_files)

    return _RepoScan(
        tags=tags,
        defines=defines,
        references=references,
        definitions=definitions,
        files=sorted(files),
    )


def _iter_source_files(root: Path, exclusions: ExclusionManager) -> Iterator[Path]:
    """Every candidate source file under ``root`` (skips ``.git``, excluded/secret paths, blobs)."""
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(root).parts
        if ".git" in parts:
            continue
        rel = str(p.relative_to(root))
        if exclusions.is_excluded(rel):
            continue
        if filename_to_lang(str(p)) is None:
            continue
        try:
            if p.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield p


def _tags_for_file(abs_path: Path, rel_fname: str) -> Iterator[Tag]:
    """Extract the tree-sitter tags from one file (empty if unparseable / no query for its lang)."""
    lang = filename_to_lang(str(abs_path))
    if lang is None:
        return
    query_scm = _QUERY_DIR / f"{lang}-tags.scm"
    if not query_scm.exists():
        return
    try:
        language = get_language(lang)
        parser = get_parser(lang)
    except Exception:  # noqa: BLE001 - a missing/broken grammar is skipped, never fatal (Law 6)
        return
    try:
        code = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if not code:
        return

    try:
        tree = parser.parse(bytes(code, "utf-8"))
        captures = _run_captures(language, query_scm.read_text(encoding="utf-8"), tree.root_node)
    except Exception:  # noqa: BLE001 - a query/parse error on one file must not sink the whole map
        return

    for capture_name, node in captures:
        if capture_name.startswith("name.definition."):
            kind = "def"
        elif capture_name.startswith("name.reference."):
            kind = "ref"
        else:
            continue
        name = node.text.decode("utf-8", errors="replace") if node.text else ""
        if not name:
            continue
        yield Tag(
            rel_fname=rel_fname,
            fname=str(abs_path),
            line=node.start_point[0] + 1,  # tree-sitter rows are 0-based; file:line is 1-based
            name=name,
            kind=kind,
        )


def _run_captures(language: Any, query_scm: str, node: Any) -> list[tuple[str, Any]]:
    """Run a tag query and return ``(capture_name, node)`` pairs across both tree-sitter APIs.

    New API (``tree_sitter>=0.24``): ``QueryCursor(Query(lang, scm)).captures(node)`` returns
    ``{name: [nodes]}``. Old API: ``language.query(scm).captures(node)`` returns ``[(node, name)]``.
    """
    from tree_sitter import Query  # local import: version-dependent symbol surface

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # some grammars emit deprecation noise on Query build
        try:
            query = Query(language, query_scm)
        except TypeError:
            # Very old API: build the query off the language object itself.
            query = language.query(query_scm)

    out: list[tuple[str, Any]] = []
    if hasattr(query, "captures"):
        result = query.captures(node)
        if isinstance(result, dict):  # new dict-shaped return
            for cap_name, nodes in result.items():
                out.extend((cap_name, n) for n in nodes)
        else:  # old list-of-pairs return
            out.extend((cap_name, n) for n, cap_name in result)
        return out

    from tree_sitter import QueryCursor  # newest API: capture via a cursor

    cursor = QueryCursor(query)
    result = cursor.captures(node)
    for cap_name, nodes in result.items():
        out.extend((cap_name, n) for n in nodes)
    return out


# ── ranking (Aider's personalized-PageRank graph) ─────────────────────────────
def _rank_tags(scan: _RepoScan) -> tuple[list[Tag], list[str]]:
    """Rank the definition tags by personalized PageRank over the referencer→definer graph.

    Replicates Aider's ``get_ranked_tags``: build a :class:`~networkx.MultiDiGraph` with one edge
    per (referencer, definer, identifier), edge weight ``mul * sqrt(#refs)`` where ``mul`` boosts
    long/interesting identifiers (``×10``) and downweights private ``_``-prefixed (``×0.1``) +
    common >5-definer names (``×0.1``); run ``nx.pagerank``; distribute each file's rank across the
    symbols it defines. Returns ``(ranked def-Tags, ranked file list)``.
    """
    defines, references = scan.defines, scan.references
    # Only identifiers that are BOTH defined and referenced form graph edges (Aider's intersection).
    idents = set(defines).intersection(references)

    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    for ident in idents:
        definers = defines[ident]
        mul = _ident_multiplier(ident, definers)
        ref_counts = Counter(references[ident])
        for referencer, num_refs in ref_counts.items():
            for definer in definers:
                graph.add_edge(
                    referencer,
                    definer,
                    weight=mul * math.sqrt(num_refs),
                    ident=ident,
                )

    if graph.number_of_edges() == 0:
        # No cross-file graph — fall back to a def-count ranking so a map still renders.
        return _fallback_rank(scan)

    # Uniform personalization over every file that carries a symbol (Aider seeds all files equally
    # when there is no "chat context" to bias toward — the pre-meeting map has none).
    nodes = list(graph.nodes)
    pers_value = 100.0 / max(len(nodes), 1)
    personalization = {n: pers_value for n in nodes}
    try:
        ranked = nx.pagerank(graph, weight="weight", personalization=personalization, dangling=personalization)
    except (ZeroDivisionError, nx.PowerIterationFailedConvergence, ImportError):
        # A non-converging graph (or a missing scipy backend) degrades to uniform rank — a map
        # still renders (Law 6: never sink the whole build on one numerical failure).
        ranked = {n: 1.0 / max(len(nodes), 1) for n in nodes}

    # Distribute each file's PageRank across its OUT-edges (per identifier it references), then
    # credit the DEFINING (file, ident) — a symbol many high-rank files reach ranks high.
    ranked_definitions: dict[tuple[str, str], float] = defaultdict(float)
    for src in graph.nodes:
        src_rank = ranked[src]
        out_edges = list(graph.out_edges(src, data=True))
        total_weight = sum(data["weight"] for _s, _d, data in out_edges) or 1.0
        for _s, dst, data in out_edges:
            ranked_definitions[(dst, data["ident"])] += src_rank * data["weight"] / total_weight

    ranked_tags: list[Tag] = []
    for (rel_fname, ident), _rank in sorted(
        ranked_definitions.items(), reverse=True, key=lambda kv: (kv[1], kv[0])
    ):
        ranked_tags.extend(scan.definitions.get((rel_fname, ident), []))

    # Append any remaining defs (a defined-but-never-referenced symbol still belongs in the map,
    # after the ranked ones) so nothing groundable is silently dropped.
    seen = {(t.rel_fname, t.name) for t in ranked_tags}
    for (rel_fname, ident), defn_tags in scan.definitions.items():
        if (rel_fname, ident) not in seen:
            ranked_tags.extend(defn_tags)

    ranked_files = [f for f, _r in sorted(ranked.items(), key=lambda kv: kv[1], reverse=True)]
    return ranked_tags, ranked_files


def _ident_multiplier(ident: str, definers: set[str]) -> float:
    """Aider's per-identifier edge-weight multiplier: boost long/interesting names, downweight
    private + common ones (so ``BaseCommand`` outranks ``_x`` and a name defined in 20 files)."""
    mul = 1.0
    if (_is_snake_case(ident) or _is_camel_case(ident)) and len(ident) >= 8:
        mul *= 10.0
    if ident.startswith("_"):
        mul *= 0.1
    if len(definers) > 5:  # a name defined in >5 files is a common/ambiguous token — downweight
        mul *= 0.1
    return mul


def _is_snake_case(ident: str) -> bool:
    return "_" in ident and ident.lower() == ident


def _is_camel_case(ident: str) -> bool:
    return ident != ident.lower() and ident != ident.upper() and "_" not in ident


def _fallback_rank(scan: _RepoScan) -> tuple[list[Tag], list[str]]:
    """No graph edges (e.g. a single tiny file): rank defs by (file def-count, name length)."""
    file_def_count = Counter(t.rel_fname for t in scan.tags if t.kind == "def")
    def_tags = [t for t in scan.tags if t.kind == "def"]
    def_tags.sort(key=lambda t: (file_def_count[t.rel_fname], len(t.name)), reverse=True)
    ranked_files = [f for f, _c in file_def_count.most_common()]
    return def_tags, ranked_files


# ── rendering (Aider's ``to_tree`` + the budget binary search) ─────────────────
def _fit_to_budget(root: Path, ranked_tags: list[Tag], *, budget_tokens: int) -> str:
    """Binary-search the number of top-ranked tags to render so the tree fits ``budget_tokens``.

    Replicates Aider's fit loop: render ``ranked_tags[:middle]``, measure, tighten. Returns the
    largest tree that stays within budget (accepting up to :data:`_BUDGET_OK_ERR` under).
    """
    if not ranked_tags:
        return ""
    num_tags = len(ranked_tags)
    lower, upper = 0, num_tags
    best = ""
    # Aider's seed guess: ~25 tokens per rendered tag.
    middle = min(max(budget_tokens // 25, 1), num_tags)
    lower_target = int(budget_tokens * (1 - _BUDGET_OK_ERR))

    while lower <= upper:
        tree = _to_tree(root, ranked_tags[:middle])
        tokens = _estimate_tokens(tree)
        if tokens <= budget_tokens:
            best = tree
            if tokens >= lower_target or middle == num_tags:
                break
            lower = middle + 1
        else:
            upper = middle - 1
        if lower > upper:
            break
        middle = (lower + upper) // 2
        if middle == 0:
            break
    return best or _to_tree(root, ranked_tags[:1])


def _to_tree(root: Path, tags: Iterable[Tag]) -> str:
    """Render tags grouped by file: a ``rel_fname:`` header then the scope-aware signature lines
    (definition headers with real line numbers, bodies elided) — Aider's ``to_tree`` via
    ``grep-ast``'s :class:`TreeContext`."""
    by_file: dict[str, list[int]] = defaultdict(list)
    order: list[str] = []
    for tag in tags:
        if tag.kind != "def":
            continue
        if tag.rel_fname not in by_file:
            order.append(tag.rel_fname)
        by_file[tag.rel_fname].append(tag.line - 1)  # TreeContext lines-of-interest are 0-based

    out: list[str] = []
    for rel_fname in order:
        abs_path = root / rel_fname
        rendered = _render_file(abs_path, rel_fname, by_file[rel_fname])
        if rendered:
            out.append(f"\n{rel_fname}:\n{rendered}")
    text = "".join(out)
    # Aider's per-line clamp: no rendered line exceeds _MAX_LINE_CHARS (a minified line can't blow
    # the budget). Preserves the trailing newline shape.
    clamped = "\n".join(line[:_MAX_LINE_CHARS] for line in text.splitlines())
    return clamped + ("\n" if text else "")


def _render_file(abs_path: Path, rel_fname: str, lois: list[int]) -> str:
    """Render one file's signature lines around its lines-of-interest with real line numbers."""
    try:
        code = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    try:
        context = TreeContext(
            rel_fname,
            code,
            color=False,
            line_number=True,  # emit real 1-based line numbers → groundable file:line
            child_context=False,
            last_line=False,
            margin=0,
            mark_lois=False,
            loi_pad=0,
            show_top_of_file_parent_scope=False,
        )
        context.add_lines_of_interest(lois)
        context.add_context()
        return str(context.format())
    except Exception:  # noqa: BLE001 - a render failure on one file must not sink the whole map
        return ""


# ── architecture header (deterministic from the same graph) ───────────────────
def _architecture_header(
    root: Path,
    scan: _RepoScan,
    ranked_files: list[str],
    exclusions: ExclusionManager,
) -> str:
    """A short header: repo name + top-level dir map + the highest-PageRank entry points, each
    with a real ``file:line`` — deterministic from the graph, NOT an LLM pass."""
    lines: list[str] = [f"# Symbol map — {root.name}", ""]

    top_dirs = _top_level_map(root, scan, exclusions)
    if top_dirs:
        lines.append("## Where things live (top-level)")
        lines.extend(top_dirs)
        lines.append("")

    entry_points = _entry_points(scan, ranked_files)
    if entry_points:
        lines.append("## Entry points (highest-rank symbols)")
        lines.extend(entry_points)
        lines.append("")

    lines.append(
        "## Ranked signatures (real file:line — cite these; open the file for the body)"
    )
    return "\n".join(lines)


def _top_level_map(root: Path, scan: _RepoScan, exclusions: ExclusionManager) -> list[str]:
    """A one-line-per-top-level-dir/file map, annotated with how many mapped source files it holds."""
    file_count: Counter[str] = Counter()
    for rel in scan.files:
        top = rel.split("/", 1)[0]
        file_count[top] += 1

    entries: list[str] = []
    try:
        top_entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
    except OSError:
        return entries
    for p in top_entries:
        if p.name == ".git":
            continue
        rel = p.name
        if p.is_file() and exclusions.is_excluded(rel):
            continue
        if p.is_dir():
            n = file_count.get(rel, 0)
            suffix = f" — {n} mapped source file{'s' if n != 1 else ''}" if n else ""
            entries.append(f"- {rel}/{suffix}")
        else:
            entries.append(f"- {rel}")
    return entries


def _entry_points(scan: _RepoScan, ranked_files: list[str], *, limit: int = 8) -> list[str]:
    """The highest-PageRank files, each named with a representative defined symbol + real file:line.

    DOMAIN-FAVOURING: tests/scripts/benchmarks/docs/archived code are pushed BELOW real product code,
    so the "where to go" hints surface the routes/services/core libs a meeting actually asks about —
    not a test helper (``log``/``sleep``) or an archived module. Non-domain files are only used to
    backfill if the domain has too few ranked entries (so a tiny/atypical repo still yields a map)."""
    domain: list[str] = []
    non_domain: list[str] = []
    for rel_fname in ranked_files:
        rep = _representative_symbol(scan, rel_fname)
        if rep is None:
            continue
        entry = f"- `{rel_fname}:{rep.line}` — {rep.name}"
        (non_domain if _is_non_domain(rel_fname) else domain).append(entry)
    out = domain[:limit]
    if len(out) < limit:  # backfill from non-domain only when the domain is thin
        out.extend(non_domain[: limit - len(out)])
    return out


def _representative_symbol(scan: _RepoScan, rel_fname: str) -> Tag | None:
    """The most-referenced (else longest-named, else first) def in a file — its headline symbol."""
    defs = [t for t in scan.tags if t.rel_fname == rel_fname and t.kind == "def"]
    if not defs:
        return None
    return max(defs, key=lambda t: (len(scan.references.get(t.name, [])), len(t.name), -t.line))


# ── misc ──────────────────────────────────────────────────────────────────────
def _estimate_tokens(text: str) -> int:
    """A cheap ~4-chars-per-token estimate (only sizes the budget search; the model tokenizes)."""
    return max(len(text) // _CHARS_PER_TOKEN, 1)


__all__ = [
    "REQUIRED_MAP_MARKERS",
    "REQUIRED_NAV_MARKERS",
    "Tag",
    "build_navigation_map",
    "build_symbol_map",
]
