"""StockRelate Streamlit entry point: state, invalidation and rerun orchestration.

Run with:  streamlit run app.py
"""

import datetime as dt
import json
import logging

import streamlit as st

from stockrelate import ui
from stockrelate.analysis import analysis_key, analyze, preset_comparison
from stockrelate.config import (
    APP_VERSION,
    DEFAULT_SETTINGS,
    DEFAULT_THRESHOLD_TICK,
    MAX_SELECTED_STOCKS,
    PREPROCESSING_POLICY_VERSION,
    TEACHING_PRESET_TICKS,
)
from stockrelate.exports import all_exports
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

logger = logging.getLogger("stockrelate")

SOURCE_OPTIONS = ["teaching_matrix", "synthetic_prices", "historical", "upload"]
SOURCE_NAMES = {
    "teaching_matrix": "Teaching matrix (5 stocks)",
    "synthetic_prices": "Bundled synthetic prices (12 stocks)",
    "historical": "Historical prices (Yahoo Finance snapshot)",
    "upload": "Upload CSV pair",
}
BUNDLED_LOADERS = {
    "synthetic_prices": load_bundled_dataset,
    "historical": load_historical_dataset,
}
STATE_DEFAULTS = {
    "source_fingerprint": None,
    "dataset": None,
    "applied_symbols": (),
    "applied_start_date": None,
    "applied_end_date": None,
    "prepared_key": None,
    "prepared_data": None,
    "apply_error": None,
    "analysis_key": None,
    "analysis_result": None,
    "analysis_error": None,
    "comparison_key": None,
    "comparison_rows": None,
    "pending_selection": None,
    "prepare_count": 0,
}


def init_state() -> None:
    state = st.session_state
    for key, value in STATE_DEFAULTS.items():
        if key not in state:
            state[key] = value
    if "source_mode" not in state:
        # Open on real market data when the Yahoo snapshot exists; else the teaching matrix.
        state["source_mode"] = "historical" if historical_dataset_available() else "teaching_matrix"
    if "threshold_tick" not in state:
        state["threshold_tick"] = DEFAULT_THRESHOLD_TICK


def clear_outputs() -> None:
    """Drop prepared data and analysis so nothing stale is rendered or downloaded."""
    state = st.session_state
    for key in (
        "prepared_key",
        "prepared_data",
        "analysis_key",
        "analysis_result",
        "analysis_error",
        "comparison_key",
        "comparison_rows",
        "apply_error",
    ):
        state[key] = None


# ------------------------------------------------------------------ sources


def load_source(source_mode: str):
    """Return (SourceView-ish info dict, dataset or None) for the current source."""
    if source_mode == "teaching_matrix":
        fixture = load_teaching_fixture()
        symbols = tuple(fixture["symbols"])
        return {
            "fingerprint": sha256_text("teaching|" + fixture["sha256"]),
            "universe": symbols,
            "names": fixture["names"],
            "sectors": fixture["sectors"],
            "available": frozenset(symbols),
            "dataset": None,
            "provenance": "Synthetic five-stock teaching correlation matrix; not market values.",
        }
    if source_mode in BUNDLED_LOADERS:
        cached: ValidatedDataset | None = st.session_state.get("dataset")
        if cached is not None and cached.source_mode == source_mode:
            dataset = cached
        else:
            dataset = BUNDLED_LOADERS[source_mode](DEFAULT_SETTINGS)
        return _dataset_info(dataset, provenance_text(dataset.provenance))

    metadata_file = st.sidebar.file_uploader(
        "Metadata CSV (symbol,name,sector)", type="csv", key="upload_metadata"
    )
    prices_file = st.sidebar.file_uploader(
        "Price CSV (date,symbol,adjusted_close)", type="csv", key="upload_prices"
    )
    if metadata_file is None or prices_file is None:
        return None
    metadata_bytes = metadata_file.getvalue()
    prices_bytes = prices_file.getvalue()
    # Content hashes, never filenames: replacing a file with new data invalidates results.
    content_key = sha256_bytes(metadata_bytes) + sha256_bytes(prices_bytes)
    cached: ValidatedDataset | None = st.session_state.get("dataset")
    if (
        cached is not None
        and cached.source_mode == "upload"
        and cached.metadata_sha256 + cached.prices_sha256 == content_key
    ):
        dataset = cached  # reuse validation on threshold-only reruns
    else:
        dataset = load_dataset(metadata_bytes, prices_bytes, DEFAULT_SETTINGS, "upload")
    return _dataset_info(dataset, "User supplied; provenance not independently verified.")


