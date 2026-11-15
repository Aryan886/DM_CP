"""Plotly figures. Charts render supplied results; they never compute connectivity."""

import math

import plotly.graph_objects as go

from stockrelate.models import AnalysisResult, BoolMatrix, FloatMatrix, Symbols

COMPONENT_COLORS = (
    "#2a5e3f",  # canvas-hero green
    "#038ff7",  # blue-500
    "#e0892b",  # warm amber
    "#8b4fc7",  # plum
    "#d94f70",  # rose
    "#1a9e8f",  # teal
    "#86521e",  # badge brown
    "#5b6475",  # slate
    "#8a9a2b",  # olive
    "#5fb3e8",  # sky
)
INK = "#1b1a18"
HAIRLINE = "#e8e6e1"
CANVAS_SOFT = "#f4f3f0"
HIGHLIGHT = "#f5a623"  # warning token: witness premises
MISSING = "#ee0000"  # error token: missing conclusion pair
EDGE = "#c9c6bf"


def circle_positions(symbols: Symbols) -> dict[str, tuple[float, float]]:
    """Fixed circular layout in canonical order; depends only on the symbols."""
    count = len(symbols)
    if count == 1:
        return {symbols[0]: (0.0, 0.0)}
    positions = {}
    for i, symbol in enumerate(symbols):
        angle = math.pi / 2 - 2 * math.pi * i / count
        positions[symbol] = (math.cos(angle), math.sin(angle))
    return positions


def correlation_heatmap(symbols: Symbols, correlation: FloatMatrix, title: str) -> go.Figure:
    labels = list(symbols)
    figure = go.Figure(
        go.Heatmap(
            z=correlation,
            x=labels,
            y=labels,
            zmin=-1,
            zmax=1,
            colorscale=[[0.0, "#038ff7"], [0.5, "#f4f3f0"], [1.0, "#2a5e3f"]],
            text=[[f"{value:.3f}" for value in row] for row in correlation],
            texttemplate="%{text}" if len(labels) <= 12 else None,
            hovertemplate="C(%{y}, %{x}) = %{z:.6f}<extra></extra>",
        )
    )
    return _square_layout(figure, title)


def boolean_heatmap(
    symbols: Symbols,
    matrix: BoolMatrix,
    title: str,
    highlight: set[tuple[str, str]] | None = None,
) -> go.Figure:
    labels = list(symbols)
    values = [[1 if cell else 0 for cell in row] for row in matrix]
    text = [[str(cell) for cell in row] for row in values]
    figure = go.Figure(
        go.Heatmap(
            z=values,
            x=labels,
            y=labels,
            zmin=0,
            zmax=1,
            colorscale=[[0, CANVAS_SOFT], [1, INK]],
            showscale=False,
            text=text,
            texttemplate="%{text}",
            hovertemplate="(%{y}, %{x}) → %{z}<extra></extra>",
            xgap=2,
            ygap=2,
        )
    )
    for source, target in sorted(highlight or ()):
        figure.add_shape(
            type="rect",
            x0=labels.index(target) - 0.5,
            x1=labels.index(target) + 0.5,
            y0=labels.index(source) - 0.5,
            y1=labels.index(source) + 0.5,
            line={"color": HIGHLIGHT, "width": 3},
        )
    return _square_layout(figure, title)


def _square_layout(figure: go.Figure, title: str) -> go.Figure:
    figure.update_layout(
        title=title,
        height=420,
        margin={"l": 40, "r": 20, "t": 60, "b": 40},
        yaxis={"autorange": "reversed", "scaleanchor": "x", "type": "category"},
        xaxis={"type": "category", "side": "bottom"},
    )
    return figure


def relation_graph(
    result: AnalysisResult,
    sectors: dict[str, str],
    title: str,
    highlight_edges: set[tuple[str, str]] | None = None,
    missing_edges: set[tuple[str, str]] | None = None,
) -> go.Figure:
    """Undirected graph Gτ: one line per edge, no loops, nodes colored by class.

    ``missing_edges`` draws dashed lines for pairs that are NOT edges (used to
    show a transitivity counterexample); they are labeled as absent.
    """
    symbols = result.symbols
    positions = circle_positions(symbols)
    figure = go.Figure()
    highlight_edges = highlight_edges or set()

    for source, target in result.relation.edges:
        (x0, y0), (x1, y1) = positions[source], positions[target]
        is_highlight = (source, target) in highlight_edges
        figure.add_trace(
            go.Scatter(
                x=[x0, x1],
                y=[y0, y1],
                mode="lines",
                line={"color": HIGHLIGHT if is_highlight else EDGE, "width": 5 if is_highlight else 1.6},
                hoverinfo="skip",
                showlegend=False,
            )
        )
    for source, target in sorted(missing_edges or ()):
        (x0, y0), (x1, y1) = positions[source], positions[target]
        figure.add_trace(
            go.Scatter(
                x=[x0, x1],
                y=[y0, y1],
                mode="lines",
                line={"color": MISSING, "width": 2, "dash": "dash"},
                name=f"missing {source}–{target}",
                hoverinfo="name",
            )
        )

    class_of = {}
    for index, component in enumerate(result.components):
        for symbol in component:
            class_of[symbol] = (index, component[0])
    figure.add_trace(
        go.Scatter(
            x=[positions[s][0] for s in symbols],
            y=[positions[s][1] for s in symbols],
            mode="markers+text",
            text=list(symbols),
            textposition="top center",
            marker={
                "size": 26,
                "color": [COMPONENT_COLORS[class_of[s][0] % len(COMPONENT_COLORS)] for s in symbols],
                "line": {"color": "#fefdfb", "width": 2.5},
            },
            customdata=[
                [sectors.get(s, ""), len(result.relation.neighbors[s]), class_of[s][1]] for s in symbols
            ],
            hovertemplate=(
                "%{text}<br>sector: %{customdata[0]}<br>degree: %{customdata[1]}"
                "<br>class: [%{customdata[2]}]<extra></extra>"
            ),
            showlegend=False,
        )
    )
    figure.update_layout(
        title=title,
        height=440,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        xaxis={"visible": False, "range": [-1.35, 1.35]},
        yaxis={"visible": False, "range": [-1.35, 1.35], "scaleanchor": "x"},
        legend={"orientation": "h"},
    )
    return figure
