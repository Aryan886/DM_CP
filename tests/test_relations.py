import itertools
import math

import pytest

from stockrelate.relations import analyze_properties, build_relation

TEACHING = ("A", "B", "C", "D", "E")


def test_teaching_relation_at_065(teaching_data):
    relation = build_relation(teaching_data.symbols, teaching_data.correlation, 0.65)
    assert relation.edges == [("A", "B"), ("B", "C"), ("D", "E")]
    assert len(relation.ordered_pairs) == 11
    assert relation.matrix[0][0] is True
    for a, b in relation.edges:
        assert (a, b) in relation.ordered_pairs and (b, a) in relation.ordered_pairs
    assert relation.matrix == [
        [True, True, False, False, False],
        [True, True, True, False, False],
        [False, True, True, False, False],
        [False, False, False, True, True],
        [False, False, False, True, True],
    ]


def test_threshold_is_inclusive_without_rounding(teaching_data):
    at_boundary = build_relation(teaching_data.symbols, teaching_data.correlation, 0.70)
    above_boundary = build_relation(teaching_data.symbols, teaching_data.correlation, 0.700000000001)
    assert ("B", "C") in at_boundary.edges
    assert ("B", "C") not in above_boundary.edges


def test_zero_threshold_includes_exact_zero_but_not_negative():
    correlation = [[1.0, 0.0, -0.5], [0.0, 1.0, -0.1], [-0.5, -0.1, 1.0]]
    relation = build_relation(("A", "B", "C"), correlation, 0.0)
    assert relation.edges == [("A", "B")]
    assert relation.neighbors["C"] == ()


def test_perfectly_correlated_distinct_stocks_remain_at_one():
    result = build_relation(("A", "B"), [[1.0, 1.0], [1.0, 1.0]], 1.0)
    assert result.edges == [("A", "B")]


@pytest.mark.parametrize("threshold", [-0.01, 1.01, float("nan"), float("inf"), True, "0.65", None])
def test_invalid_thresholds_fail(teaching_data, threshold):
    with pytest.raises(ValueError):
        build_relation(teaching_data.symbols, teaching_data.correlation, threshold)


def test_integer_bounds_accepted(teaching_data):
    assert build_relation(teaching_data.symbols, teaching_data.correlation, 0).threshold == 0.0
    assert build_relation(teaching_data.symbols, teaching_data.correlation, 1).threshold == 1.0


@pytest.mark.parametrize(
    "correlation",
    [
        [[1.0, 0.5], [0.4, 1.0]],  # asymmetric
        [[1.0, math.nan], [math.nan, 1.0]],  # undefined
        [[1.0, 1.5], [1.5, 1.0]],  # out of range
        [[0.9, 0.5], [0.5, 1.0]],  # bad diagonal
        [[1.0, 0.5]],  # wrong shape
    ],
)
def test_invalid_correlation_rejected(correlation):
    with pytest.raises(ValueError):
        build_relation(("A", "B"), correlation, 0.5)


def test_non_canonical_symbols_rejected():
    with pytest.raises(ValueError):
        build_relation(("B", "A"), [[1.0, 0.5], [0.5, 1.0]], 0.5)


def test_representation_counts_and_isolates():
    correlation = [[1.0, 0.9, 0.1], [0.9, 1.0, 0.1], [0.1, 0.1, 1.0]]
    relation = build_relation(("A", "B", "C"), correlation, 0.5)
    n, m = 3, len(relation.edges)
    assert len(relation.ordered_pairs) == n + 2 * m
    assert sum(len(v) for v in relation.neighbors.values()) == 2 * m
    assert relation.neighbors["C"] == ()
    assert ("C", "C") in relation.ordered_pairs


def test_matrix_rows_are_independent(teaching_data):
    relation = build_relation(teaching_data.symbols, teaching_data.correlation, 0.65)
    relation.matrix[0][4] = True
    assert relation.matrix[1][4] is False


# Section 10.4 generic relation fixtures
@pytest.mark.parametrize(
    "symbols, matrix, expected",
    [
        ((), [], (True, True, True)),
        (("A",), [[True]], (True, True, True)),
        (("A", "B"), [[False, False], [False, False]], (False, True, True)),
        (("A", "B"), [[True, True], [False, True]], (True, False, True)),
        (("A", "B"), [[False, True], [True, False]], (False, True, False)),
        (("A", "B", "C"), [[True] * 3 for _ in range(3)], (True, True, True)),
        (
            ("A", "B", "C"),
            [[True, False, False], [False, True, False], [False, False, True]],
            (True, True, True),
        ),
    ],
)
def test_generic_relation_fixtures(symbols, matrix, expected):
    report = analyze_properties(symbols, matrix)
    assert (report.reflexive.holds, report.symmetric.holds, report.transitive.holds) == expected
    for result in (report.reflexive, report.symmetric, report.transitive):
        if result.holds:
            assert result.witness is None


def test_teaching_chain_properties(teaching_data):
    relation = build_relation(teaching_data.symbols, teaching_data.correlation, 0.65)
    report = analyze_properties(relation.symbols, relation.matrix)
    assert report.reflexive.holds and report.symmetric.holds
    assert not report.transitive.holds
    assert report.transitive.witness == ("A", "B", "C")


def test_transitivity_must_allow_repeated_vertices():
    report = analyze_properties(("A", "B"), [[False, True], [True, False]])
    assert not report.reflexive.holds
    assert report.reflexive.witness == ("A",)
    assert report.symmetric.holds
    assert report.transitive.witness == ("A", "B", "A")


def test_symmetry_witness():
    report = analyze_properties(("A", "B"), [[True, True], [False, True]])
    assert report.symmetric.witness == ("A", "B")


def test_matrix_validation():
    with pytest.raises(ValueError):
        analyze_properties(("A", "B"), [[True, 1], [False, True]])
    with pytest.raises(ValueError):
        analyze_properties(("A", "B"), [[True, True]])


def test_analyze_properties_does_not_mutate():
    matrix = [[False, True], [True, False]]
    original = [row.copy() for row in matrix]
    analyze_properties(("A", "B"), matrix)
    assert matrix == original


def _oracle(pairs: set, carrier: tuple) -> tuple[bool, bool, bool]:
    reflexive = all((a, a) in pairs for a in carrier)
    symmetric = all((b, a) in pairs for a, b in pairs)
    composition = {(a, d) for a, b in pairs for c, d in pairs if b == c}
    return reflexive, symmetric, composition <= pairs


def test_all_512_relations_on_three_vertices():
    carrier = ("A", "B", "C")
    cells = list(itertools.product(range(3), range(3)))
    for mask in range(2**9):
        matrix = [[False] * 3 for _ in range(3)]
        pairs = set()
        for bit, (i, j) in enumerate(cells):
            if mask >> bit & 1:
                matrix[i][j] = True
                pairs.add((carrier[i], carrier[j]))
        report = analyze_properties(carrier, matrix)
        expected = _oracle(pairs, carrier)
        assert (report.reflexive.holds, report.symmetric.holds, report.transitive.holds) == expected
        # Every failure witness satisfies its formal failure condition.
        if not report.reflexive.holds:
            (a,) = report.reflexive.witness
            assert (a, a) not in pairs
        if not report.symmetric.holds:
            a, b = report.symmetric.witness
            assert (a, b) in pairs and (b, a) not in pairs
        if not report.transitive.holds:
            a, b, c = report.transitive.witness
            assert (a, b) in pairs and (b, c) in pairs and (a, c) not in pairs
