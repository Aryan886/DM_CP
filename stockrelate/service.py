"""Web-facing service layer: turns DM results into JSON-ready dictionaries.

Stateless per request: the browser holds the selection and threshold, and the
server keeps small bounded caches keyed by content hashes so that repeated
requests (e.g. dragging the threshold slider) never re-prepare price data.
This module only translates results; DM algorithms stay in relations.py,
graphs.py, sets.py and analysis.py.
"""

import json
import logging
from collections import OrderedDict
from dataclasses import dataclass

from stockrelate import charts
from stockrelate.analysis import analysis_key, analyze, preset_comparison
from stockrelate.config import (
    APP_VERSION,
    DEFAULT_SETTINGS,
    DEFAULT_THRESHOLD_TICK,
    MAX_DISPLAYED_PAIRS,
    MAX_SELECTED_STOCKS,
    PREPROCESSING_POLICY_VERSION,
    TEACHING_PRESET_TICKS,
)
from stockrelate.exports import all_exports
from stockrelate.graphs import degrees, find_shortest_path
from stockrelate.market_data import (
    DataValidationError,
    ValidatedDataset,
    historical_dataset_available,
    load_bundled_dataset,
    load_dataset,
    load_historical_dataset,
    load_teaching_fixture,
    prepare_market_data,
    prepare_teaching_data,
    sha256_bytes,
    sha256_text,
)
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

logger = logging.getLogger("stockrelate")

SOURCE_OPTIONS = ["teaching_matrix", "synthetic_prices", "historical", "upload"]
SOURCE_NAMES = {
    "teaching_matrix": "Teaching matrix",
    "synthetic_prices": "Bundled synthetic prices",
    "historical": "Historical prices",
    "upload": "Upload CSV pair",
}
SOURCE_DETAILS = {
    "teaching_matrix": "5 made-up stocks A–E with hand-written correlations",
    "synthetic_prices": "12 generated stocks, 3 sectors — not market data",
    "historical": "16 US stocks, Yahoo Finance snapshot 2024–2025",
    "upload": "Your own metadata + price CSV files",
}
SOURCE_LABELS = {
    "teaching_matrix": "Teaching matrix (synthetic correlations)",
    "synthetic_prices": "Bundled synthetic prices (not market data)",
    "upload": "Uploaded CSV pair",
    "historical": "Historical prices (Yahoo Finance snapshot)",
}
BUNDLED_LOADERS = {
    "synthetic_prices": load_bundled_dataset,
    "historical": load_historical_dataset,
}


class ServiceError(ValueError):
    """A problem the user can fix (bad input, unknown dataset); shown verbatim."""


class _Cache:
    """Tiny bounded LRU map."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.items: OrderedDict = OrderedDict()

    def get(self, key):
        if key in self.items:
            self.items.move_to_end(key)
            return self.items[key]
        return None

    def put(self, key, value) -> None:
        self.items[key] = value
        self.items.move_to_end(key)
        while len(self.items) > self.size:
            self.items.popitem(last=False)


_datasets = _Cache(8)  # dataset key -> ValidatedDataset
_prepared = _Cache(32)  # prepared key -> PreparedData
_analyses = _Cache(64)  # analysis key -> AnalysisResult
_comparisons = _Cache(32)  # data_id -> preset rows

stats = {"prepare_count": 0}  # observable by tests: slider moves must not re-prepare


# ------------------------------------------------------------------ helpers


def available_sources() -> list[str]:
    """Hide the historical option until scripts/fetch_yahoo_data.py has produced its files."""
    return [mode for mode in SOURCE_OPTIONS if mode != "historical" or historical_dataset_available()]


def default_source() -> str:
    return "historical" if historical_dataset_available() else "teaching_matrix"


def labeled(names: dict[str, str], symbol: str) -> str:
    name = names.get(symbol, "")
    return f"{symbol} · {name}" if name and name != symbol else symbol


def labeled_members(names: dict[str, str], members) -> str:
    ordered = sorted_members(members)
    return ", ".join(labeled(names, symbol) for symbol in ordered) if ordered else "∅"


def compare_text(value: float, tau: float) -> str:
    return f"{value:.6g} ≥ {tau:.6g}" if value >= tau else f"{value:.6g} < {tau:.6g}"


def provenance_text(provenance: dict) -> str:
    text = provenance.get("description", "")
    if provenance.get("kind") == "historical":
        first, last = provenance.get("date_range") or ("?", "?")
        text += (
            f" ({first} to {last}); {provenance.get('price_convention', '')}; "
            f"retrieved {provenance.get('retrieved_at', 'unknown')}. "
            "Selection is not an index-membership claim."
        )
    return text


def _figure(figure) -> dict:
    """Plain figure JSON without Plotly's default template; the browser applies the Clay theme."""
    payload = json.loads(figure.to_json())
    payload.get("layout", {}).pop("template", None)
    return payload


