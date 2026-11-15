from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from stockrelate.market_data import historical_dataset_available

SUBMIT = "FormSubmitter:selection_form-Apply selection"
APP = str(Path(__file__).resolve().parent.parent / "app.py")


def start():
    app = AppTest.from_file(APP, default_timeout=60).run()
    assert len(app.exception) == 0, app.exception
    return app


def start_teaching():
    """Switch to the synthetic A–E teaching matrix, which applies all five stocks."""
    app = start()
    app.radio(key="source_mode").set_value("teaching_matrix").run()
    assert len(app.exception) == 0, app.exception
    return app


def submit_selection(app, symbols):
    """Form values are only sent with the submit click, so both happen in one run."""
    app.multiselect(key="draft_symbols").set_value(symbols)
    app.button(key=SUBMIT).click()
    app.run()


@pytest.mark.skipif(not historical_dataset_available(), reason="historical snapshot not fetched")
def test_first_launch_shows_real_historical_data():
    app = start()
    prepared = app.session_state["prepared_data"]
    result = app.session_state["analysis_result"]
    assert app.session_state["source_mode"] == "historical"
    assert prepared.source_mode == "historical"
    assert prepared.names["AAPL"] == "Apple Inc."
    assert result.relation.threshold == 0.65
    assert result.metrics["node_count"] == len(prepared.symbols) == 16
    assert app.session_state["prepare_count"] == 1


def test_teaching_matrix_applies_all_five():
    app = start_teaching()
    result = app.session_state["analysis_result"]
    assert app.session_state["applied_symbols"] == ("A", "B", "C", "D", "E")
    assert result.metrics["component_count"] == 2


def test_slider_updates_analysis_without_reprepare():
    app = start_teaching()
    prepare_count = app.session_state["prepare_count"]
    app.slider(key="threshold_tick").set_value(75).run()
    assert len(app.exception) == 0
    result = app.session_state["analysis_result"]
    assert result.metrics["component_count"] == 3
    assert result.relation.threshold == 0.75
    assert app.session_state["prepare_count"] == prepare_count


def test_preset_button_sets_shared_threshold():
    app = start_teaching()
    app.button(key="preset_90").click().run()
    assert app.session_state["threshold_tick"] == 90
    assert app.session_state["analysis_result"].metrics["edge_count"] == 0


def test_selection_shrink_resets_path_endpoints():
    app = start_teaching()
    app.selectbox(key="path_target").set_value("E").run()
    submit_selection(app, ["A", "B", "C"])
    assert len(app.exception) == 0
    assert app.session_state["applied_symbols"] == ("A", "B", "C")
    assert app.session_state["path_target"] in ("A", "B", "C")
    assert app.session_state["analysis_result"].relation.symbols == ("A", "B", "C")


def test_empty_selection_disables_analysis():
    app = start_teaching()
    submit_selection(app, [])
    assert len(app.exception) == 0
    assert app.session_state["applied_symbols"] == ()
    assert app.session_state["analysis_result"] is None


def test_use_set_result_as_selection():
    app = start_teaching()
    app.multiselect(key="set_x").set_value(["A", "B", "C"]).run()
    app.multiselect(key="set_y").set_value(["B", "D"]).run()
    assert app.session_state["applied_symbols"] == ("A", "B", "C", "D", "E")  # inspection only
    app.button(key="use_X ∪ Y").click().run()
    assert app.session_state["applied_symbols"] == ("A", "B", "C", "D")
    assert app.session_state["analysis_result"].metrics["node_count"] == 4


def test_switching_to_bundled_source_replaces_results():
    app = start_teaching()
    old_id = app.session_state["prepared_data"].data_id
    app.radio(key="source_mode").set_value("synthetic_prices").run()
    assert len(app.exception) == 0
    prepared = app.session_state["prepared_data"]
    assert prepared.data_id != old_id
    assert prepared.sample_count == 250
    assert app.session_state["analysis_result"].metrics["node_count"] == 12


def test_switching_to_upload_clears_outputs_until_files_and_apply():
    app = start_teaching()
    app.radio(key="source_mode").set_value("upload").run()
    assert len(app.exception) == 0
    assert app.session_state["prepared_data"] is None
    assert app.session_state["analysis_result"] is None
    assert app.session_state["applied_symbols"] == ()


def test_sessions_are_independent():
    first = start_teaching()
    second = start_teaching()
    first.slider(key="threshold_tick").set_value(90).run()
    second.run()
    assert second.session_state["threshold_tick"] == 65
    assert second.session_state["analysis_result"].metrics["component_count"] == 2
    assert first.session_state["analysis_result"].metrics["component_count"] == 5
