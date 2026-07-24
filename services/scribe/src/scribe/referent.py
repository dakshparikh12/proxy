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


# The canonical per-repo graph.db schema written by Doc 01's
# ``code_intel.graph_store.GraphStore.write_graph`` (§12.2): columns
# ``id, kind, file_path, line, exported, built_at_sha``. This is the REAL corpus a
# meeting binds against. The legacy ``node_id, area, file, symbol`` shape is a
# hand-built test double — we still read it (dual-schema) so a synthetic fixture
# keeps working, but the real Doc-01 store is the one that matters in production.
_REAL_SELECT = "SELECT id, kind, file_path, line FROM graph_nodes"
_LEGACY_SELECT = "SELECT node_id, area, file, symbol FROM graph_nodes"


def _top_package(file_path: str) -> str:
    """Top-level path segment of ``file_path`` -> the node's overview *area*.

    ``payments/checkout.py`` -> ``payments``; a bare file (``conf.py``) -> ``""``.
    Deterministic, pure string work — the area a Doc-01 node belongs to.
    """
    fp = file_path.replace("\\", "/")
    return fp.split("/", 1)[0] if "/" in fp else ""


def _real_row_to_keys(node_id: str, file_path: str) -> tuple[str, str, str, str]:
    """Derive ``(node_id, area, file, symbol)`` match keys from a real Doc-01 row.

    Maps the canonical ``id``/``file_path`` to the matcher's four match keys so the
    same ranking logic (symbol > file > area > id-leaf) works over the real schema:

    * ``symbol`` = the leaf of ``id`` after ``::`` (``a.py::Cls.m`` -> ``m``); for a
      dotted module id with no ``::`` (``docs.conf``) the leaf after ``.``; for a
      ``table::name`` id the leaf ``name``.
    * ``file``   = ``file_path`` (its leaf is also matched by ``_match_graph_nodes``).
    * ``area``   = the top-level package of ``file_path`` (``payments/…`` -> ``payments``).
    """
    symbol = _leaf(node_id)
    area = _top_package(file_path)
    return (node_id, area, file_path, symbol)


def _column_names(conn: sqlite3.Connection) -> set[str]:
    """The column names of ``graph_nodes`` (via table_info — no row read)."""
    cur = conn.execute("PRAGMA table_info(graph_nodes)")
    return {str(row[1]) for row in cur.fetchall()}


def _read_graph_nodes(db_path: Optional[str], *, strict: bool) -> list[tuple[str, str, str, str]]:
    """Read the ``graph_nodes`` corpus from SQLite — the ONLY table touched.

    Handles BOTH the real Doc-01 canonical schema (``id, kind, file_path, line, …``,
    written by ``GraphStore.write_graph``) and the legacy synthetic shape
    (``node_id, area, file, symbol``). The real schema's ``id``/``file_path`` are
    mapped into the matcher's ``(node_id, area, file, symbol)`` keys so the same
    deterministic ranking binds a term to a REAL node id (AC-REFM-04).

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
    real_schema = False
    try:
        # Read-only URI: we never write, and a concurrent writer can't corrupt our
        # view. mode=ro makes a missing file raise (caught below) rather than
        # silently create an empty db — so "absent corpus" is honest, not faked.
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
        # Pick the SELECT by the columns actually present: the real Doc-01 store
        # exposes ``id``; the legacy double exposes ``node_id``. Neither present
        # (e.g. a wrong-shape table) falls through to the real SELECT, which errors
        # and degrades per policy — preserving the AC-REFM-04-NEG behavior.
        cols = _column_names(conn)
        if "id" in cols and "file_path" in cols:
            real_schema = True
            rows = conn.execute(_REAL_SELECT).fetchall()
        else:
            rows = conn.execute(_LEGACY_SELECT).fetchall()
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
            if real_schema:
                node_id, _kind, file_path, _line = row
                coerced.append(_real_row_to_keys(_as_str(node_id), _as_str(file_path)))
            else:
                node_id, area, file, symbol = row
                coerced.append(
                    (_as_str(node_id), _as_str(area), _as_str(file), _as_str(symbol))
                )
        except (ValueError, TypeError) as exc:
            if strict:
                raise ReferentCorpusError(f"malformed graph_nodes row: {row!r}") from exc
            continue
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