def app_config() -> dict:
    return {
        "version": APP_VERSION,
        "sources": [
            {"id": mode, "label": SOURCE_NAMES[mode], "detail": SOURCE_DETAILS[mode]}
            for mode in available_sources()
        ],
        "default_source": default_source(),
        "default_tick": DEFAULT_THRESHOLD_TICK,
        "preset_ticks": list(TEACHING_PRESET_TICKS),
        "max_selected": MAX_SELECTED_STOCKS,
        "min_return_observations": DEFAULT_SETTINGS.min_return_observations,
    }


# ------------------------------------------------------------------ sources


@dataclass(frozen=True)
class Source:
    mode: str
    universe: Symbols
    names: dict[str, str]
    sectors: dict[str, str]
    available: frozenset[str]
    dataset: ValidatedDataset | None
    dataset_key: str | None
    provenance: str


def _source_from_dataset(dataset: ValidatedDataset, key: str, provenance: str) -> Source:
    return Source(
        mode=dataset.source_mode,
        universe=dataset.universe,
        names=dataset.names,
        sectors=dataset.sectors,
        available=dataset.available_symbols,
        dataset=dataset,
        dataset_key=key,
        provenance=provenance,
    )


def load_source(mode: str, dataset_key: str | None = None) -> Source:
    if mode not in available_sources():
        raise ServiceError(f"Unknown data source {mode!r}.")
    if mode == "teaching_matrix":
        fixture = load_teaching_fixture()
        symbols = tuple(fixture["symbols"])
        return Source(
            mode=mode,
            universe=symbols,
            names=fixture["names"],
            sectors=fixture["sectors"],
            available=frozenset(symbols),
            dataset=None,
            dataset_key=None,
            provenance="Synthetic five-stock teaching correlation matrix; not market values.",
        )
    if mode in BUNDLED_LOADERS:
        dataset = _datasets.get(mode)
        if dataset is None:
            dataset = BUNDLED_LOADERS[mode](DEFAULT_SETTINGS)
            _datasets.put(mode, dataset)
        return _source_from_dataset(dataset, mode, provenance_text(dataset.provenance))
    # upload
    dataset = _datasets.get(dataset_key) if dataset_key else None
    if dataset is None:
        raise ServiceError("Uploaded files are no longer available on the server. Please upload them again.")
    return _source_from_dataset(dataset, dataset_key, "User supplied; provenance not independently verified.")


def register_upload(metadata_bytes: bytes, prices_bytes: bytes) -> Source:
    # Content hashes, never filenames: replacing a file with new data gives a new key.
    key = "upload:" + sha256_bytes(metadata_bytes) + sha256_bytes(prices_bytes)
    dataset = _datasets.get(key)
    if dataset is None:
        dataset = load_dataset(metadata_bytes, prices_bytes, DEFAULT_SETTINGS, "upload")
        _datasets.put(key, dataset)
    return load_source("upload", key)


def source_payload(source: Source) -> dict:
    dataset = source.dataset
    bounds = dataset.date_bounds() if dataset is not None else None
    stocks = []
    for symbol in source.universe:
        stocks.append(
            {
                "symbol": symbol,
                "name": source.names.get(symbol, ""),
                "sector": source.sectors.get(symbol, ""),
                "available": symbol in source.available,
            }
        )
    available = [s for s in source.universe if s in source.available]
    return {
        "mode": source.mode,
        "label": SOURCE_NAMES[source.mode],
        "dataset_key": source.dataset_key,
        "stocks": stocks,
        "default_selection": available[:MAX_SELECTED_STOCKS],
        "date_bounds": list(bounds) if bounds else None,
        "warnings": list(dataset.warnings) if dataset is not None else [],
        "provenance": source.provenance,
        "auto_apply": source.mode != "upload",
    }


# ------------------------------------------------------------------ prepare + analyze


