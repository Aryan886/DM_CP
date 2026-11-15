import itertools

import pytest

from stockrelate.graphs import build_neighbors, find_components, find_shortest_path, warshall_closure
from stockrelate.relations import analyze_properties, count_true


def matrix_from_edges(symbols, edges):
    index = {s: i for i, s in enumerate(symbols)}
    matrix = [[i == j for j in range(len(symbols))] for i in range(len(symbols))]
    for a, b in edges:
        matrix[index[a]][index[b]] = True
        matrix[index[b]][index[a]] = True
    return matrix


def neighbors_from_edges(symbols, edges):
    return build_neighbors(symbols, matrix_from_edges(symbols, edges))


def test_closure_adds_identity_without_mutating_input():
    matrix = [[False, True, False], [False, False, True], [False, False, False]]
    original = [row.copy() for row in matrix]
    closure = warshall_closure(matrix)
    assert closure == [[True, True, True], [False, True, True], [False, False, True]]
    assert matrix == original
    assert warshall_closure(closure) == closure


def test_directed_chain_does_not_invent_reverse_and_is_rejected_as_undirected():
    matrix = [[False, True, False], [False, False, True], [False, False, False]]
    closure = warshall_closure(matrix)
    assert closure[0][2] is True and closure[2][0] is False
    with pytest.raises(ValueError, match="symmetric"):
        build_neighbors(("A", "B", "C"), matrix)


@pytest.mark.parametrize(
    "matrix, expected",
    [
        ([], []),
        ([[False]], [[True]]),
        ([[True, False], [False, True]], [[True, False], [False, True]]),
        ([[True] * 3 for _ in range(3)], [[True] * 3 for _ in range(3)]),
    ],
)
def test_closure_edge_cases(matrix, expected):
    assert warshall_closure(matrix) == expected


@pytest.mark.parametrize(
    "symbols, edges, components",
    [
        (("A", "B", "C", "D"), [("A", "B"), ("B", "C"), ("C", "D")], [["A", "B", "C", "D"]]),  # chain
        (("A", "B", "C"), [("A", "B"), ("B", "C"), ("A", "C")], [["A", "B", "C"]]),  # cycle/clique
        (("A", "B", "C", "D"), [("A", "B"), ("C", "D")], [["A", "B"], ["C", "D"]]),  # disjoint
        (("A", "B", "C"), [("B", "C")], [["A"], ["B", "C"]]),  # isolate
        (("A",), [], [["A"]]),  # singleton
        ((), [], []),
    ],
)
def test_components(symbols, edges, components):
    assert find_components(neighbors_from_edges(symbols, edges)) == components


def test_paths():
    neighbors = neighbors_from_edges(("A", "B", "C", "D", "E"), [("A", "B"), ("B", "C"), ("D", "E")])
    assert find_shortest_path(neighbors, "A", "C") == ["A", "B", "C"]
    assert find_shortest_path(neighbors, "C", "A") == ["C", "B", "A"]
    assert find_shortest_path(neighbors, "A", "D") is None
    assert find_shortest_path(neighbors, "E", "E") == ["E"]
    with pytest.raises(ValueError, match="ZZ"):
        find_shortest_path(neighbors, "A", "ZZ")


def test_shortest_path_in_cycle_uses_fewest_edges():
    neighbors = neighbors_from_edges(
        ("A", "B", "C", "D", "E"), [("A", "B"), ("B", "C"), ("C", "D"), ("D", "E"), ("E", "A")]
    )
    assert find_shortest_path(neighbors, "A", "D") == ["A", "E", "D"]


def test_external_neighbor_mapping_validation():
    with pytest.raises(ValueError):
        find_components({"A": ("B",), "B": ()})  # missing reciprocal
    with pytest.raises(ValueError):
        find_components({"A": ("Z",)})  # unknown neighbor


def test_exhaustive_four_vertex_graphs():
    symbols = ("A", "B", "C", "D")
    possible = list(itertools.combinations(symbols, 2))
    for mask in range(2 ** len(possible)):
        edges = [edge for bit, edge in enumerate(possible) if mask >> bit & 1]
        matrix = matrix_from_edges(symbols, edges)
        components = find_components(build_neighbors(symbols, matrix))
        closure = warshall_closure(matrix)

        owner = {s: k for k, block in enumerate(components) for s in block}
        assert sorted(owner) == list(symbols)
        assert sum(len(block) for block in components) == len(symbols)
        for i, a in enumerate(symbols):
            for j, b in enumerate(symbols):
                assert closure[i][j] == (owner[a] == owner[b])
                if matrix[i][j]:
                    assert closure[i][j]
        assert warshall_closure(closure) == closure
        assert count_true(closure) == sum(len(block) ** 2 for block in components)
        degree_zero = [s for s in symbols if not any(s in e for e in edges)]
        for s in degree_zero:
            assert [s] in components

        # Reflexive symmetric relation is transitive iff each component is complete.
        index = {s: i for i, s in enumerate(symbols)}
        complete = all(matrix[index[a]][index[b]] for block in components for a in block for b in block)
        assert analyze_properties(symbols, matrix).transitive.holds == complete
        assert analyze_properties(symbols, closure).is_equivalence
