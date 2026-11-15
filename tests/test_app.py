"""Behaviour tests for the web API (the browser holds selection and threshold state)."""

import pytest
from fastapi.testclient import TestClient

from server import app
from stockrelate import service
from stockrelate.market_data import historical_dataset_available
from tests.conftest import FIXTURES

client = TestClient(app)
TEACHING = ["A", "B", "C", "D", "E"]


def analyze(symbols=TEACHING, tick=65, source="teaching_matrix", **extra):
    response = client.post("/api/analyze", json={"source": source, "symbols": symbols, "tick": tick, **extra})
    return response


def test_index_and_static_assets_are_served():
    page = client.get("/")
    assert page.status_code == 200
    assert "StockRelate" in page.text
    for asset in ("/static/styles.css", "/static/app.js", "/static/favicon.svg"):
        assert client.get(asset).status_code == 200


def test_config_lists_sources_and_defaults():
    config = client.get("/api/config").json()
    ids = [source["id"] for source in config["sources"]]
    assert ids[0] == "teaching_matrix" and "upload" in ids
    assert config["default_tick"] == 65
    assert config["preset_ticks"] == [20, 65, 75, 90]
    assert config["default_source"] == ("historical" if historical_dataset_available() else "teaching_matrix")


@pytest.mark.skipif(not historical_dataset_available(), reason="historical snapshot not fetched")
def test_historical_source_applies_all_sixteen():
    source = client.get("/api/sources/historical").json()
    assert source["auto_apply"] is True
    assert len(source["default_selection"]) == 16
    start, end = source["date_bounds"]
    result = analyze(source["default_selection"], source="historical", start=start, end=end).json()
    assert result["names"]["AAPL"] == "Apple Inc."
    assert result["tau"] == 0.65
    assert result["metrics"]["node_count"] == 16


def test_teaching_matrix_applies_all_five():
    source = client.get("/api/sources/teaching_matrix").json()
    assert source["default_selection"] == TEACHING
    assert source["date_bounds"] is None
    result = analyze().json()
    assert result["symbols"] == TEACHING
    assert result["metrics"]["component_count"] == 2
    assert set(result["figures"]) >= {"correlation", "graph", "reachability_matrix"}
    assert "template" not in result["figures"]["graph"]["layout"]


def test_threshold_change_does_not_reprepare():
    analyze(tick=65)
    before = service.stats["prepare_count"]
    result = analyze(tick=75).json()
    assert result["metrics"]["component_count"] == 3
    assert result["tau"] == 0.75
    assert service.stats["prepare_count"] == before


def test_preset_ninety_isolates_everything():
    result = analyze(tick=90).json()
    assert result["metrics"]["edge_count"] == 0
    assert result["metrics"]["component_count"] == 5


def test_teaching_transitivity_counterexample_at_065():
    result = analyze(tick=65).json()
    transitive = result["direct"]["cards"][2]
    assert transitive["holds"] is False
    assert result["direct"]["witness"]
    assert result["reachability"]["cards"][2]["holds"] is True
    assert result["reachability"]["closure_added_pairs"]


def test_subset_selection_is_canonical():
    result = analyze(["C", "A", "B"]).json()
    assert result["symbols"] == ["A", "B", "C"]


def test_empty_selection_is_rejected_for_analysis():
    response = analyze([])
    assert response.status_code == 422
    assert "at least one" in response.json()["detail"]


def test_unknown_symbol_is_a_user_error():
    response = analyze(["A", "ZZZ"])
    assert response.status_code == 422
    assert "ZZZ" in response.json()["detail"]


def test_tick_out_of_range_is_rejected():
    assert analyze(tick=101).status_code == 422


def test_sets_operations_and_use_as_selection_flow():
    body = {"source": "teaching_matrix", "applied": TEACHING, "x": ["A", "B", "C"], "y": ["B", "D"]}
    sets = client.post("/api/sets", json=body).json()
    operations = {op["expression"]: op for op in sets["operations"]}
    assert operations["X ∪ Y"]["symbols"] == ["A", "B", "C", "D"]
    assert operations["X ∩ Y"]["result"] == "{B}"
    assert operations["X \\ Y"]["size"] == 2
    assert sets["applied_size"] == 5  # inspection never changes S
    # the browser then applies the union as the new S
    result = analyze(operations["X ∪ Y"]["symbols"]).json()
    assert result["metrics"]["node_count"] == 4


def test_sets_with_empty_selection():
    sets = client.post("/api/sets", json={"source": "teaching_matrix", "applied": []}).json()
    assert sets["applied_text"] == "∅"
    assert sets["sector_subsets"] == []
    assert sets["universe_size"] == 5


def test_shortest_path():
    body = {"source": "teaching_matrix", "symbols": TEACHING, "tick": 65, "path_from": "A", "path_to": "C"}
    path = client.post("/api/path", json=body).json()
    assert path["found"] is True
    assert path["path"][0] == "A" and path["path"][-1] == "C"
    unreachable = client.post("/api/path", json={**body, "tick": 90}).json()
    assert unreachable["found"] is False


def test_switching_to_bundled_source():
    source = client.get("/api/sources/synthetic_prices").json()
    start, end = source["date_bounds"]
    result = analyze(source["default_selection"], source="synthetic_prices", start=start, end=end).json()
    assert result["sample_count"] == 250
    assert result["metrics"]["node_count"] == 12


def test_upload_round_trip():
    files = {
        "metadata": ("m.csv", (FIXTURES / "metadata.csv").read_bytes(), "text/csv"),
        "prices": ("p.csv", (FIXTURES / "missing_prices.csv").read_bytes(), "text/csv"),
    }
    response = client.post("/api/upload", files=files)
    assert response.status_code == 200, response.text
    source = response.json()
    assert source["mode"] == "upload"
    assert source["auto_apply"] is False
    assert source["dataset_key"].startswith("upload:")


def test_upload_source_requires_known_key():
    response = analyze(["A"], source="upload", dataset_key="upload:missing")
    assert response.status_code == 422


def test_exports_download():
    body = {"source": "teaching_matrix", "symbols": TEACHING, "tick": 65}
    response = client.post("/api/export/components.csv", json=body)
    assert response.status_code == 200
    assert response.text.startswith("component_id,symbol")
    assert "attachment" in response.headers["content-disposition"]
    assert client.post("/api/export/nope.csv", json=body).status_code == 404