def prepare(source: Source, symbols, start: str | None, end: str | None) -> PreparedData:
    symbols = tuple(sorted(set(symbols)))
    if not symbols:
        raise ServiceError("Select at least one stock to run the analysis.")
    if len(symbols) > MAX_SELECTED_STOCKS:
        raise ServiceError(f"Select at most {MAX_SELECTED_STOCKS} stocks.")
    key = sha256_text(
        json.dumps(
            {
                "source": source.dataset_key or "teaching",
                "mode": source.mode,
                "symbols": list(symbols),
                "start": start,
                "end": end,
                "policy": PREPROCESSING_POLICY_VERSION,
                "min_obs": DEFAULT_SETTINGS.min_return_observations,
            },
            sort_keys=True,
        )
    )
    cached = _prepared.get(key)
    if cached is not None:
        return cached
    if source.dataset is None:
        prepared = prepare_teaching_data(symbols)
    else:
        if not start or not end:
            bounds = source.dataset.date_bounds()
            if bounds is None:
                raise ServiceError("The dataset has no price records.")
            start, end = start or bounds[0], end or bounds[1]
        prepared = prepare_market_data(source.dataset, symbols, start, end, DEFAULT_SETTINGS)
    stats["prepare_count"] += 1
    _prepared.put(key, prepared)
    return prepared


def run_analysis(prepared: PreparedData, tick: int) -> AnalysisResult:
    if not isinstance(tick, int) or not 0 <= tick <= 100:
        raise ServiceError("Threshold tick must be an integer from 0 to 100.")
    tau = tick / 100
    key = analysis_key(prepared.data_id, tau)
    result = _analyses.get(key)
    if result is None:
        result = analyze(prepared, tau)
        _analyses.put(key, result)
    return result


def comparison_rows(prepared: PreparedData) -> list[dict]:
    rows = _comparisons.get(prepared.data_id)
    if rows is None:
        rows = preset_comparison(prepared, TEACHING_PRESET_TICKS)
        _comparisons.put(prepared.data_id, rows)
    return rows


def _correlation_of(prepared: PreparedData, first: str, second: str) -> float:
    index = {symbol: i for i, symbol in enumerate(prepared.symbols)}
    return prepared.correlation[index[first]][index[second]]


def _property_cards(
    label: str,
    symbols: Symbols,
    matrix: BoolMatrix,
    report: PropertyReport,
    prepared: PreparedData | None = None,
    tau: float | None = None,
) -> list[dict]:
    n = len(symbols)
    cards = []
    reflexive = report.reflexive
    cards.append(
        {
            "name": "Reflexive",
            "statement": "∀a ∈ S: aRa",
            "holds": reflexive.holds,
            "text": f"Checked all {n} diagonal cells; every (a, a) is related."
            if reflexive.holds
            else f"Counterexample: ({reflexive.witness[0]}, {reflexive.witness[0]}) ∉ {label}.",
            "detail": [],
        }
    )
    witness = report.symmetric.witness
    cards.append(
        {
            "name": "Symmetric",
            "statement": "∀a, b: aRb ⇒ bRa",
            "holds": report.symmetric.holds,
            "text": f"Checked all {n * n} ordered pairs; no pair lacks its reverse."
            if witness is None
            else (
                f"Counterexample: ({witness[0]}, {witness[1]}) ∈ {label} "
                f"but ({witness[1]}, {witness[0]}) ∉ {label}."
            ),
            "detail": [],
        }
    )
    witness = report.transitive.witness
    detail: list[str] = []
    if witness is None:
        text = (
            f"Exhaustively checked all {count_transitivity_premises(matrix)} triples satisfying the "
            "premises; each conclusion held. This alone does not mean all stocks form one class."
        )
    else:
        a, b, c = witness
        text = f"Counterexample: ({a}, {b}) ∈ {label} and ({b}, {c}) ∈ {label}, but ({a}, {c}) ∉ {label}."
        if prepared is not None and tau is not None:
            if any(prepared.names.get(s, s) != s for s in (a, b, c)):
                detail.append("; ".join(labeled(prepared.names, s) for s in dict.fromkeys((a, b, c))))
            for x, y in ((a, b), (b, c), (a, c)):
                detail.append(f"C({x}, {y}) = {compare_text(_correlation_of(prepared, x, y), tau)}")
    cards.append(
        {
            "name": "Transitive",
            "statement": "∀a, b, c: (aRb ∧ bRc) ⇒ aRc",
            "holds": report.transitive.holds,
            "text": text,
            "detail": detail,
        }
    )
    return cards


