"""Threshold relation construction and generic relation-property checks.

Rτ = {(a, b) ∈ S × S : C[a][b] >= τ}   (signed, inclusive, unrounded)
"""

import math
from numbers import Real

from stockrelate.models import (
    BoolMatrix,
    FloatMatrix,
    Pair,
    PropertyReport,
    PropertyResult,
    RelationResult,
    Symbols,
)


def validate_threshold(threshold: object) -> float:
    """Accept a finite real number in [0, 1]. Booleans and strings are rejected."""
    if isinstance(threshold, bool) or not isinstance(threshold, Real):
        raise ValueError(f"Threshold must be a real number in [0, 1], got {threshold!r}.")
    value = float(threshold)
    if not math.isfinite(value):
        raise ValueError(f"Threshold must be finite, got {threshold!r}.")
    if value < 0.0 or value > 1.0:
        raise ValueError(f"Threshold must lie in [0, 1], got {value}.")
    return value


def validate_symbols(symbols: object) -> Symbols:
    """Symbols must be a tuple of unique strings."""
    if not isinstance(symbols, tuple):
        raise ValueError("Symbols must be given as a tuple of identifiers.")
    seen: set[str] = set()
    for symbol in symbols:
        if not isinstance(symbol, str) or not symbol:
            raise ValueError(f"Invalid stock identifier {symbol!r}.")
        if symbol in seen:
            raise ValueError(f"Duplicate stock identifier {symbol!r} in symbols.")
        seen.add(symbol)
    return symbols


def validate_canonical_symbols(symbols: object) -> Symbols:
    """Canonical symbols are unique and in lexicographic order."""
    validated = validate_symbols(symbols)
    if list(validated) != sorted(validated):
        raise ValueError("Symbols must be in canonical (sorted) order.")
    return validated


def validate_boolean_matrix(symbols: Symbols, matrix: object) -> BoolMatrix:
    """A square list-of-lists of real booleans whose size matches the labels."""
    size = len(symbols)
    if not isinstance(matrix, list) or len(matrix) != size:
        raise ValueError(f"Relation matrix must be a list of {size} rows.")
    for i, row in enumerate(matrix):
        if not isinstance(row, list) or len(row) != size:
            raise ValueError(f"Row {i} of the relation matrix must have {size} entries.")
        for j, value in enumerate(row):
            if not isinstance(value, bool):
                raise ValueError(f"Relation matrix cell ({i}, {j}) is not a boolean: {value!r}.")
    return matrix


def validate_correlation_matrix(symbols: Symbols, correlation: object) -> FloatMatrix:
    """Check shape, finiteness, range, exact symmetry and unit diagonal.

    Correlations must already be canonicalized (see market_data), so exact
    symmetry and an exact diagonal of 1.0 are required here.
    """
    size = len(symbols)
    if not isinstance(correlation, list) or len(correlation) != size:
        raise ValueError(f"Correlation matrix must be a list of {size} rows.")
    for i, row in enumerate(correlation):
        if not isinstance(row, list) or len(row) != size:
            raise ValueError(f"Row {i} of the correlation matrix must have {size} entries.")
        for j, value in enumerate(row):
            label = f"({symbols[i]}, {symbols[j]})"
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"Correlation {label} is not a number: {value!r}.")
            if not math.isfinite(value):
                raise ValueError(f"Correlation {label} is undefined or infinite: {value!r}.")
            if value < -1.0 or value > 1.0:
                raise ValueError(f"Correlation {label} = {value!r} lies outside [-1, 1].")
    for i in range(size):
        if correlation[i][i] != 1.0:
            raise ValueError(
                f"Correlation diagonal ({symbols[i]}, {symbols[i]}) = "
                f"{correlation[i][i]!r}; expected exactly 1."
            )
        for j in range(i + 1, size):
            if correlation[i][j] != correlation[j][i]:
                raise ValueError(
                    f"Correlation matrix is not symmetric at ({symbols[i]}, {symbols[j]}): "
                    f"{correlation[i][j]!r} vs {correlation[j][i]!r}."
                )
    return correlation


