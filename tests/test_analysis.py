import csv
import io
import json

import pytest

from stockrelate.analysis import analyze, preset_comparison
from stockrelate.exports import all_exports
from stockrelate.graphs import find_shortest_path, warshall_closure
from stockrelate.market_data import load_bundled_dataset, prepare_market_data
from stockrelate.relations import count_true


def test_chain_is_not_transitive_but_reachability_is(teaching_data):
    result = analyze(teaching_data, 0.65)
    assert result.relation.edges == [("A", "B"), ("B", "C"), ("D", "E")]
    assert result.metrics["ordered_pair_count"] == 11
    assert result.direct_properties.reflexive.holds
    assert result.direct_properties.symmetric.holds
    assert not result.direct_properties.transitive.holds
    assert result.direct_properties.transitive.witness == ("A", "B", "C")
    assert result.components == [["A", "B", "C"], ["D", "E"]]
    assert result.closure_added_pairs == [("A", "C"), ("C", "A")]
    assert result.reachability_properties.is_equivalence
    assert find_shortest_path(result.relation.neighbors, "A", "C") == ["A", "B", "C"]
    assert find_shortest_path(result.relation.neighbors, "A", "D") is None
    assert find_shortest_path(result.relation.neighbors, "E", "E") == ["E"]


def test_teaching_metrics_at_065(teaching_data):
    result = analyze(teaching_data, 0.65)
    assert result.metrics == {
        "node_count": 5,
        "edge_count": 3,
        "ordered_pair_count": 11,
        "component_count": 2,
        "isolated_count": 0,
        "largest_component_size": 3,
        "density": 0.3,
    }
    assert {s: len(n) for s, n in result.relation.neighbors.items()} == {
        "A": 1,
        "B": 2,
        "C": 1,
        "D": 1,
        "E": 1,
    }
    assert count_true(result.reachability_matrix) == 13
    assert result.reachability_matrix == [
        [True, True, True, False, False],
        [True, True, True, False, False],
        [True, True, True, False, False],
        [False, False, False, True, True],
        [False, False, False, True, True],
    ]
    assert warshall_closure(result.reachability_matrix) == result.reachability_matrix


# Section 10.2 table
@pytest.mark.parametrize(
    "threshold, edges, pairs, transitive, components",
    [
        (0.00, 10, 25, True, [["A", "B", "C", "D", "E"]]),
        (0.20, 4, 13, True, [["A", "B", "C"], ["D", "E"]]),
        (0.65, 3, 11, False, [["A", "B", "C"], ["D", "E"]]),
        (0.70, 3, 11, False, [["A", "B", "C"], ["D", "E"]]),
        (0.75, 2, 9, True, [["A", "B"], ["C"], ["D", "E"]]),
        (0.90, 0, 5, True, [["A"], ["B"], ["C"], ["D"], ["E"]]),
        (1.00, 0, 5, True, [["A"], ["B"], ["C"], ["D"], ["E"]]),
    ],
)
def test_teaching_table(teaching_data, threshold, edges, pairs, transitive, components):
    result = analyze(teaching_data, threshold)
    assert result.metrics["edge_count"] == edges
    assert result.metrics["ordered_pair_count"] == pairs
    assert result.direct_properties.transitive.holds is transitive
    assert result.components == components
    assert result.metrics["component_count"] == len(components)
    assert result.metrics["largest_component_size"] == max(len(c) for c in components)


def test_preset_comparison_rows(teaching_data):
    rows = preset_comparison(teaching_data, (20, 65, 75, 90))
    assert [
        (r["edge_count"], r["direct_transitive"], r["component_count"], r["largest_component_size"])
        for r in rows
    ] == [(4, True, 2, 3), (3, False, 2, 3), (2, True, 3, 2), (0, True, 5, 1)]


def test_isolated_vertex_is_singleton_class(teaching_data):
    result = analyze(teaching_data, 0.75)
    assert ["C"] in result.components
    assert result.reachability_matrix[2][2] is True
    assert result.metrics["isolated_count"] == 1


def test_single_stock_density_convention():
    from stockrelate.market_data import prepare_teaching_data

    result = analyze(prepare_teaching_data(["A"]), 0.5)
    assert result.metrics["density"] == 0.0
    assert result.components == [["A"]]


