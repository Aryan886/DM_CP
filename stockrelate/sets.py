"""Manual set operations for Module 1.

Python ``set`` objects are used for storage, but union, intersection,
difference and subset are written as explicit loops with membership checks
so that the definitions are visible.
"""

from collections.abc import Iterable


def set_union(first: Iterable[str], second: Iterable[str]) -> set[str]:
    """X ∪ Y = {x : x ∈ X or x ∈ Y}."""
    result: set[str] = set()
    for element in first:
        result.add(element)
    for element in second:
        if element not in result:
            result.add(element)
    return result


def set_intersection(first: Iterable[str], second: Iterable[str]) -> set[str]:
    """X ∩ Y = {x : x ∈ X and x ∈ Y}."""
    second_members = set(second)
    result: set[str] = set()
    for element in first:
        if element in second_members:
            result.add(element)
    return result


def set_difference(first: Iterable[str], second: Iterable[str]) -> set[str]:
    """X \\ Y = {x : x ∈ X and x ∉ Y}."""
    second_members = set(second)
    result: set[str] = set()
    for element in first:
        if element not in second_members:
            result.add(element)
    return result


def is_subset(first: Iterable[str], second: Iterable[str]) -> bool:
    """X ⊆ Y iff every x ∈ X is also in Y. The empty set is a subset of every set."""
    second_members = set(second)
    for element in first:
        if element not in second_members:
            return False
    return True


def sorted_members(members: Iterable[str]) -> list[str]:
    """Lexicographic display order. Sorting is a display convention only."""
    return sorted(set(members))


def format_set(members: Iterable[str]) -> str:
    """Render a set as ``{A, B}`` or ``∅`` in canonical display order."""
    ordered = sorted_members(members)
    if not ordered:
        return "∅"
    return "{" + ", ".join(ordered) + "}"


def canonical_selection(selected: Iterable[str], universe: Iterable[str]) -> tuple[str, ...]:
    """Collapse duplicates, check S ⊆ U and return the canonical sorted tuple.

    Raises ValueError naming every identifier that is not in the universe.
    """
    universe_members = set(universe)
    members: set[str] = set()
    unknown: list[str] = []
    for symbol in selected:
        if not isinstance(symbol, str):
            raise ValueError(f"Stock identifiers must be strings, got {symbol!r}.")
        if symbol not in universe_members:
            if symbol not in unknown:
                unknown.append(symbol)
            continue
        members.add(symbol)
    if unknown:
        raise ValueError("Unknown stock identifier(s) not in the universe U: " + ", ".join(sorted(unknown)))
    return tuple(sorted(members))


def sector_subsets(selected: Iterable[str], sectors: dict[str, str]) -> dict[str, list[str]]:
    """S_k = S ∩ U_k for every sector k that has at least one selected stock.

    Each stock has exactly one sector, so the nonempty subsets partition S.
    """
    subsets: dict[str, list[str]] = {}
    for symbol in sorted_members(selected):
        if symbol not in sectors:
            raise ValueError(f"Stock identifier {symbol!r} has no sector metadata.")
        sector = sectors[symbol]
        if sector not in subsets:
            subsets[sector] = []
        subsets[sector].append(symbol)
    return dict(sorted(subsets.items()))