def build_relation(symbols: Symbols, correlation: FloatMatrix, threshold: object) -> RelationResult:
    """Build Rτ, its Boolean matrix, ordered pairs, undirected edges and neighbors."""
    from stockrelate.graphs import build_neighbors

    symbols = validate_canonical_symbols(symbols)
    validate_correlation_matrix(symbols, correlation)
    tau = validate_threshold(threshold)
    size = len(symbols)

    # Independent rows; never [[False] * n] * n.
    matrix = [[False for _ in range(size)] for _ in range(size)]
    for i in range(size):
        matrix[i][i] = correlation[i][i] >= tau
        for j in range(i + 1, size):
            is_related = correlation[i][j] >= tau
            matrix[i][j] = is_related
            matrix[j][i] = is_related

    ordered_pairs: list[Pair] = []
    edges: list[Pair] = []
    for i in range(size):
        for j in range(size):
            if matrix[i][j]:
                ordered_pairs.append((symbols[i], symbols[j]))
                if i < j:
                    edges.append((symbols[i], symbols[j]))

    return RelationResult(
        threshold=tau,
        symbols=symbols,
        matrix=matrix,
        ordered_pairs=ordered_pairs,
        edges=edges,
        neighbors=build_neighbors(symbols, matrix),
    )


def check_reflexive(symbols: Symbols, matrix: BoolMatrix) -> PropertyResult:
    """∀a ∈ S: aRa. Witness: (a,) with A[a][a] false."""
    for i in range(len(symbols)):
        if not matrix[i][i]:
            return PropertyResult(holds=False, witness=(symbols[i],))
    return PropertyResult(holds=True, witness=None)


def check_symmetric(symbols: Symbols, matrix: BoolMatrix) -> PropertyResult:
    """∀a,b: aRb ⇒ bRa. Witness: (a, b) with aRb and not bRa."""
    size = len(symbols)
    for i in range(size):
        for j in range(size):
            if matrix[i][j] and not matrix[j][i]:
                return PropertyResult(holds=False, witness=(symbols[i], symbols[j]))
    return PropertyResult(holds=True, witness=None)


def check_transitive(symbols: Symbols, matrix: BoolMatrix) -> PropertyResult:
    """∀a,b,c: (aRb ∧ bRc) ⇒ aRc. Repeated indices are allowed.

    Witness: (a, b, c) with aRb, bRc and not aRc.
    """
    size = len(symbols)
    for i in range(size):
        for j in range(size):
            if not matrix[i][j]:
                continue
            for k in range(size):
                if matrix[j][k] and not matrix[i][k]:
                    return PropertyResult(holds=False, witness=(symbols[i], symbols[j], symbols[k]))
    return PropertyResult(holds=True, witness=None)


def analyze_properties(symbols: Symbols, matrix: BoolMatrix) -> PropertyReport:
    """Generic analyzer for any square Boolean relation matrix (directed allowed)."""
    symbols = validate_symbols(symbols)
    validate_boolean_matrix(symbols, matrix)
    return PropertyReport(
        reflexive=check_reflexive(symbols, matrix),
        symmetric=check_symmetric(symbols, matrix),
        transitive=check_transitive(symbols, matrix),
    )


def count_transitivity_premises(matrix: BoolMatrix) -> int:
    """Number of triples (a, b, c) with aRb and bRc, i.e. premises checked."""
    size = len(matrix)
    total = 0
    for i in range(size):
        for j in range(size):
            if not matrix[i][j]:
                continue
            for k in range(size):
                if matrix[j][k]:
                    total += 1
    return total


def count_true(matrix: BoolMatrix) -> int:
    total = 0
    for row in matrix:
        for value in row:
            if value:
                total += 1
    return total


def matrix_pairs(symbols: Symbols, matrix: BoolMatrix) -> list[Pair]:
    """All ordered pairs (a, b) with matrix[a][b] true, in row-major canonical order."""
    pairs: list[Pair] = []
    for i in range(len(symbols)):
        for j in range(len(symbols)):
            if matrix[i][j]:
                pairs.append((symbols[i], symbols[j]))
    return pairs


def is_entrywise_subset(smaller: BoolMatrix, larger: BoolMatrix) -> bool:
    """R ⊆ Q as sets of ordered pairs, compared entrywise."""
    if len(smaller) != len(larger):
        return False
    for i in range(len(smaller)):
        for j in range(len(smaller)):
            if smaller[i][j] and not larger[i][j]:
                return False
    return True
