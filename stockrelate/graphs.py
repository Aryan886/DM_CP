"""Adjacency lists, BFS components and paths, and Warshall closure.

All traversal and closure logic is implemented manually; no graph library.
"""

from collections import deque

from stockrelate.models import BoolMatrix, Symbols
from stockrelate.relations import validate_boolean_matrix, validate_symbols

Neighbors = dict[str, tuple[str, ...]]


def build_neighbors(symbols: Symbols, matrix: BoolMatrix) -> Neighbors:
    """Undirected neighbor lists from off-diagonal true entries.

    Every symbol gets an entry, including isolated vertices. Asymmetric input
    is rejected because this application treats the graph as undirected.
    """
    symbols = validate_symbols(symbols)
    validate_boolean_matrix(symbols, matrix)
    size = len(symbols)
    neighbors: Neighbors = {}
    for i in range(size):
        adjacent: list[str] = []
        for j in range(size):
            if matrix[i][j] != matrix[j][i]:
                raise ValueError(
                    "Undirected graph requires a symmetric matrix; "
                    f"({symbols[i]}, {symbols[j]}) is {matrix[i][j]} but "
                    f"({symbols[j]}, {symbols[i]}) is {matrix[j][i]}."
                )
            if i != j and matrix[i][j]:
                adjacent.append(symbols[j])
        neighbors[symbols[i]] = tuple(sorted(adjacent))
    return neighbors


def validate_neighbors(neighbors: Neighbors) -> None:
    """Check an externally constructed mapping: known, reciprocal, loop-free."""
    if not isinstance(neighbors, dict):
        raise ValueError("Neighbors must be a dict mapping each symbol to its neighbors.")
    for vertex, adjacent in neighbors.items():
        if list(adjacent) != sorted(adjacent):
            raise ValueError(f"Neighbors of {vertex!r} must be sorted.")
        for other in adjacent:
            if other not in neighbors:
                raise ValueError(f"Neighbor {other!r} of {vertex!r} is not a known vertex.")
            if other == vertex:
                raise ValueError(f"Self-loop at {vertex!r} is not allowed in neighbor lists.")
            if vertex not in neighbors[other]:
                raise ValueError(
                    f"Edge {vertex!r}–{other!r} has no reciprocal entry; graph is not undirected."
                )


def find_components(neighbors: Neighbors) -> list[list[str]]:
    """Connected components by BFS, in canonical order of smallest member.

    Vertices are marked visited when enqueued, so none is enqueued twice.
    Singleton components are kept.
    """
    validate_neighbors(neighbors)
    visited: set[str] = set()
    components: list[list[str]] = []
    for start in sorted(neighbors):
        if start in visited:
            continue
        component: list[str] = []
        queue: deque[str] = deque([start])
        visited.add(start)
        while queue:
            vertex = queue.popleft()
            component.append(vertex)
            for other in neighbors[vertex]:
                if other not in visited:
                    visited.add(other)
                    queue.append(other)
        components.append(sorted(component))
    return components


def find_shortest_path(neighbors: Neighbors, start: str, end: str) -> list[str] | None:
    """Fewest-edge path from start to end using BFS with a parent map.

    Returns [start] when start == end (a length-zero path), None when end is
    unreachable, and raises ValueError for unknown endpoints.
    """
    validate_neighbors(neighbors)
    for endpoint in (start, end):
        if endpoint not in neighbors:
            raise ValueError(f"Unknown stock identifier {endpoint!r} for path search.")
    if start == end:
        return [start]
    parent: dict[str, str | None] = {start: None}
    queue: deque[str] = deque([start])
    while queue:
        vertex = queue.popleft()
        for other in neighbors[vertex]:
            if other in parent:
                continue
            parent[other] = vertex
            if other == end:
                path = [end]
                step = parent[end]
                while step is not None:
                    path.append(step)
                    step = parent[step]
                path.reverse()
                return path
            queue.append(other)
    return None


def warshall_closure(matrix: BoolMatrix) -> BoolMatrix:
    """Reflexive-transitive closure (I ∪ R)* by Warshall's algorithm.

    Works for directed matrices. The intermediate vertex k is the outer loop.
    The input matrix is not modified.
    """
    size = len(matrix)
    validate_boolean_matrix(tuple(str(i) for i in range(size)), matrix)
    closure = [list(row) for row in matrix]
    for i in range(size):
        closure[i][i] = True
    for k in range(size):
        for i in range(size):
            for j in range(size):
                closure[i][j] = closure[i][j] or (closure[i][k] and closure[k][j])
    return closure


def degrees(neighbors: Neighbors) -> dict[str, int]:
    """Vertex degree excluding self-loops."""
    return {vertex: len(adjacent) for vertex, adjacent in neighbors.items()}