def provenance_text(provenance: dict) -> str:
    """One-line provenance for the footer; historical data names provider and retrieval date."""
    text = provenance.get("description", "")
    if provenance.get("kind") == "historical":
        first, last = provenance.get("date_range") or ("?", "?")
        text += (
            f" ({first} to {last}); {provenance.get('price_convention', '')}; "
            f"retrieved {provenance.get('retrieved_at', 'unknown')}. "
            "Selection is not an index-membership claim."
        )
    return text


def available_sources() -> list[str]:
    """Hide the historical option until scripts/fetch_yahoo_data.py has produced its files."""
    return [mode for mode in SOURCE_OPTIONS if mode != "historical" or historical_dataset_available()]


def _dataset_info(dataset: ValidatedDataset, provenance_line: str) -> dict:
    return {
        "fingerprint": dataset.fingerprint,
        "universe": dataset.universe,
        "names": dataset.names,
        "sectors": dataset.sectors,
        "available": dataset.available_symbols,
        "dataset": dataset,
        "provenance": provenance_line,
    }


def on_source_change(info: dict) -> None:
    """New source or replaced files: invalidate everything derived from the old one."""
    state = st.session_state
    clear_outputs()
    state["source_fingerprint"] = info["fingerprint"]
    state["dataset"] = info["dataset"]
    state["applied_symbols"] = ()
    state["applied_start_date"] = None
    state["applied_end_date"] = None
    available = [s for s in info["universe"] if s in info["available"]]
    state["draft_symbols"] = available[:MAX_SELECTED_STOCKS]
    dataset = info["dataset"]
    if dataset is not None and dataset.date_bounds() is not None:
        first, last = dataset.date_bounds()
        state["draft_start"] = dt.date.fromisoformat(first)
        state["draft_end"] = dt.date.fromisoformat(last)
    if state["source_mode"] != "upload":
        # Bundled sources have known-valid defaults: apply all stocks over the full window
        # immediately so every module shows a complete result. Uploads wait for Apply.
        start = state["draft_start"].isoformat() if dataset is not None else None
        end = state["draft_end"].isoformat() if dataset is not None else None
        apply_selection(info, tuple(state["draft_symbols"]), start, end)


# ------------------------------------------------------------------ apply


def prepared_key_for(info: dict, symbols, start, end) -> str:
    return sha256_text(
        json.dumps(
            {
                "source": info["fingerprint"],
                "mode": st.session_state["source_mode"],
                "symbols": sorted(symbols),
                "start": start,
                "end": end,
                "policy": PREPROCESSING_POLICY_VERSION,
                "min_obs": DEFAULT_SETTINGS.min_return_observations,
            },
            sort_keys=True,
        )
    )


def apply_selection(info: dict, symbols, start: str | None, end: str | None) -> None:
    """Validate and prepare in locals; only on success replace the applied snapshot."""
    state = st.session_state
    symbols = tuple(sorted(set(symbols)))
    key = prepared_key_for(info, symbols, start, end)
    if key == state.get("prepared_key") and state.get("prepared_data") is not None:
        return  # identical inputs: reuse the existing snapshot
    clear_outputs()
    state["applied_symbols"] = symbols
    state["applied_start_date"] = start
    state["applied_end_date"] = end
    if not symbols:
        return  # empty S is allowed for set inspection; analysis stays disabled
    try:
        if info["dataset"] is None:
            prepared = prepare_teaching_data(symbols)
        else:
            prepared = prepare_market_data(info["dataset"], symbols, start, end, DEFAULT_SETTINGS)
    except (DataValidationError, ValueError) as error:
        state["apply_error"] = str(error)
        return
    state["prepare_count"] += 1
    state["prepared_key"] = key
    state["prepared_data"] = prepared


def select_all(universe: list[str]) -> None:
    st.session_state["draft_symbols"] = universe[:MAX_SELECTED_STOCKS]


def clear_selection() -> None:
    st.session_state["draft_symbols"] = []


