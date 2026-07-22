"""The referent matcher — deterministic, no-LLM referent binding (Doc 03 §3.4).

When the Scribe marks a referent candidate ("checkout"), this matcher binds it to
a real code node via :func:`lookup_referent` — a **deterministic, no-model** lookup
over Doc 01's **core overview areas + the ``graph_nodes`` table** (area names, file
names, key symbols). It returns ``node_id | area | None``:

* a matched ``node_id`` (a real row id in ``graph_nodes``),
* a matched overview ``area`` name,
* or ``None`` when nothing binds — an unmatched candidate stays **named-but-unbound**
  (§3.8: the notes never fabricate to fill a hole).

Hard invariants (each is a sealed acceptance criterion, AC-REFM-*):

* **No LLM, no external call.** The whole call graph is local string/rank work over
  SQLite ``graph_nodes`` + the in-memory overview-areas structure — no
  ``anthropic``/``openai`` call, no ``libs.http.call_external`` (AC-REFM-01/07).
* **Return type is ``str | None`` only** — never bool/list/dict/object (AC-REFM-02).
* **Corpus is scoped** to ``graph_nodes`` + overview areas; no other table, no git
  walk, no full-codebase scan, no external index (AC-REFM-03).
* **Degrades honestly.** An empty/absent corpus, a refused connection, a missing
  table, or garbage rows all yield ``None`` (or a surfaced failure for a genuinely
  broken handle) — never a fabricated binding, never a silent-corrupt proceed
  (AC-REFM-02-NEG / -03-NEG / -04-NEG / -05).
* **Deterministic** — the same term over a frozen corpus always returns the same
  value; ranking ties break on a stable key, never on hash/random order (AC-REFM-08).

*(The agentic/LLM referent map is Expansion — V0 binds deterministically off the
overview areas + graph nodes only, §3.4.)*
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

# The single table this matcher is permitted to read (AC-REFM-03). Naming it as a
# module constant keeps the scope auditable: a static scan sees exactly one table
# name and no other.
GRAPH_NODES_TABLE = "graph_nodes"


class ReferentCorpusError(RuntimeError):
    """A genuinely broken corpus handle (not merely empty).

    Raised only for a handle the caller *asked* us to treat as authoritative but
    which is structurally unusable in a way we must surface rather than silently
    swallow — e.g. an explicit ``strict=True`` lookup against a database whose
    ``graph_nodes`` table is malformed/garbage. The default (lenient) path never
    raises: a missing/empty/absent corpus degrades to ``None`` (AC-REFM-02-NEG),
    which is the honest "named-but-unbound" outcome. ``strict`` exists so a caller
    that *needs* to know the corpus is broken can choose to see the failure instead
    of a false ``None`` (AC-REFM-04-NEG: honest degradation, no silent proceed).
    """


@dataclass(frozen=True)
class OverviewArea:
    """One core-overview area (Doc 01) — an area name the matcher may bind to.

    Frozen + ordered by construction so a lookup over the areas is deterministic.
    """

    name: str


@dataclass(frozen=True)
class ReferentCorpus:
    """The *only* data the matcher reads: overview areas + a ``graph_nodes`` handle.

    ``areas`` is the in-memory core-overview-areas structure (Doc 01). ``db_path``
    points at a SQLite database whose ``graph_nodes`` table carries the real code
    nodes (id, area, file, symbol). Either may be empty/absent — the matcher then
    simply finds no match and returns ``None`` (honest unbound), never raising on
    the ordinary empty/missing case.
    """

    areas: tuple[OverviewArea, ...] = ()
    db_path: Optional[str] = None


@dataclass
class BoundReferent:
    """A candidate term after matching — the notes-entry carrier for the binding.

    ``binding`` is the matched ``node_id``/``area`` or ``None``. ``bound`` is
    ``True`` iff a real node/area matched: an unmatched candidate stays
    ``bound=False`` with ``binding=None`` — plain, named-but-unbound, never a
    fabricated id and never an empty-string stand-in (AC-REFM-05). The binding
    is carried verbatim through the notes fold so the Workroom reads which real
    node the room meant (AC-REFM-06).
    """

    term: str
    binding: Optional[str] = None
    bound: bool = False


def _normalize(term: str) -> str:
    """Deterministic normalization for matching: casefold + strip surrounding ws.

    Pure string work — no locale-dependent or randomized transform.
    """
    return term.strip().casefold()


def _leaf(value: str) -> str:
    """The trailing path/symbol segment of an id or path (``a/b/c`` -> ``c``)."""
    # Split on the common separators a graph id / file path uses. Pure, ordered.
    leaf = value
    for sep in ("::", "/", "."):
        if sep in leaf:
            leaf = leaf.rsplit(sep, 1)[-1]
    return leaf


def _match_areas(term_norm: str, areas: tuple[OverviewArea, ...]) -> Optional[str]:
    """Match a normalized term against overview area names. Deterministic.

    Ranking (best first): exact area-name match, then leaf-segment match. Ties
    break on the area name (a stable, total order) so the result is identical
    across runs and hash seeds (AC-REFM-08).
    """
    exact: list[str] = []
    leaf_hits: list[str] = []
    for area in areas:
        name_norm = _normalize(area.name)
        if name_norm == term_norm:
            exact.append(area.name)
        elif _normalize(_leaf(area.name)) == term_norm:
            leaf_hits.append(area.name)
    if exact:
        return sorted(exact)[0]
    if leaf_hits:
        return sorted(leaf_hits)[0]
    return None


def _match_graph_nodes(term_norm: str, rows: list[tuple[str, str, str, str]]) -> Optional[str]:
    """Match a normalized term against ``graph_nodes`` rows. Deterministic.

    Each row is ``(node_id, area, file, symbol)``. Ranking (best first):

    1. exact symbol match,
    2. exact file(-leaf) match,
    3. exact area match,
    4. leaf-of-node-id match.

    Within a rank, ties break on ``node_id`` (a stable total order) — never on
    row-iteration or hash order — so 100 calls return the same id (AC-REFM-08).
    The returned value is always a real ``node_id`` present in the corpus; nothing
    is synthesized (AC-REFM-04, no fabrication).
    """
    by_symbol: list[str] = []
    by_file: list[str] = []
    by_area: list[str] = []
    by_id_leaf: list[str] = []
    for node_id, area, file, symbol in rows:
        if not node_id:
            # A row with no real id can never be a legitimate binding — skip it
            # rather than return an empty-string stand-in (AC-REFM-05).
            continue
        if symbol and _normalize(symbol) == term_norm:
            by_symbol.append(node_id)
        elif file and (_normalize(file) == term_norm or _normalize(_leaf(file)) == term_norm):
            by_file.append(node_id)
        elif area and _normalize(area) == term_norm:
            by_area.append(node_id)
        elif _normalize(_leaf(node_id)) == term_norm:
            by_id_leaf.append(node_id)
    for bucket in (by_symbol, by_file, by_area, by_id_leaf):
        if bucket:
            return sorted(bucket)[0]
    return None


def _read_graph_nodes(db_path: Optional[str], *, strict: bool) -> list[tuple[str, str, str, str]]:
    """Read the ``graph_nodes`` corpus from SQLite — the ONLY table touched.

    Returns ``[]`` (honest empty) when the database file is absent, has no
    ``graph_nodes`` table, or the table is empty. Reads are scoped to a single
    ``SELECT ... FROM graph_nodes`` — no other table, no git tree, no external
    index is ever queried (AC-REFM-03 / -03-NEG).

    Degradation policy (AC-REFM-02-NEG / -04-NEG):

    * lenient (default): any SQLite error (missing file, missing table, refused/
      malformed handle) collapses to ``[]`` -> caller returns ``None`` (honest
      unbound). No exception escapes; no fabricated row is invented.
    * strict: a genuinely broken/garbage corpus re-raises as
      :class:`ReferentCorpusError` so a caller that must know surfaces the failure
      rather than silently proceeding on a false ``None``.
    """
    if not db_path:
        return []
    conn: Optional[sqlite3.Connection] = None
    try:
        # Read-only, immutable=1 URI: we never write, and a concurrent writer can't
        # corrupt our view. mode=ro makes a missing file raise (caught below) rather
        # than silently create an empty db — so "absent corpus" is honest, not faked.
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        # Scope guard: confirm the table exists via sqlite_master, then SELECT only
        # from graph_nodes. If the table is absent we return [] (honest empty).
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (GRAPH_NODES_TABLE,),
        )
        if cur.fetchone() is None:
            return []
        rows = conn.execute(
            "SELECT node_id, area, file, symbol FROM graph_nodes"  # literal table name — no interpolation
        ).fetchall()
    except sqlite3.Error as exc:
        if strict:
            raise ReferentCorpusError(f"graph_nodes corpus unreadable: {exc}") from exc
        return []
    finally:
        if conn is not None:
            conn.close()
    # Coerce each cell to a plain str — a garbage/NULL cell becomes "" and is then
    # ignored by the matchers (never returned as a binding), so malformed rows
    # degrade to "no match" rather than a corrupt binding (AC-REFM-04-NEG).
    coerced: list[tuple[str, str, str, str]] = []
    for row in rows:
        try:
            node_id, area, file, symbol = row
        except (ValueError, TypeError) as exc:
            if strict:
                raise ReferentCorpusError(f"malformed graph_nodes row: {row!r}") from exc
            continue
        coerced.append((_as_str(node_id), _as_str(area), _as_str(file), _as_str(symbol)))
    return coerced


def _as_str(value: object) -> str:
    """Coerce a SQLite cell to a plain string; NULL/None -> ''. Pure, total."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def lookup_referent(term: str, corpus: ReferentCorpus, *, strict: bool = False) -> Optional[str]:
    """Bind a Scribe-marked referent candidate to a real code node — no LLM.

    Deterministic, no-model lookup over the overview areas + ``graph_nodes`` only
    (§3.4). Returns:

    * a real ``node_id`` (from ``graph_nodes``) or an overview ``area`` name when
      the term matches, or
    * ``None`` when nothing binds — the candidate stays named-but-unbound (§3.8).

    Match order: ``graph_nodes`` (symbol > file > area > id-leaf) first — it is the
    most specific corpus — then overview areas. Everything is pure string/rank work
    over the two in-scope sources; no external call is made on any path
    (AC-REFM-01/07). The return is always ``str`` or ``None`` (AC-REFM-02).

    ``strict`` only changes the failure mode for a *broken* corpus handle: default
    (lenient) degrades a missing/empty/garbage corpus to ``None``; ``strict=True``
    re-raises as :class:`ReferentCorpusError` so a caller can surface it.
    """
    if not term or not term.strip():
        return None
    term_norm = _normalize(term)

    rows = _read_graph_nodes(corpus.db_path, strict=strict)
    node_hit = _match_graph_nodes(term_norm, rows)
    if node_hit is not None:
        return node_hit

    area_hit = _match_areas(term_norm, corpus.areas)
    if area_hit is not None:
        return area_hit

    return None