def _witness_highlights(report: PropertyReport):
    cells: set[tuple[str, str]] = set()
    edges: set[tuple[str, str]] = set()
    missing: set[tuple[str, str]] = set()
    witness = report.transitive.witness
    if witness is not None:
        a, b, c = witness
        cells = {(a, b), (b, c), (a, c)}
        edges = {tuple(sorted(pair)) for pair in ((a, b), (b, c)) if pair[0] != pair[1]}
        if a != c:
            missing = {tuple(sorted((a, c)))}
    if report.symmetric.witness is not None:
        a, b = report.symmetric.witness
        cells |= {(a, b), (b, a)}
    if report.reflexive.witness is not None:
        a = report.reflexive.witness[0]
        cells.add((a, a))
    return cells, edges, missing


def analysis_payload(source: Source, prepared: PreparedData, result: AnalysisResult) -> dict:
    relation = result.relation
    tau = relation.threshold
    symbols = result.symbols
    names = prepared.names
    summary = prepared.validation_summary
    degree_map = degrees(relation.neighbors)
    class_of = {s: result.component_of(s) for s in symbols}
    cells, edges, missing = _witness_highlights(result.direct_properties)
    added = set(result.closure_added_pairs)
    covered = sorted(s for component in result.components for s in component)
    synthetic = prepared.source_mode in ("teaching_matrix", "synthetic_prices")

    return {
        "analysis_id": result.analysis_id,
        "data_id": prepared.data_id,
        "tau": tau,
        "source_mode": prepared.source_mode,
        "source_label": SOURCE_LABELS.get(prepared.source_mode, prepared.source_mode),
        "synthetic": synthetic,
        "symbols": list(symbols),
        "names": names,
        "sectors": prepared.sectors,
        "requested_start": prepared.requested_start,
        "requested_end": prepared.requested_end,
        "sample_count": prepared.sample_count,
        "candidate_return_rows": summary.candidate_return_rows,
        "removed_return_count": summary.removed_return_count,
        "correlation": prepared.correlation,
        "metrics": result.metrics,
        "relation": {
            "matrix": relation.matrix,
            "ordered_pairs": [list(p) for p in relation.ordered_pairs[:MAX_DISPLAYED_PAIRS]],
            "ordered_pair_total": len(relation.ordered_pairs),
            "edges": [
                {
                    "source": a,
                    "target": b,
                    "source_label": labeled(names, a),
                    "target_label": labeled(names, b),
                    "correlation": _correlation_of(prepared, a, b),
                }
                for a, b in relation.edges
            ],
        },
        "direct": {
            "is_equivalence": result.direct_properties.is_equivalence,
            "cards": _property_cards("R", symbols, relation.matrix, result.direct_properties, prepared, tau),
            "witness": list(result.direct_properties.transitive.witness or []),
        },
        "reachability": {
            "matrix": result.reachability_matrix,
            "cards": _property_cards(
                "Q", symbols, result.reachability_matrix, result.reachability_properties
            ),
            "closure_added_pairs": [list(p) for p in result.closure_added_pairs],
        },
        "components": [
            {
                "id": component[0],
                "members": format_set(component),
                "size": len(component),
                "stocks": labeled_members(names, component),
                "symbols": component,
            }
            for component in result.components
        ],
        "partition_checks": [
            {"check": "Union of classes = S", "ok": set(covered) == set(symbols)},
            {"check": "No overlapping classes", "ok": len(covered) == len(set(covered))},
            {"check": "No empty classes", "ok": all(result.components)},
            {"check": "Every stock assigned exactly once", "ok": covered == list(symbols)},
        ],
        "stock_rows": [
            {
                "symbol": s,
                "name": names.get(s, ""),
                "sector": prepared.sectors.get(s, ""),
                "degree": degree_map[s],
                "class_id": class_of[s],
            }
            for s in symbols
        ],
        "comparison": comparison_rows(prepared),
        "figures": {
            "correlation": _figure(
                charts.correlation_heatmap(symbols, prepared.correlation, "Correlation matrix C")
            ),
            "relation_matrix": _figure(charts.boolean_heatmap(symbols, relation.matrix, "Relation A")),
            "graph": _figure(charts.relation_graph(result, prepared.sectors, "Graph")),
            "witness_matrix": _figure(charts.boolean_heatmap(symbols, relation.matrix, "R", cells)),
            "witness_graph": _figure(charts.relation_graph(result, prepared.sectors, "G", edges, missing)),
            "reachability_matrix": _figure(
                charts.boolean_heatmap(symbols, result.reachability_matrix, "Q", added)
            ),
        },
        "validation": {
            "data_id": prepared.data_id,
            "analysis_id": result.analysis_id,
            "source_mode": prepared.source_mode,
            "sample_count": prepared.sample_count,
            "price_dates_in_window": summary.date_count,
            "candidate_return_rows": summary.candidate_return_rows,
            "retained_return_rows": summary.retained_return_rows,
            "removed_return_dates": list(summary.removed_return_dates),
            "missing_price_counts": summary.missing_price_counts,
            "warnings": list(summary.warnings),
            "normalization_notes": list(summary.normalization_notes),
            "provenance": prepared.provenance,
        },
        "provenance": source.provenance,
        "exports": list(all_exports(result, prepared)),
    }