def use_as_selection(members: list[str]) -> None:
    st.session_state["pending_selection"] = list(members)
    st.session_state["draft_symbols"] = list(members)


def set_threshold_tick(tick: int) -> None:
    st.session_state["threshold_tick"] = tick


def reset_invalid_choices(symbols: tuple[str, ...]) -> None:
    """Fix widget state before the widgets are created in this run."""
    state = st.session_state
    for key in ("set_x", "set_y"):
        if key in state and any(s not in symbols for s in state[key]):
            state[key] = [s for s in state[key] if s in symbols]
    defaults = {"path_source": 0, "path_target": -1, "pair_source": 0, "pair_target": -1}
    for key, position in defaults.items():
        if symbols and state.get(key) not in symbols:
            state[key] = symbols[position]


# ------------------------------------------------------------------ page


def render_sidebar(info: dict | None) -> bool:
    """Draw the draft selection form. Returns True when Apply was pressed."""
    if info is None:
        st.sidebar.info("Upload both CSV files to continue.")
        return False
    universe = list(info["universe"])
    names, sectors = info["names"], info["sectors"]
    state = st.session_state
    # Only touch widget state when it is actually invalid; writing it on every run
    # would override the user's pending (submitted) widget value.
    draft = state.get("draft_symbols", [])
    if any(s not in universe for s in draft):
        state["draft_symbols"] = [s for s in draft if s in universe]

    st.sidebar.markdown("**Edit selection**")
    col1, col2 = st.sidebar.columns(2)
    col1.button("Select all", key="select_all", on_click=select_all, args=(universe,), width="stretch")
    col2.button("Clear", key="clear_selection", on_click=clear_selection, width="stretch")
    with st.sidebar.form("selection_form"):
        st.multiselect(
            f"Stocks (max {MAX_SELECTED_STOCKS})",
            universe,
            key="draft_symbols",
            max_selections=MAX_SELECTED_STOCKS,
            format_func=lambda s: (
                f"{s} · {names[s]} · {sectors[s]}" + ("" if s in info["available"] else " · unavailable")
            ),
        )
        dataset = info["dataset"]
        if dataset is not None and dataset.date_bounds() is not None:
            first, last = (dt.date.fromisoformat(d) for d in dataset.date_bounds())
            st.date_input("Start date (inclusive)", key="draft_start", min_value=first, max_value=last)
            st.date_input("End date (inclusive)", key="draft_end", min_value=first, max_value=last)
            st.caption(
                f"Prices available {first} to {last}. At least "
                f"{DEFAULT_SETTINGS.min_return_observations} aligned returns are required."
            )
        submitted = st.form_submit_button("Apply selection", type="primary", width="stretch")
    if dataset is not None and dataset.warnings:
        with st.sidebar.expander(f"Dataset warnings ({len(dataset.warnings)})"):
            for warning in dataset.warnings:
                st.text(warning)
    return submitted


def compute_analysis() -> None:
    """Recompute relation-dependent results only when data or threshold changed."""
    state = st.session_state
    prepared = state["prepared_data"]
    if prepared is None:
        return
    tau = state["threshold_tick"] / 100
    key = analysis_key(prepared.data_id, tau)
    if state["analysis_key"] == key and state["analysis_result"] is not None:
        return
    state["analysis_result"] = None
    state["analysis_error"] = None
    try:
        state["analysis_result"] = analyze(prepared, tau)
        state["analysis_key"] = key
        if state["comparison_key"] != prepared.data_id:
            state["comparison_rows"] = preset_comparison(prepared, TEACHING_PRESET_TICKS)
            state["comparison_key"] = prepared.data_id
    except Exception:  # application boundary: log and show an understandable error
        logger.exception("Analysis failed (data_id=%s, threshold=%s)", prepared.data_id, tau)
        state["analysis_key"] = None
        state["analysis_error"] = (
            "The analysis failed unexpectedly. Results and downloads are hidden. "
            "Details were written to the application log."
        )


def render_downloads(prepared, result) -> None:
    st.markdown("#### Downloads (current analysis)")
    files = all_exports(result, prepared)
    columns = st.columns(len(files))
    for column, (name, (content, mime)) in zip(columns, files.items(), strict=True):
        column.download_button(
            name, content, file_name=name, mime=mime, key=f"download_{name}", width="stretch"
        )


