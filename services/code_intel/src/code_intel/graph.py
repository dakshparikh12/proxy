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


@dataclass
class Edge:
    source: str
    target: str
    kind: str


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
        ranked = sorted(self.nodes, key=lambda n: (-n.pagerank, n.id))
        return ranked[:limit] if limit is not None else ranked

    # -- traversal -------------------------------------------------------- #
    def _reverse_adj(self) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        transitive: dict[str, list[str]] = defaultdict(list)
        reads: dict[str, list[str]] = defaultdict(list)
        for e in self.edges:
            if e.kind == _READS:
                reads[e.target].append(e.source)
            elif e.kind in _TRANSITIVE_KINDS:
                transitive[e.target].append(e.source)
        return transitive, reads

    def reverse_dependents(self, target_id: str) -> list[str]:
        transitive, reads = self._reverse_adj()
        result: set[str] = set()
        seen = {target_id}
        dq: deque[str] = deque([target_id])
        while dq:
            cur = dq.popleft()
            for pred in transitive.get(cur, ()):
                if pred not in seen:
                    seen.add(pred)
                    result.add(pred)
                    dq.append(pred)
            if cur == target_id:
                for pred in reads.get(cur, ()):
                    result.add(pred)
                    if pred not in seen:
                        seen.add(pred)
                        dq.append(pred)
        return [nid for nid in result if nid != target_id]

    def entry_point_ids(self) -> list[str]:
        has_incoming: set[str] = set()
        for e in self.edges:
            has_incoming.add(e.target)
        return [n.id for n in self.nodes if n.id not in has_incoming]