def _sweep(prepared):
    previous = None
    for tick in range(101):
        result = analyze(prepared, tick / 100)
        if previous is not None:
            assert set(result.relation.edges) <= set(previous.relation.edges)
            assert result.metrics["component_count"] >= previous.metrics["component_count"]
            for component in result.components:  # refinement
                assert any(set(component) <= set(old) for old in previous.components)
            for i, row in enumerate(result.reachability_matrix):
                for j, cell in enumerate(row):
                    if cell:
                        assert previous.reachability_matrix[i][j]
        previous = result


def test_threshold_sweep_teaching(teaching_data):
    _sweep(teaching_data)


def test_threshold_sweep_bundled():
    dataset = load_bundled_dataset()
    _sweep(prepare_market_data(dataset, dataset.universe, "2025-01-01", "2025-12-31"))


def test_analysis_does_not_mutate_prepared(teaching_data):
    original = [row.copy() for row in teaching_data.correlation]
    analyze(teaching_data, 0.65)
    assert teaching_data.correlation == original


def test_analysis_id_depends_on_threshold(teaching_data):
    assert analyze(teaching_data, 0.65).analysis_id != analyze(teaching_data, 0.66).analysis_id
    assert analyze(teaching_data, 0.65).analysis_id == analyze(teaching_data, 0.65).analysis_id


def test_exports_round_trip(teaching_data):
    result = analyze(teaching_data, 0.65)
    files = all_exports(result, teaching_data)
    assert set(files) == {
        "relation_matrix.csv",
        "reachability_matrix.csv",
        "ordered_pairs.csv",
        "edges.csv",
        "components.csv",
        "analysis.json",
    }

    def rows(name):
        return list(csv.reader(io.StringIO(files[name][0])))

    relation_rows = rows("relation_matrix.csv")
    assert relation_rows[0] == ["symbol", "A", "B", "C", "D", "E"]
    assert [[c == "1" for c in r[1:]] for r in relation_rows[1:]] == result.relation.matrix
    reach_rows = rows("reachability_matrix.csv")
    assert [[c == "1" for c in r[1:]] for r in reach_rows[1:]] == result.reachability_matrix
    assert len(rows("ordered_pairs.csv")) == 1 + 11
    assert ["A", "A"] in rows("ordered_pairs.csv")
    edges = rows("edges.csv")
    assert edges[0] == ["source", "target", "correlation"]
    assert [(a, b, float(c)) for a, b, c in edges[1:]] == [("A", "B", 0.8), ("B", "C", 0.7), ("D", "E", 0.85)]
    assert rows("components.csv")[1:] == [["A", "A"], ["A", "B"], ["A", "C"], ["D", "D"], ["D", "E"]]

    def reject_constant(value):
        raise ValueError(f"non-finite JSON constant {value}")

    payload = json.loads(files["analysis.json"][0], parse_constant=reject_constant)
    assert payload["schema_version"] == 1
    assert payload["threshold"] == 0.65
    assert payload["data_id"] == teaching_data.data_id
    assert payload["components"] == result.components
    assert payload["relation_matrix"] == result.relation.matrix
    assert payload["direct_properties"]["transitive"] == {"holds": False, "witness": ["A", "B", "C"]}
    assert payload["sample_count"] is None


def test_exports_reject_mismatched_prepared(teaching_data):
    from stockrelate.market_data import prepare_teaching_data

    result = analyze(teaching_data, 0.65)
    with pytest.raises(ValueError):
        all_exports(result, prepare_teaching_data(["A", "B"]))


def test_matches_committed_expected_analysis(teaching_data):
    from tests.conftest import FIXTURES

    expected = json.loads((FIXTURES / "expected_analysis.json").read_text(encoding="utf-8"))
    result = analyze(teaching_data, expected["threshold"])
    assert [list(e) for e in result.relation.edges] == expected["edges"]
    assert [[int(c) for c in row] for row in result.relation.matrix] == expected["relation_matrix"]
    assert [[int(c) for c in row] for row in result.reachability_matrix] == expected["reachability_matrix"]
    assert list(result.direct_properties.transitive.witness) == expected["direct_transitive_witness"]
    assert result.components == expected["components"]
    assert [list(p) for p in result.closure_added_pairs] == expected["closure_added_pairs"]
    assert result.metrics == expected["metrics"]
    assert {s: len(n) for s, n in result.relation.neighbors.items()} == expected["degrees"]