def main() -> None:
    st.set_page_config(page_title="StockRelate", page_icon="🔗", layout="wide")
    init_state()
    state = st.session_state

    st.sidebar.title("StockRelate")
    st.sidebar.radio(
        "Data source", available_sources(), key="source_mode", format_func=lambda mode: SOURCE_NAMES[mode]
    )

    info = None
    source_error = None
    try:
        info = load_source(state["source_mode"])
    except (DataValidationError, ValueError) as error:
        source_error = str(error)
    except Exception:
        logger.exception("Loading source %s failed", state["source_mode"])
        source_error = "The data source could not be loaded. See the application log."

    current_fingerprint = info["fingerprint"] if info else None
    if current_fingerprint != state["source_fingerprint"]:
        if info is None:
            clear_outputs()
            state["source_fingerprint"] = None
            state["dataset"] = None
            state["applied_symbols"] = ()
        else:
            on_source_change(info)

    submitted = render_sidebar(info)
    if info is not None:
        if submitted:
            start = state.get("draft_start")
            end = state.get("draft_end")
            apply_selection(
                info,
                state["draft_symbols"],
                start.isoformat() if start else None,
                end.isoformat() if end else None,
            )
        elif state["pending_selection"] is not None:
            apply_selection(
                info, state["pending_selection"], state["applied_start_date"], state["applied_end_date"]
            )
    state["pending_selection"] = None

    applied = tuple(state["applied_symbols"])
    reset_invalid_choices(applied)

    # Header
    st.title("StockRelate — Sets, Relations and Reachability")
    if source_error:
        st.error(source_error)
    if info is None:
        st.info("Choose a data source and provide its files to begin.")
        return
    if state["source_mode"] == "teaching_matrix":
        window = "no price window (teaching matrix)"
    elif state["applied_start_date"]:
        window = f"{state['applied_start_date']} to {state['applied_end_date']}"
    else:
        window = "not applied yet"
    st.markdown(
        f"**Source:** {SOURCE_NAMES[state['source_mode']]}  ·  "
        f"**Applied S:** {len(applied)} stock(s)  ·  **Window:** {window}"
    )
    if state["apply_error"]:
        st.error("Selection could not be analyzed: " + state["apply_error"])
    elif not applied:
        st.warning("No applied selection. Choose stocks in the sidebar and press **Apply selection**.")

    st.slider("Threshold tick (τ = tick / 100) — shared by all modules", 0, 100, key="threshold_tick", step=1)
    tau = state["threshold_tick"] / 100
    compute_analysis()
    prepared = state["prepared_data"]
    result = state["analysis_result"]
    if state["analysis_error"]:
        st.error(state["analysis_error"])
    if result is not None:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("τ", f"{tau:.2f}")
        col2.metric("Nodes", result.metrics["node_count"])
        col3.metric("Edges", result.metrics["edge_count"])
        col4.metric("Reachability classes", result.metrics["component_count"])

    view = ui.SourceView(
        source_mode=state["source_mode"],
        universe=tuple(info["universe"]),
        names=info["names"],
        sectors=info["sectors"],
        available=info["available"],
        applied=applied,
    )
    tabs = st.tabs(["1 Sets", "2 Direct relation", "3 Properties", "4 Reachability", "5 Explorer"])
    with tabs[0]:
        ui.render_sets_tab(view, use_as_selection)
    renderers = [
        lambda: ui.render_relation_tab(prepared, result),
        lambda: ui.render_properties_tab(prepared, result),
        lambda: ui.render_reachability_tab(prepared, result),
        lambda: ui.render_explorer_tab(prepared, result, state["comparison_rows"], set_threshold_tick),
    ]
    for tab, render in zip(tabs[1:], renderers, strict=True):
        with tab:
            if result is None:
                st.info("Modules 2–5 need a valid applied selection of at least one stock.")
            else:
                render()

    st.divider()
    if result is not None and prepared is not None:
        render_downloads(prepared, result)
        summary = prepared.validation_summary
        with st.expander("Validation summary and provenance"):
            st.json(
                {
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
                }
            )
    st.caption(
        f"StockRelate v{APP_VERSION} · {info['provenance']} · Correlation describes "
        "association in the sample only; it is not a forecast or financial advice."
    )


main()
