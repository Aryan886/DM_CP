import pytest

from stockrelate.market_data import parse_metadata
from stockrelate.sets import (
    canonical_selection,
    format_set,
    is_subset,
    sector_subsets,
    set_difference,
    set_intersection,
    set_union,
)
from tests.conftest import fixture_bytes

S = {"A", "B", "C", "D", "E"}
X = {"A", "B", "C"}
Y = {"B", "D"}


@pytest.mark.parametrize(
    "result, expected",
    [
        (set_union(X, Y), {"A", "B", "C", "D"}),
        (set_intersection(X, Y), {"B"}),
        (set_difference(X, Y), {"A", "C"}),
        (set_difference(Y, X), {"D"}),
        (set_union(X, set()), X),
        (set_intersection(X, set()), set()),
    ],
)
def test_section_10_1_set_fixture(result, expected):
    assert result == expected


def test_subset_fixture():
    assert is_subset(X, Y) is False
    assert is_subset(X, S) is True
    assert is_subset(set(), X) is True
    assert is_subset(set(), set()) is True


@pytest.mark.parametrize(
    "first, second",
    [
        (set(), set()),
        ({"A"}, set()),
        (set(), {"A"}),
        ({"A", "B"}, {"C", "D"}),  # disjoint
        ({"A", "B"}, {"A", "B"}),  # equal
        (X, Y),  # overlap
    ],
)
def test_operations_agree_with_builtin_oracle(first, second):
    first_copy, second_copy = set(first), set(second)
    assert set_union(first, second) == first | second
    assert set_intersection(first, second) == first & second
    assert set_difference(first, second) == first - second
    assert is_subset(first, second) == (first <= second)
    assert first == first_copy and second == second_copy  # no mutation


def test_repeated_selection_collapses():
    assert canonical_selection(["B", "A", "B", "A"], S) == ("A", "B")


def test_unknown_identifier_rejected():
    with pytest.raises(ValueError, match="ZZZ"):
        canonical_selection(["A", "ZZZ"], S)


def test_empty_selection_is_allowed():
    assert canonical_selection([], S) == ()


def test_sector_subsets_partition_selection():
    metadata, _ = parse_metadata(fixture_bytes("metadata.csv"))
    sectors = dict(zip(metadata["symbol"], metadata["sector"], strict=True))
    subsets = sector_subsets(S, sectors)
    assert subsets == {"Automobile": ["E"], "Banking": ["A", "B"], "Technology": ["C", "D"]}
    members = [s for block in subsets.values() for s in block]
    assert sorted(members) == sorted(S)
    assert len(members) == len(set(members))
    assert sector_subsets(set(), sectors) == {}


def test_format_set():
    assert format_set({"B", "A"}) == "{A, B}"
    assert format_set(set()) == "∅"
