"""Streamlit render functions for the five modules.

These functions only translate structured results into tables, charts and
explanations. They contain no DM algorithm implementations.
"""

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from stockrelate import charts
from stockrelate.config import MAX_DISPLAYED_PAIRS, TEACHING_PRESET_TICKS
from stockrelate.graphs import degrees, find_shortest_path
from stockrelate.models import AnalysisResult, BoolMatrix, PreparedData, PropertyReport, Symbols
from stockrelate.relations import count_transitivity_premises
from stockrelate.sets import (
    format_set,
    is_subset,
    sector_subsets,
    set_difference,
    set_intersection,
    set_union,
    sorted_members,
)

SOURCE_LABELS = {
    "teaching_matrix": "Teaching matrix (synthetic correlations)",
    "synthetic_prices": "Bundled synthetic prices (not market data)",
    "upload": "Uploaded CSV pair",
    "historical": "Historical prices (Yahoo Finance snapshot)",
}


@dataclass(frozen=True)
class SourceView:
    """What Module 1 needs to know about the loaded universe and the applied set."""

    source_mode: str
    universe: Symbols
    names: dict[str, str]
    sectors: dict[str, str]
    available: frozenset[str]
    applied: Symbols


def bool_frame(symbols: Symbols, matrix: BoolMatrix) -> pd.DataFrame:
    return pd.DataFrame(
        [[1 if cell else 0 for cell in row] for row in matrix],
        index=list(symbols),
        columns=list(symbols),
    )


def code(text: str) -> str:
    return f"`{text}`"


def labeled(names: dict[str, str], symbol: str) -> str:
    """'AAPL · Apple Inc.' for tables; falls back to the ticker alone."""
    name = names.get(symbol, "")
    return f"{symbol} · {name}" if name and name != symbol else symbol


def labeled_members(names: dict[str, str], members) -> str:
    ordered = sorted_members(members)
    return ", ".join(labeled(names, symbol) for symbol in ordered) if ordered else "∅"


def correlation_of(prepared: PreparedData, first: str, second: str) -> float:
    index = {symbol: i for i, symbol in enumerate(prepared.symbols)}
    return prepared.correlation[index[first]][index[second]]


def compare_text(value: float, tau: float) -> str:
    return f"{value:.6g} ≥ {tau:.6g}" if value >= tau else f"{value:.6g} < {tau:.6g}"


# --------------------------------------------------------------------------
# Module 1
# --------------------------------------------------------------------------


