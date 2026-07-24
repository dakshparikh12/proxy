"""In-memory dependency graph + deterministic ranking / traversal (M4/M5).

Nodes are declarations (functions, tables); edges are typed references
(``calls``/``imports``/``writes``/``reads``/``extends``/``implements``). PageRank
is computed with networkx (deterministic power iteration, no random seed — ties
broken by node id). ``get_dependents`` walks the reverse graph transitively over
every edge kind *except* ``reads``, which is followed depth-1 only (founder
decision, AC-M5-003).
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

_TRANSITIVE_KINDS = {"calls", "imports", "writes", "extends", "implements"}
_READS = "reads"


@dataclass
class Node:
    id: str
    path: str
    line: int
    kind: str = "function"
    pagerank: float = 0.0
    exported: int = 0
    built_at_sha: str = ""


@dataclass
class Edge:
    source: str
    target: str
    kind: str
    file_path: str = ""
    line: int = 0
    # How the edge target was resolved. "name" = a direct ``ast.Name`` callee /
    # in-repo import (an exact syntactic referent). "attr" = a method / qualified
    # call (``self.foo()``, ``obj.method()``, ``pkg.func()``) recovered by
    # trailing-attr-name heuristic — it may bind the wrong same-named symbol, so
    # any dependent reached THROUGH such an edge is a lower-bound, never resolved
    # (Law 2). Default "name" preserves the exact-referent semantics of every
    # pre-existing edge kind (imports/reads/writes/extends/implements).
    resolution: str = "name"


@dataclass
class Graph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_id: dict[str, Node] = {n.id: n for n in self.nodes}

    # -- construction helpers -------------------------------------------- #
    def index(self) -> None:
        self._by_id = {n.id: n for n in self.nodes}

    def get(self, node_id: str) -> Node | None:
        return self._by_id.get(node_id)

    def resolve_symbol(self, symbol: str) -> list[Node]:
        """Resolve a bare symbol or full id to matching node(s)."""
        exact = [n for n in self.nodes if n.id == symbol]
        if exact:
            return exact
        return [n for n in self.nodes if n.id.rsplit("::", 1)[-1] == symbol]

    # -- ranking ---------------------------------------------------------- #
    def compute_pagerank(self, alpha: float = 0.85, iterations: int = 100) -> None:
        """Deterministic power-iteration PageRank (no seed; scipy-free).

        Matches the golden derivation (d=0.85, 100 iterations, uniform teleport,
        dangling mass redistributed uniformly). Ties are broken elsewhere by node id.
        """
        ids = [n.id for n in self.nodes]
        n = len(ids)
        if n == 0:
            return
        id_set = set(ids)
        out_links: dict[str, list[str]] = {i: [] for i in ids}
        for e in self.edges:
            if e.source in id_set and e.target in id_set:
                out_links[e.source].append(e.target)
        dangling = [i for i in ids if not out_links[i]]
        pr = {i: 1.0 / n for i in ids}
        base = (1.0 - alpha) / n
        for _ in range(iterations):
            dangle = alpha * sum(pr[i] for i in dangling) / n
            new = {i: base + dangle for i in ids}
            for src, targets in out_links.items():
                if targets:
                    share = alpha * pr[src] / len(targets)
                    for tgt in targets:
                        new[tgt] += share
            pr = new
        for node in self.nodes:
            node.pagerank = float(pr.get(node.id, 0.0))

    def get_nodes_by_pagerank(self, limit: int | None = None) -> list[Node]:
        # The ranked OVERVIEW is key *symbols* (functions/classes/tables/routes),
        # §3.4. `module` nodes exist for import blast-radius (get_dependents) but
        # are the file granularity, not overview symbols, so they are excluded
        # here (still fully queryable via graph traversal + resolve_symbol).
        symbols = [n for n in self.nodes if n.kind != "module"]
        ranked = sorted(symbols, key=lambda n: (-n.pagerank, n.id))
        return ranked[:limit] if limit is not None else ranked

    # -- traversal -------------------------------------------------------- #
    def _reverse_adj(self) -> tuple[dict[str, list[tuple[str, bool]]], dict[str, list[str]]]:
        # transitive predecessors carry a ``heuristic`` flag: True when the edge
        # was recovered by trailing-attr-name (``resolution == "attr"``, a method /
        # qualified call). Any dependent whose ONLY reverse path crosses such an
        # edge is a lower-bound (Law 2), so the flag must ride the adjacency.
        transitive: dict[str, list[tuple[str, bool]]] = defaultdict(list)
        reads: dict[str, list[str]] = defaultdict(list)
        for e in self.edges:
            if e.kind == _READS:
                reads[e.target].append(e.source)
            elif e.kind in _TRANSITIVE_KINDS:
                heuristic = e.kind == "calls" and getattr(e, "resolution", "name") == "attr"
                transitive[e.target].append((e.source, heuristic))
        return transitive, reads

    def reverse_dependents(self, target_id: str) -> list[str]:
        return list(self.reverse_dependents_with_confidence(target_id).keys())

    def reverse_dependents_with_confidence(self, target_id: str) -> dict[str, str]:
        """Reverse-dependency set, each tagged ``resolved`` or ``lower-bound``.

        A dependent is ``resolved`` when it is reachable from ``target_id`` via at
        least one reverse path made entirely of exact-referent edges (name-resolved
        calls, imports, reads, writes, extends, implements). It is ``lower-bound``
        when EVERY reverse path to it must cross a heuristic attribute/method-call
        edge (``resolution == "attr"``) — that inclusion is real but the exact
        binding is unproven, so it must never be overstated as ``resolved`` (Law 2).

        Implemented as a 0-1 BFS: reaching a node via a name-only path (weight 0)
        strictly dominates reaching it via any heuristic path (weight 1), so we
        prefer the name-only frontier and only downgrade when no exact path exists.
        """
        transitive, reads = self._reverse_adj()
        # confidence[node] = False (exact/resolved) or True (lower-bound). Absent =
        # not yet reached. A node once marked exact is never downgraded.
        conf: dict[str, bool] = {}
        # 0-1 BFS: two-ended deque — exact hops push front, heuristic hops push back.
        dq: deque[tuple[str, bool]] = deque([(target_id, False)])
        while dq:
            cur, cur_lb = dq.popleft()
            # skip if we already have an equal-or-better label for cur
            prev = conf.get(cur)
            if cur != target_id:
                if prev is False:
                    continue  # already resolved — best possible
                if prev is True and cur_lb:
                    continue  # already lower-bound and this path is no better
                conf[cur] = cur_lb
            for pred, heuristic in transitive.get(cur, ()):
                pred_lb = cur_lb or heuristic
                known = conf.get(pred)
                if known is False:
                    continue
                if known is True and pred_lb:
                    continue
                if pred_lb:
                    dq.append((pred, True))
                else:
                    dq.appendleft((pred, False))
            if cur == target_id:
                # reads edges are followed depth-1 only, and are exact referents.
                for pred in reads.get(cur, ()):
                    if conf.get(pred) is not False:
                        dq.appendleft((pred, False))
        conf.pop(target_id, None)
        return {nid: ("lower-bound" if lb else "resolved") for nid, lb in conf.items()}

    def entry_point_ids(self) -> list[str]:
        has_incoming: set[str] = set()
        for e in self.edges:
            has_incoming.add(e.target)
        return [n.id for n in self.nodes if n.id not in has_incoming]
