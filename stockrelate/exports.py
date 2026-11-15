"""Export files generated from one successful AnalysisResult (Section 5.6)."""

import csv
import io
import json

from stockrelate.config import APP_VERSION, EXPORT_SCHEMA_VERSION
from stockrelate.models import AnalysisResult, BoolMatrix, PreparedData, PropertyReport, Symbols


def _csv_text(rows: list[list]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    return buffer.getvalue()


def matrix_csv(symbols: Symbols, matrix: BoolMatrix) -> str:
    rows: list[list] = [["symbol", *symbols]]
    for symbol, row in zip(symbols, matrix, strict=True):
        rows.append([symbol, *(1 if value else 0 for value in row)])
    return _csv_text(rows)


def ordered_pairs_csv(result: AnalysisResult) -> str:
    return _csv_text([["source", "target"], *map(list, result.relation.ordered_pairs)])


def edges_csv(result: AnalysisResult, prepared: PreparedData) -> str:
    index = {symbol: i for i, symbol in enumerate(prepared.symbols)}
    rows: list[list] = [["source", "target", "correlation"]]
    for source, target in result.relation.edges:
        rows.append([source, target, repr(prepared.correlation[index[source]][index[target]])])
    return _csv_text(rows)


def components_csv(result: AnalysisResult) -> str:
    rows: list[list] = [["component_id", "symbol"]]
    for component in result.components:
        for symbol in component:
            rows.append([component[0], symbol])
    return _csv_text(rows)


def _report_dict(report: PropertyReport) -> dict:
    return {
        name: {
            "holds": getattr(report, name).holds,
            "witness": list(getattr(report, name).witness) if getattr(report, name).witness else None,
        }
        for name in ("reflexive", "symmetric", "transitive")
    }


def analysis_json(result: AnalysisResult, prepared: PreparedData) -> str:
    symbols = list(result.symbols)
    payload = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "app_version": APP_VERSION,
        "analysis_id": result.analysis_id,
        "data_id": result.data_id,
        "source_mode": prepared.source_mode,
        "provenance": prepared.provenance,
        "selection": symbols,
        "requested_start": prepared.requested_start,
        "requested_end": prepared.requested_end,
        "return_dates": list(prepared.return_dates),
        "sample_count": prepared.sample_count,
        "threshold": result.relation.threshold,
        "comparison_rule": "(a, b) in R iff correlation[a][b] >= threshold (signed, inclusive, unrounded)",
        "direct_properties": _report_dict(result.direct_properties),
        "reachability_properties": _report_dict(result.reachability_properties),
        "components": result.components,
        "metrics": result.metrics,
        "closure_added_pairs": [list(pair) for pair in result.closure_added_pairs],
        "warnings": list(prepared.validation_summary.warnings),
        "normalization_notes": list(prepared.validation_summary.normalization_notes),
        "correlation": prepared.correlation,
        "relation_matrix": result.relation.matrix,
        "reachability_matrix": result.reachability_matrix,
    }
    return json.dumps(payload, indent=2, allow_nan=False, ensure_ascii=False) + "\n"


def all_exports(result: AnalysisResult, prepared: PreparedData) -> dict[str, tuple[str, str]]:
    """Map filename -> (content, MIME type)."""
    if result.data_id != prepared.data_id:
        raise ValueError("Analysis result does not belong to the prepared data supplied.")
    return {
        "relation_matrix.csv": (matrix_csv(result.symbols, result.relation.matrix), "text/csv"),
        "reachability_matrix.csv": (matrix_csv(result.symbols, result.reachability_matrix), "text/csv"),
        "ordered_pairs.csv": (ordered_pairs_csv(result), "text/csv"),
        "edges.csv": (edges_csv(result, prepared), "text/csv"),
        "components.csv": (components_csv(result), "text/csv"),
        "analysis.json": (analysis_json(result, prepared), "application/json"),
    }
