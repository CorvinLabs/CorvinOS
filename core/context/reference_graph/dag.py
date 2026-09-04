"""Reference DAG validation: cycle detection + topological order (ADR-0563 Phase 2).

References may declare ``depends_on`` edges (file paths). A cycle (A -> B -> A)
would make on-demand resolution non-terminating, so the graph is validated
fail-closed at build time and again by the loader before transitive resolution.

The algorithm is an *iterative* three-colour DFS: a hostile 100k-node chain
must not blow the interpreter recursion limit (that would be a crash, not a
detection).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

from .types import ContextReference


@dataclass(frozen=True)
class CycleError(Exception):
    """A dependency cycle was found. ``path`` lists the nodes forming the cycle."""

    path: tuple[str, ...]

    def __str__(self) -> str:
        return "CycleError: " + " -> ".join(self.path)


@dataclass(frozen=True)
class DanglingDependencyError(Exception):
    """A reference depends on a node that is not part of the graph (fail-closed)."""

    source: str
    missing: str

    def __str__(self) -> str:
        return f"DanglingDependencyError: {self.source} depends on unknown {self.missing}"


@dataclass(frozen=True)
class DagValidation:
    """Immutable result of a graph validation."""

    ok: bool
    node_count: int
    edge_count: int
    cycle: Optional[tuple[str, ...]] = None
    dangling: Optional[tuple[str, str]] = None
    topological_order: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.ok and (self.cycle is not None or self.dangling is not None):
            raise ValueError("ok=True cannot carry a cycle or dangling edge")
        if not self.ok and self.cycle is None and self.dangling is None:
            raise ValueError("ok=False must name a cycle or a dangling edge")


class ReferenceGraph:
    """Directed graph of reference nodes. Edges: ``src`` depends on ``dst``."""

    def __init__(self) -> None:
        self._adj: dict[str, list[str]] = {}

    @classmethod
    def from_references(cls, references: Iterable[ContextReference]) -> "ReferenceGraph":
        graph = cls()
        for ref in references:
            graph.add_node(ref.file_path)
            for dep in ref.depends_on:
                graph.add_edge(ref.file_path, dep)
        return graph

    def add_node(self, node: str) -> None:
        if not node:
            raise ValueError("node id cannot be empty")
        self._adj.setdefault(node, [])

    def add_edge(self, src: str, dst: str) -> None:
        """Add edge src -> dst. The dst node is NOT auto-created (dangling stays detectable)."""
        if not src or not dst:
            raise ValueError("edge endpoints cannot be empty")
        self._adj.setdefault(src, [])
        if dst not in self._adj[src]:
            self._adj[src].append(dst)

    @property
    def nodes(self) -> tuple[str, ...]:
        return tuple(self._adj.keys())

    @property
    def edges(self) -> Mapping[str, tuple[str, ...]]:
        return {k: tuple(v) for k, v in self._adj.items()}

    def edge_count(self) -> int:
        return sum(len(v) for v in self._adj.values())

    def find_dangling(self) -> Optional[tuple[str, str]]:
        for src, dsts in self._adj.items():
            for dst in dsts:
                if dst not in self._adj:
                    return (src, dst)
        return None

    def find_cycle(self) -> Optional[tuple[str, ...]]:
        """
        Return the first cycle found as a node path (closed: first == last),
        or None if the graph is acyclic. Iterative DFS, deterministic order.
        """
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {n: WHITE for n in self._adj}
        parent: dict[str, Optional[str]] = {}

        for root in self._adj:
            if colour[root] != WHITE:
                continue
            parent[root] = None
            stack: list[tuple[str, int]] = [(root, 0)]
            colour[root] = GREY
            while stack:
                node, idx = stack[-1]
                neighbours = self._adj.get(node, [])
                if idx < len(neighbours):
                    stack[-1] = (node, idx + 1)
                    nxt = neighbours[idx]
                    if nxt not in colour:
                        # dangling edge: handled by find_dangling(); ignore here
                        continue
                    if colour[nxt] == GREY:
                        # back edge -> reconstruct cycle nxt ... node -> nxt
                        path = [node]
                        cur = node
                        while cur != nxt:
                            cur = parent[cur]  # type: ignore[assignment]
                            path.append(cur)
                        path.reverse()
                        path.append(nxt)
                        return tuple(path)
                    if colour[nxt] == WHITE:
                        colour[nxt] = GREY
                        parent[nxt] = node
                        stack.append((nxt, 0))
                else:
                    colour[node] = BLACK
                    stack.pop()
        return None

    def topological_order(self) -> tuple[str, ...]:
        """Kahn's algorithm. Raises CycleError / DanglingDependencyError (fail-closed)."""
        dangling = self.find_dangling()
        if dangling is not None:
            raise DanglingDependencyError(source=dangling[0], missing=dangling[1])
        cycle = self.find_cycle()
        if cycle is not None:
            raise CycleError(path=cycle)

        indeg: dict[str, int] = {n: 0 for n in self._adj}
        for dsts in self._adj.values():
            for d in dsts:
                indeg[d] += 1
        ready = [n for n, d in indeg.items() if d == 0]
        order: list[str] = []
        while ready:
            n = ready.pop(0)
            order.append(n)
            for d in self._adj[n]:
                indeg[d] -= 1
                if indeg[d] == 0:
                    ready.append(d)
        if len(order) != len(self._adj):  # defensive: cannot happen after find_cycle()
            raise CycleError(path=tuple(n for n in self._adj if n not in order))
        return tuple(order)

    def transitive_dependencies(self, node: str) -> tuple[str, ...]:
        """All nodes reachable from ``node`` (excluding itself), in resolution order.

        Resolution order = dependencies before dependents (reverse topological
        restricted to the reachable sub-graph). Raises on cycles / dangling edges.
        """
        if node not in self._adj:
            raise DanglingDependencyError(source="<root>", missing=node)
        full_order = self.topological_order()
        reachable: set[str] = set()
        frontier = [node]
        while frontier:
            cur = frontier.pop()
            for d in self._adj[cur]:
                if d not in reachable:
                    reachable.add(d)
                    frontier.append(d)
        return tuple(n for n in reversed(full_order) if n in reachable)

    def validate(self) -> DagValidation:
        node_count = len(self._adj)
        edge_count = self.edge_count()
        dangling = self.find_dangling()
        if dangling is not None:
            return DagValidation(ok=False, node_count=node_count, edge_count=edge_count, dangling=dangling)
        cycle = self.find_cycle()
        if cycle is not None:
            return DagValidation(ok=False, node_count=node_count, edge_count=edge_count, cycle=cycle)
        return DagValidation(
            ok=True,
            node_count=node_count,
            edge_count=edge_count,
            topological_order=self.topological_order(),
        )


def validate_reference_dag(references: Iterable[ContextReference]) -> DagValidation:
    """Build the graph from references and validate it (pure, no audit)."""
    return ReferenceGraph.from_references(references).validate()
