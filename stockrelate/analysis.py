"""Pure orchestration of the DM pipeline plus independent consistency checks.

This module only coordinates functions from relations.py and graphs.py; it
does not reimplement BFS, Warshall or correlation.
"""

import json

from stockrelate.config import ALGORITHM_CONTRACT_VERSION
from stockrelate.graphs import find_components, warshall_closure
from stockrelate.market_data import sha256_text
from stockrelate.models import AnalysisResult, BoolMatrix, PreparedData, RelationResult
from stockrelate.relations import (
    analyze_properties,
    build_relation,
    count_true,
    is_entrywise_subset,
    validate_threshold,
)


class AnalysisInvariantError(RuntimeError):
    """A production invariant failed; indicates a defect, not bad user input."""


def analysis_key(data_id: str, threshold: float) -> str:
    """Stable digest of prepared data, threshold and algorithm contract."""
    payload = {"data_id": data_id, "threshold": repr(float(threshold)), "algo": ALGORITHM_CONTRACT_VERSION}
    return sha256_text(json.dumps(payload, sort_keys=True))


def compute_metrics(relation: RelationResult, components: list[list[str]]) -> dict:
    node_count = len(relation.symbols)
    edge_count = len(relation.edges)
    if node_count >= 2:
        density = 2 * edge_count / (node_count * (node_count - 1))
    else:
        density = 0.0  # convention: no possible edges
    isolated_count = 0
    for symbol in relation.symbols:
        if not relation.neighbors[symbol]:
            isolated_count += 1
    largest = 0
    for component in components:
        largest = max(largest, len(component))
    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "ordered_pair_count": len(relation.ordered_pairs),
        "component_count": len(components),
        "isolated_count": isolated_count,
        "largest_component_size": largest,
        "density": float(density),
    }


def check_consistency(
    relation: RelationResult,
    reachability: BoolMatrix,
    reachability_is_equivalence: bool,
    components: list[list[str]],
) -> None:
    """Section 7.6 checks. Raise AnalysisInvariantError instead of using assert."""
    symbols = relation.symbols
    size = len(symbols)

    # |Rτ| = n + 2m and sum(degrees) = 2m (valid data makes Rτ reflexive).
    if relation.matrix and all(relation.matrix[i][i] for i in range(size)):
        if len(relation.ordered_pairs) != size + 2 * len(relation.edges):
            raise AnalysisInvariantError("Ordered-pair count differs from n + 2m.")
    degree_sum = sum(len(adjacent) for adjacent in relation.neighbors.values())
    if degree_sum != 2 * len(relation.edges):
        raise AnalysisInvariantError("Sum of degrees differs from twice the edge count.")

    # 1. Rτ ⊆ Qτ.
    if not is_entrywise_subset(relation.matrix, reachability):
        raise AnalysisInvariantError("Direct relation is not contained in reachability.")
    # 2. Qτ is an equivalence relation.
    if not reachability_is_equivalence:
        raise AnalysisInvariantError("Reachability relation is not an equivalence relation.")
    # 4. Partition: nonempty, disjoint, covering S.
    owner: dict[str, int] = {}
    for index, component in enumerate(components):
        if not component:
            raise AnalysisInvariantError("Empty component found.")
        for symbol in component:
            if symbol in owner:
                raise AnalysisInvariantError(f"{symbol} appears in more than one component.")
            owner[symbol] = index
    if set(owner) != set(symbols):
        raise AnalysisInvariantError("Components do not cover the selected set exactly.")
    # 3. Qτ[i][j] iff same BFS component (BFS and Warshall are independent methods).
    for i in range(size):
        for j in range(size):
            same = owner[symbols[i]] == owner[symbols[j]]
            if reachability[i][j] != same:
                raise AnalysisInvariantError(f"BFS and Warshall disagree on ({symbols[i]}, {symbols[j]}).")
    # 5. Σ|class|² equals the number of true cells in Qτ.
    if sum(len(component) ** 2 for component in components) != count_true(reachability):
        raise AnalysisInvariantError("Class sizes do not match reachability cell count.")


def analyze(prepared: PreparedData, threshold: object) -> AnalysisResult:
    """Build Rτ, check its properties, compute Qτ and its classes, verify consistency."""
    tau = validate_threshold(threshold)
    relation = build_relation(prepared.symbols, prepared.correlation, tau)
    direct_properties = analyze_properties(relation.symbols, relation.matrix)
    components = find_components(relation.neighbors)
    reachability = warshall_closure(relation.matrix)
    reachability_properties = analyze_properties(relation.symbols, reachability)
    check_consistency(relation, reachability, reachability_properties.is_equivalence, components)

    closure_added_pairs = []
    for i, source in enumerate(relation.symbols):
        for j, target in enumerate(relation.symbols):
            if reachability[i][j] and not relation.matrix[i][j]:
                closure_added_pairs.append((source, target))

    return AnalysisResult(
        analysis_id=analysis_key(prepared.data_id, tau),
        data_id=prepared.data_id,
        relation=relation,
        direct_properties=direct_properties,
        reachability_matrix=reachability,
        reachability_properties=reachability_properties,
        components=components,
        metrics=compute_metrics(relation, components),
        closure_added_pairs=closure_added_pairs,
    )


def preset_comparison(prepared: PreparedData, ticks) -> list[dict]:
    """Fixed comparison rows for preset thresholds on the same prepared matrix."""
    rows = []
    for tick in ticks:
        result = analyze(prepared, tick / 100)
        rows.append(
            {
                "threshold": tick / 100,
                "edge_count": result.metrics["edge_count"],
                "direct_transitive": result.direct_properties.transitive.holds,
                "component_count": result.metrics["component_count"],
                "largest_component_size": result.metrics["largest_component_size"],
            }
        )
    return rows