def bind_referent(term: str, corpus: ReferentCorpus, *, strict: bool = False) -> BoundReferent:
    """Match one candidate and wrap it as a :class:`BoundReferent` notes carrier.

    An unmatched candidate comes back ``bound=False`` / ``binding=None`` — plain,
    named-but-unbound, never fabricated (AC-REFM-05). A matched candidate carries
    the real ``node_id``/``area`` in ``binding`` for the notes fold (AC-REFM-06).
    """
    binding = lookup_referent(term, corpus, strict=strict)
    return BoundReferent(term=term, binding=binding, bound=binding is not None)


def bind_referents(
    terms: list[str], corpus: ReferentCorpus, *, strict: bool = False
) -> list[BoundReferent]:
    """Bind a list of candidate terms, preserving input order (deterministic)."""
    return [bind_referent(term, corpus, strict=strict) for term in terms]


@dataclass
class FoldedReferents:
    """A tiny stand-in for the notes fold's referent view, read back by consumers.

    Models the property AC-REFM-06 checks: the binding a matcher produced survives
    the fold + read-back verbatim — it is not stripped, nulled, or lost. Keyed by
    referent term so a downstream reader (the Workroom, via the notes read path)
    resolves ``term -> binding``.
    """

    bindings: dict[str, Optional[str]] = field(default_factory=dict)

    @classmethod
    def fold(cls, referents: list[BoundReferent]) -> "FoldedReferents":
        """Deterministic left-fold: later entries for the same term supersede."""
        acc: dict[str, Optional[str]] = {}
        for ref in referents:
            acc[ref.term] = ref.binding
        return cls(bindings=acc)

    def binding_for(self, term: str) -> Optional[str]:
        """Read a term's binding back — the value the matcher bound, unchanged."""
        return self.bindings.get(term)


__all__ = [
    "GRAPH_NODES_TABLE",
    "ReferentCorpusError",
    "OverviewArea",
    "ReferentCorpus",
    "BoundReferent",
    "FoldedReferents",
    "lookup_referent",
    "bind_referent",
    "bind_referents",
]