def render_sets_tab(view: SourceView, use_as_selection: Callable[[list[str]], None]) -> None:
    st.subheader("Module 1 · Stock Set Builder")
    st.caption("Sets, membership, cardinality, subsets, union, intersection and difference.")

    universe, applied = view.universe, view.applied
    col1, col2, col3 = st.columns(3)
    col1.metric("|U| (universe)", len(universe))
    col2.metric("|S| (applied selection)", len(applied))
    col3.metric("S ⊆ U", "True ✓" if is_subset(applied, universe) else "False ✗")
    st.markdown(f"**U** = {code(format_set(universe))}")
    st.markdown(f"**S** = {code(format_set(applied))}")
    if not applied:
        st.info(
            "S = ∅ (cardinality 0). Select stocks in the sidebar and press **Apply selection** "
            "to enable Modules 2–5."
        )

    table = pd.DataFrame(
        {
            "symbol": list(universe),
            "name": [view.names[s] for s in universe],
            "sector": [view.sectors[s] for s in universe],
            "in S": ["yes" if s in applied else "no" for s in universe],
            "price data": [
                "teaching matrix"
                if view.source_mode == "teaching_matrix"
                else ("available" if s in view.available else "unavailable")
                for s in universe
            ],
        }
    )
    st.dataframe(table, hide_index=True, width="stretch")

    st.markdown("#### Sector subsets  Sₖ = S ∩ Uₖ")
    subsets = sector_subsets(applied, view.sectors)
    if subsets:
        st.dataframe(
            pd.DataFrame(
                {
                    "sector k": list(subsets),
                    "Sₖ": [format_set(members) for members in subsets.values()],
                    "|Sₖ|": [len(members) for members in subsets.values()],
                    "stocks": [labeled_members(view.names, members) for members in subsets.values()],
                }
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "Each stock has exactly one sector, so the nonempty sector subsets are "
            "pairwise disjoint and their union is S: they partition S."
        )

    st.markdown("#### Custom subsets X, Y ⊆ S")
    if not applied:
        st.caption("Custom subsets are drawn from S; they are all ∅ while S is empty.")
    sector_options = ["(none)", *subsets]
    left, right = st.columns(2)
    with left:
        preset = st.selectbox("Fill X from sector", sector_options, key="preset_x")
        if preset != "(none)":
            st.button(
                "Set X = S ∩ U_" + preset,
                key="apply_preset_x",
                on_click=_set_state,
                args=("set_x", subsets.get(preset, [])),
            )
        x_members = st.multiselect(
            "X", list(applied), key="set_x", format_func=lambda s: labeled(view.names, s)
        )
    with right:
        preset = st.selectbox("Fill Y from sector", sector_options, key="preset_y")
        if preset != "(none)":
            st.button(
                "Set Y = S ∩ U_" + preset,
                key="apply_preset_y",
                on_click=_set_state,
                args=("set_y", subsets.get(preset, [])),
            )
        y_members = st.multiselect(
            "Y", list(applied), key="set_y", format_func=lambda s: labeled(view.names, s)
        )

    operations = [
        ("X ∪ Y", set_union(x_members, y_members)),
        ("X ∩ Y", set_intersection(x_members, y_members)),
        ("X \\ Y", set_difference(x_members, y_members)),
        ("Y \\ X", set_difference(y_members, x_members)),
    ]
    st.dataframe(
        pd.DataFrame(
            {
                "expression": [name for name, _ in operations],
                "result": [format_set(members) for _, members in operations],
                "cardinality": [len(members) for _, members in operations],
                "stocks": [labeled_members(view.names, members) for _, members in operations],
            }
        ),
        hide_index=True,
        width="stretch",
    )
    checks = [
        ("|X|", str(len(set(x_members)))),
        ("|Y|", str(len(set(y_members)))),
        ("X ⊆ Y", _tick(is_subset(x_members, y_members))),
        ("Y ⊆ X", _tick(is_subset(y_members, x_members))),
        ("X ⊆ S", _tick(is_subset(x_members, applied))),
        ("∅ ⊆ X", _tick(is_subset([], x_members))),
    ]
    st.dataframe(pd.DataFrame(checks, columns=["statement", "value"]), hide_index=True)

    st.markdown("Replace the analysis selection S with a result (inspection alone never changes S):")
    columns = st.columns(len(operations))
    for column, (name, members) in zip(columns, operations, strict=True):
        column.button(
            f"Use {name} as S",
            key=f"use_{name}",
            on_click=use_as_selection,
            args=(sorted_members(members),),
            width="stretch",
        )


def _set_state(key: str, value) -> None:
    st.session_state[key] = list(value)


def _tick(value: bool) -> str:
    return "True ✓" if value else "False ✗"


# --------------------------------------------------------------------------
# Module 2
# --------------------------------------------------------------------------


def render_relation_tab(prepared: PreparedData, result: AnalysisResult) -> None:
    st.subheader("Module 2 · Correlation Binary Relation Builder")
    relation = result.relation
    tau = relation.threshold
    symbols = result.symbols
    summary = prepared.validation_summary

    st.markdown(f"**Source:** {SOURCE_LABELS.get(prepared.source_mode, prepared.source_mode)}")
    if prepared.source_mode == "teaching_matrix":
        st.caption("Synthetic teaching correlations; no price window and no empirical observation count.")
    else:
        st.caption(
            f"Requested window {prepared.requested_start} to {prepared.requested_end}; "
            f"{prepared.sample_count} aligned return observations retained "
            f"(from {summary.candidate_return_rows} candidates, "
            f"{summary.removed_return_count} discarded)."
        )

    with st.expander("How returns and correlations are defined"):
        st.markdown(
            "Simple fractional returns between consecutive observed dates:  \n"
            "r(t,s) = P(t,s) / P(t−1,s) − 1  (0.02 means 2%).  \n"
            "Return rows touching any missing price are discarded for **all** stocks "
            "(listwise alignment), so every correlation uses the same dates.  \n"
            "Pearson correlation C(i,j) = Σ(rᵢ−r̄ᵢ)(rⱼ−r̄ⱼ) / (√Σ(rᵢ−r̄ᵢ)² · √Σ(rⱼ−r̄ⱼ)²)."
        )

    st.plotly_chart(
        charts.correlation_heatmap(
            symbols,
            prepared.correlation,
            "Correlation matrix C"
            + (" (synthetic)" if prepared.source_mode in ("teaching_matrix", "synthetic_prices") else ""),
        ),
        width="stretch",
    )

    st.markdown(
        f"**Rτ = {{(a, b) ∈ S × S : C(a, b) ≥ τ}}** with τ = {code(repr(tau))} "
        "(signed, inclusive, unrounded; negative correlations never qualify)."
    )
    col1, col2, col3 = st.columns(3)
    col1.metric("Nodes n = |S|", result.metrics["node_count"])
    col2.metric("Undirected edges m", result.metrics["edge_count"])
    col3.metric("Ordered pairs |Rτ| = n + 2m", result.metrics["ordered_pair_count"])

    left, right = st.columns(2)
    with left:
        st.plotly_chart(
            charts.boolean_heatmap(symbols, relation.matrix, "Relation matrix A (diagonal kept)"),
            width="stretch",
        )
    with right:
        st.plotly_chart(
            charts.relation_graph(result, prepared.sectors, "Graph Gτ (self-loops hidden)"),
            width="stretch",
        )
        st.caption(
            "The graph omits loops for readability; every (a, a) is still in Rτ. "
            "Each line is one undirected edge = two ordered pairs."
        )

    with st.expander("Relation matrix as a table (0/1)"):
        st.dataframe(bool_frame(symbols, relation.matrix), width="stretch")

    pairs = relation.ordered_pairs
    st.markdown(
        f"**Ordered pairs** ({len(pairs)} total"
        + (
            f", first {MAX_DISPLAYED_PAIRS} shown; download for all"
            if len(pairs) > MAX_DISPLAYED_PAIRS
            else ""
        )
        + ")"
    )
    st.dataframe(
        pd.DataFrame(pairs[:MAX_DISPLAYED_PAIRS], columns=["source", "target"]),
        hide_index=True,
        height=220,
        width="stretch",
    )
    st.markdown(f"**Undirected edges** ({len(relation.edges)})")
    st.dataframe(
        pd.DataFrame(
            [
                (labeled(prepared.names, a), labeled(prepared.names, b), correlation_of(prepared, a, b))
                for a, b in relation.edges
            ],
            columns=["source", "target", "correlation"],
        ),
        hide_index=True,
        width="stretch",
    )

    st.markdown("#### Pair inspector")
    left, right = st.columns(2)
    source = left.selectbox(
        "Source a", list(symbols), key="pair_source", format_func=lambda s: labeled(prepared.names, s)
    )
    target = right.selectbox(
        "Target b", list(symbols), key="pair_target", format_func=lambda s: labeled(prepared.names, s)
    )
    i, j = symbols.index(source), symbols.index(target)
    value = prepared.correlation[i][j]
    member = relation.matrix[i][j]
    st.dataframe(
        pd.DataFrame(
            [
                ("C(a, b)", repr(value)),
                ("Comparison", compare_text(value, tau)),
                ("(a, b) ∈ Rτ", _tick(member)),
                ("Self-relation (a = b)", "yes" if source == target else "no"),
                ("Drawn as graph edge", "yes" if member and source != target else "no"),
            ],
            columns=["item", "value"],
        ),
        hide_index=True,
    )


# --------------------------------------------------------------------------
# Module 3
# --------------------------------------------------------------------------


def render_property_report(
    label: str,
    symbols: Symbols,
    matrix: BoolMatrix,
    report: PropertyReport,
    prepared: PreparedData | None = None,
    tau: float | None = None,
) -> None:
    n = len(symbols)
    cards = st.columns(3)
    with cards[0]:
        _property_card(
            "Reflexive",
            "∀a ∈ S: aRa",
            report.reflexive.holds,
            f"Checked all {n} diagonal cells; every (a, a) is related.",
            None
            if report.reflexive.holds
            else f"({report.reflexive.witness[0]}, {report.reflexive.witness[0]}) ∉ {label}.",
        )
    with cards[1]:
        witness = report.symmetric.witness
        _property_card(
            "Symmetric",
            "∀a, b: aRb ⇒ bRa",
            report.symmetric.holds,
            f"Checked all {n * n} ordered pairs; no pair lacks its reverse.",
            None
            if witness is None
            else f"({witness[0]}, {witness[1]}) ∈ {label} but ({witness[1]}, {witness[0]}) ∉ {label}.",
        )
    with cards[2]:
        witness = report.transitive.witness
        failure = None
        if witness is not None:
            a, b, c = witness
            failure = f"({a}, {b}) ∈ {label} and ({b}, {c}) ∈ {label}, but ({a}, {c}) ∉ {label}."
            if prepared is not None and tau is not None:
                if any(prepared.names.get(s, s) != s for s in (a, b, c)):
                    failure += "  \n" + "; ".join(
                        labeled(prepared.names, s) for s in dict.fromkeys((a, b, c))
                    )
                failure += (
                    f"  \nC({a},{b}) = {compare_text(correlation_of(prepared, a, b), tau)}; "
                    f"C({b},{c}) = {compare_text(correlation_of(prepared, b, c), tau)}; "
                    f"C({a},{c}) = {compare_text(correlation_of(prepared, a, c), tau)}."
                )
        _property_card(
            "Transitive",
            "∀a, b, c: (aRb ∧ bRc) ⇒ aRc",
            report.transitive.holds,
            f"Exhaustively checked all {count_transitivity_premises(matrix)} triples satisfying "
            "the premises; each conclusion held. This alone does not mean all stocks form one class.",
            failure,
        )


def _property_card(name: str, statement: str, holds: bool, pass_text: str, fail_text: str | None) -> None:
    with st.container(border=True):
        st.markdown(f"**{name}**  \n{code(statement)}")
        if holds:
            st.success(f"✓ PASS — {pass_text}")
        else:
            st.error(f"✗ FAIL — counterexample: {fail_text}")


def render_properties_tab(prepared: PreparedData, result: AnalysisResult) -> None:
    st.subheader("Module 3 · Relation Property Analyzer")
    st.markdown("Analyzing: **Direct relation R** = Rτ at τ = " + code(repr(result.relation.threshold)))
    symbols = result.symbols
    report = result.direct_properties
    render_property_report("R", symbols, result.relation.matrix, report, prepared, result.relation.threshold)

    if report.is_equivalence:
        st.info(
            "R itself is an equivalence relation at this threshold, so its classes coincide "
            "with the reachability classes of Module 4."
        )
    else:
        st.warning(
            "R is **not** an equivalence relation here. Module 4 builds the reachability "
            "relation Q, which always is."
        )

    witness = report.transitive.witness
    highlight_cells: set[tuple[str, str]] = set()
    highlight_edges: set[tuple[str, str]] = set()
    missing: set[tuple[str, str]] = set()
    if witness is not None:
        a, b, c = witness
        highlight_cells = {(a, b), (b, c), (a, c)}
        highlight_edges = {tuple(sorted(pair)) for pair in ((a, b), (b, c)) if pair[0] != pair[1]}
        if a != c:
            missing = {tuple(sorted((a, c)))}
    if report.symmetric.witness is not None:
        a, b = report.symmetric.witness
        highlight_cells |= {(a, b), (b, a)}
    if report.reflexive.witness is not None:
        a = report.reflexive.witness[0]
        highlight_cells.add((a, a))

    left, right = st.columns(2)
    with left:
        st.plotly_chart(
            charts.boolean_heatmap(
                symbols, result.relation.matrix, "Direct relation R (witness cells outlined)", highlight_cells
            ),
            width="stretch",
        )
    with right:
        st.plotly_chart(
            charts.relation_graph(
                result, prepared.sectors, "Gτ with witness highlighted", highlight_edges, missing
            ),
            width="stretch",
        )
    if witness is not None:
        st.caption(
            "Yellow: the two premise edges and outlined cells. Red dashed: the missing conclusion pair."
        )


# --------------------------------------------------------------------------
# Module 4
# --------------------------------------------------------------------------


def render_reachability_tab(prepared: PreparedData, result: AnalysisResult) -> None:
    st.subheader("Module 4 · Reachability Equivalence Classes & Community Finder")
    symbols = result.symbols
    st.markdown(
        "**Q** = (I ∪ Rτ)*: a Q b iff there is a path of length ≥ 0 from a to b in Gτ. "
        "Computed by Warshall's algorithm; classes found independently by BFS."
    )

    st.markdown("#### Reachability classes (connected components)")
    col1, col2, col3 = st.columns(3)
    col1.metric("Classes", result.metrics["component_count"])
    col2.metric("Largest class", result.metrics["largest_component_size"])
    col3.metric("Isolated stocks", result.metrics["isolated_count"])
    st.dataframe(
        pd.DataFrame(
            {
                "class id": [f"[{component[0]}]" for component in result.components],
                "members": [format_set(component) for component in result.components],
                "size": [len(component) for component in result.components],
                "stocks": [labeled_members(prepared.names, component) for component in result.components],
            }
        ),
        hide_index=True,
        width="stretch",
    )
    with st.expander("Stock → class table"):
        st.dataframe(
            pd.DataFrame(
                {
                    "symbol": list(symbols),
                    "name": [prepared.names.get(s, "") for s in symbols],
                    "sector": [prepared.sectors.get(s, "") for s in symbols],
                    "class id": [f"[{result.component_of(s)}]" for s in symbols],
                }
            ),
            hide_index=True,
        )

    covered = sorted(s for component in result.components for s in component)
    st.markdown("**Partition checks**")
    st.dataframe(
        pd.DataFrame(
            [
                ("Union of classes = S", _tick(set(covered) == set(symbols))),
                ("No overlapping classes", _tick(len(covered) == len(set(covered)))),
                ("No empty classes", _tick(all(result.components))),
                ("Every stock assigned exactly once", _tick(covered == list(symbols))),
            ],
            columns=["check", "result"],
        ),
        hide_index=True,
    )

    st.plotly_chart(
        charts.relation_graph(result, prepared.sectors, "Gτ colored by class (original edges only)"),
        width="stretch",
    )
    st.caption("Node colors mark classes; class IDs in the tables are authoritative.")

    added = set(result.closure_added_pairs)
    left, right = st.columns(2)
    with left:
        st.plotly_chart(
            charts.boolean_heatmap(symbols, result.relation.matrix, "Direct relation Rτ"),
            width="stretch",
        )
    with right:
        st.plotly_chart(
            charts.boolean_heatmap(
                symbols, result.reachability_matrix, "Reachability Q (Q \\ R outlined)", added
            ),
            width="stretch",
        )
    if added:
        st.markdown(
            "**Q \\ R** (added by closure): "
            + ", ".join(f"({a}, {b})" for a, b in result.closure_added_pairs)
        )
        st.caption(
            "These are reachability links through intermediate stocks, not newly measured "
            "correlations. A class need not be a clique: members can be below the threshold "
            "with each other."
        )
    else:
        st.markdown("**Q \\ R = ∅** — the closure added no pairs, so R was already transitive.")

    st.markdown("**Equivalence check of Q** — analyzing: **Reachability relation Q**")
    render_property_report("Q", symbols, result.reachability_matrix, result.reachability_properties)

    st.markdown("#### Shortest path (fewest edges)")
    left, right = st.columns(2)
    start = left.selectbox(
        "From", list(symbols), key="path_source", format_func=lambda s: labeled(prepared.names, s)
    )
    end = right.selectbox(
        "To", list(symbols), key="path_target", format_func=lambda s: labeled(prepared.names, s)
    )
    path = find_shortest_path(result.relation.neighbors, start, end)
    if path is None:
        st.warning(f"No path from {start} to {end} at this threshold, so ({start}, {end}) ∉ Q.")
    elif len(path) == 1:
        st.success(f"{start} reaches itself by a zero-edge path, so ({start}, {start}) ∈ Q.")
    else:
        steps = []
        for first, second in zip(path, path[1:], strict=False):
            steps.append(f"{first}–{second} (C = {correlation_of(prepared, first, second):.4f})")
        st.success(
            f"Path of {len(path) - 1} edge(s): " + " → ".join(labeled(prepared.names, s) for s in path)
        )
        st.caption(
            "Edges used: " + "; ".join(steps) + ". BFS minimizes edge count, not correlation distance."
        )

    with st.expander("How Warshall's algorithm works"):
        st.markdown(
            "Start from R with every diagonal cell set to 1 (paths of length 0). "
            "For each intermediate stock k (outer loop), for each i and j: "
            "Q[i][j] ← Q[i][j] OR (Q[i][k] AND Q[k][j]). After processing k, every pair "
            "connected by a path whose internal stocks are among those processed is marked. "
            "The result is the smallest reflexive-transitive relation containing R."
        )


# --------------------------------------------------------------------------
# Module 5
# --------------------------------------------------------------------------


def render_explorer_tab(
    prepared: PreparedData,
    result: AnalysisResult,
    comparison: list[dict],
    set_threshold_tick: Callable[[int], None],
) -> None:
    st.subheader("Module 5 · Interactive Threshold Explorer")
    st.markdown("Every view in this app reads the single shared threshold slider above the tabs.")

    st.markdown("**Teaching presets** (move the shared slider):")
    columns = st.columns(len(TEACHING_PRESET_TICKS))
    for column, tick in zip(columns, TEACHING_PRESET_TICKS, strict=True):
        column.button(
            f"τ = {tick / 100:.2f}",
            key=f"preset_{tick}",
            on_click=set_threshold_tick,
            args=(tick,),
            width="stretch",
        )
    if prepared.source_mode == "teaching_matrix":
        st.caption(
            "Expected story on the teaching matrix: at 0.20 the triangle A–B–C is complete; at 0.65 "
            "the edge A–C disappears and transitivity fails while A, B, C stay one class; at 0.75 C "
            "splits off and transitivity returns; at 0.90 every stock is isolated."
        )
    else:
        st.caption(
            "Try the presets and watch the table: edges only disappear as τ rises, classes only "
            "split, while direct transitivity can switch between failing and holding."
        )

    st.markdown("**Preset comparison** (same prepared correlation matrix)")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "τ": f"{row['threshold']:.2f}",
                    "edges": row["edge_count"],
                    "R transitive?": "yes ✓" if row["direct_transitive"] else "no ✗",
                    "classes": row["component_count"],
                    "largest class": row["largest_component_size"],
                }
                for row in comparison
            ]
        ),
        hide_index=True,
    )

    st.markdown("**Current threshold**")
    degree_map = degrees(result.relation.neighbors)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("τ", f"{result.relation.threshold:.2f}")
    col2.metric("Edges", result.metrics["edge_count"])
    col3.metric("Classes", result.metrics["component_count"])
    col4.metric("Density", f"{result.metrics['density']:.3f}")
    st.plotly_chart(
        charts.relation_graph(result, prepared.sectors, "Gτ (fixed positions across thresholds)"),
        width="stretch",
    )
    st.dataframe(
        pd.DataFrame(
            {
                "symbol": list(result.symbols),
                "name": [prepared.names.get(s, "") for s in result.symbols],
                "sector": [prepared.sectors.get(s, "") for s in result.symbols],
                "degree": [degree_map[s] for s in result.symbols],
                "class id": [f"[{result.component_of(s)}]" for s in result.symbols],
            }
        ),
        hide_index=True,
    )
    if len(result.symbols) < 2:
        st.caption("Density is shown as 0 by convention when n < 2 (no possible edges).")

    with st.expander("Why classes refine as τ rises"):
        st.markdown(
            "For fixed data, τ₁ ≤ τ₂ implies R(τ₂) ⊆ R(τ₁), E(τ₂) ⊆ E(τ₁) and Q(τ₂) ⊆ Q(τ₁). "
            "Raising τ can only remove edges, so classes can split but never merge, and the "
            "class count never decreases: the partition becomes finer.  \n"
            "**Transitivity is not monotone:** a triangle is transitive, removing one edge breaks "
            "it, and removing another can make it transitive again."
        )