# ------------------------------------------------------------------ module 1 and path


def sets_payload(source: Source, applied, x, y) -> dict:
    applied_set = set(applied)
    unknown = sorted(set(applied) - set(source.universe))
    if unknown:
        raise ServiceError("Unknown stock identifier(s): " + ", ".join(unknown))
    x = [s for s in x if s in applied_set]
    y = [s for s in y if s in applied_set]
    names = source.names
    subsets = sector_subsets(applied, source.sectors)
    operations = [
        ("X ∪ Y", set_union(x, y)),
        ("X ∩ Y", set_intersection(x, y)),
        ("X \\ Y", set_difference(x, y)),
        ("Y \\ X", set_difference(y, x)),
    ]
    return {
        "universe_size": len(source.universe),
        "applied_size": len(applied_set),
        "applied_subset": is_subset(applied, source.universe),
        "universe_text": format_set(source.universe),
        "applied_text": format_set(applied),
        "table": [
            {
                "symbol": s,
                "name": names.get(s, ""),
                "sector": source.sectors.get(s, ""),
                "in_s": s in applied_set,
                "price_data": "teaching matrix"
                if source.mode == "teaching_matrix"
                else ("available" if s in source.available else "unavailable"),
            }
            for s in source.universe
        ],
        "sector_subsets": [
            {
                "sector": sector,
                "members": format_set(members),
                "size": len(members),
                "stocks": labeled_members(names, members),
                "symbols": members,
            }
            for sector, members in subsets.items()
        ],
        "x": sorted_members(x),
        "y": sorted_members(y),
        "operations": [
            {
                "expression": name,
                "result": format_set(members),
                "size": len(members),
                "stocks": labeled_members(names, members),
                "symbols": sorted_members(members),
            }
            for name, members in operations
        ],
        "checks": [
            {"statement": "|X|", "value": str(len(set(x)))},
            {"statement": "|Y|", "value": str(len(set(y)))},
            {"statement": "X ⊆ Y", "value": is_subset(x, y)},
            {"statement": "Y ⊆ X", "value": is_subset(y, x)},
            {"statement": "X ⊆ S", "value": is_subset(x, applied)},
            {"statement": "∅ ⊆ X", "value": is_subset([], x)},
        ],
    }


def path_payload(prepared: PreparedData, result: AnalysisResult, start: str, end: str) -> dict:
    path = find_shortest_path(result.relation.neighbors, start, end)
    names = prepared.names
    if path is None:
        return {
            "found": False,
            "message": f"No path from {start} to {end} at this threshold, so ({start}, {end}) ∉ Q.",
        }
    if len(path) == 1:
        return {
            "found": True,
            "path": path,
            "message": f"{start} reaches itself by a zero-edge path, so ({start}, {start}) ∈ Q.",
            "steps": [],
        }
    steps = [
        {"from": a, "to": b, "correlation": _correlation_of(prepared, a, b)}
        for a, b in zip(path, path[1:], strict=False)
    ]
    return {
        "found": True,
        "path": path,
        "labels": [labeled(names, s) for s in path],
        "message": f"Path of {len(path) - 1} edge(s)",
        "steps": steps,
    }


__all__ = [
    "DataValidationError",
    "ServiceError",
    "analysis_payload",
    "app_config",
    "load_source",
    "path_payload",
    "prepare",
    "register_upload",
    "run_analysis",
    "sets_payload",
    "source_payload",
    "stats",
]
