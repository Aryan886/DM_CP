import json

import numpy as np
import pytest

from stockrelate.config import Settings
from stockrelate.market_data import (
    DATA_DIR,
    DataValidationError,
    canonicalize_correlation,
    load_bundled_dataset,
    load_dataset,
    parse_metadata,
    prepare_market_data,
    prepare_teaching_data,
    sha256_bytes,
)
from tests.conftest import fixture_bytes

TINY = Settings(min_return_observations=5)
WINDOW = ("2025-01-01", "2025-12-31")


def tiny_dataset(prices_name="tiny_prices.csv"):
    return load_dataset(fixture_bytes("tiny_metadata.csv"), fixture_bytes(prices_name), TINY)


def csv_bytes(lines):
    return ("\n".join(lines) + "\n").encode("utf-8")


def prices_from_series(series: dict[str, list[float]]) -> bytes:
    lines = ["date,symbol,adjusted_close"]
    for t in range(len(next(iter(series.values())))):
        date = f"2025-02-{t + 1:02d}"
        for symbol, values in series.items():
            lines.append(f"{date},{symbol},{values[t]!r}")
    return csv_bytes(lines)


# ---------------------------------------------------------------- tiny fixture


def test_tiny_fixture_returns_and_correlation():
    prepared = prepare_market_data(tiny_dataset(), ["A", "B", "C"], *WINDOW, TINY)
    assert prepared.symbols == ("A", "B", "C")
    assert prepared.sample_count == 5 == len(prepared.return_dates)
    expected = np.array([[1, 1, -1], [1, 1, -1], [-1, -1, 1]], dtype=float)
    assert np.allclose(np.array(prepared.correlation), expected, atol=1e-9)
    assert prepared.correlation[0][0] == 1.0


def test_tiny_fixture_rejected_by_default_minimum():
    dataset = load_dataset(fixture_bytes("tiny_metadata.csv"), fixture_bytes("tiny_prices.csv"))
    with pytest.raises(DataValidationError, match="Only 5 aligned return"):
        prepare_market_data(dataset, ["A", "B", "C"], *WINDOW)


def test_tiny_fixture_classes_at_065():
    from stockrelate.analysis import analyze

    result = analyze(prepare_market_data(tiny_dataset(), ["A", "B", "C"], *WINDOW, TINY), 0.65)
    assert result.relation.edges == [("A", "B")]
    assert result.components == [["A", "B"], ["C"]]


def test_missing_middle_price_discards_both_touching_returns():
    settings = Settings(min_return_observations=3)
    dataset = load_dataset(fixture_bytes("tiny_metadata.csv"), fixture_bytes("missing_prices.csv"), settings)
    prepared = prepare_market_data(dataset, ["A", "B", "C"], *WINDOW, settings)
    assert prepared.return_dates == ("2025-01-03", "2025-01-08", "2025-01-09")
    summary = prepared.validation_summary
    assert summary.removed_return_dates == ("2025-01-06", "2025-01-07")
    assert summary.missing_price_counts == {"A": 0, "B": 1, "C": 0}
    assert summary.candidate_return_rows == 5


def test_selection_change_affects_retained_dates():
    settings = Settings(min_return_observations=3)
    dataset = load_dataset(fixture_bytes("tiny_metadata.csv"), fixture_bytes("missing_prices.csv"), settings)
    prepared = prepare_market_data(dataset, ["A", "C"], *WINDOW, settings)
    assert prepared.sample_count == 5  # B's gap no longer matters


def test_unsorted_input_is_accepted():
    lines = fixture_bytes("tiny_prices.csv").decode().strip().splitlines()
    shuffled = [lines[0], *reversed(lines[1:])]
    dataset = load_dataset(fixture_bytes("tiny_metadata.csv"), csv_bytes(shuffled), TINY)
    prepared = prepare_market_data(dataset, ["A", "B", "C"], *WINDOW, TINY)
    assert prepared.sample_count == 5


def test_window_boundaries_are_inclusive_and_have_no_lookback():
    settings = Settings(min_return_observations=2)
    prepared = prepare_market_data(tiny_dataset(), ["A", "B"], "2025-01-06", "2025-01-08", settings)
    # Grid 01-06, 01-07, 01-08 -> returns ending 01-07 and 01-08 only.
    assert prepared.return_dates == ("2025-01-07", "2025-01-08")


def test_differing_coverage_uses_common_dates():
    lines = ["date,symbol,adjusted_close"]
    a = [100, 101, 99, 102, 100, 103, 101]
    b = [50, 51, 52, 50, 53, 51, 52]
    for t in range(7):
        lines.append(f"2025-03-{t + 1:02d},A,{a[t]}")
        if t >= 2:  # B starts later
            lines.append(f"2025-03-{t + 1:02d},B,{b[t]}")
    settings = Settings(min_return_observations=2)
    dataset = load_dataset(fixture_bytes("tiny_metadata.csv"), csv_bytes(lines), settings)
    prepared = prepare_market_data(dataset, ["A", "B"], *WINDOW, settings)
    assert prepared.return_dates == ("2025-03-04", "2025-03-05", "2025-03-06", "2025-03-07")


@pytest.mark.parametrize(
    "minimum, accepted",
    [(4, True), (5, True), (6, False)],
)
def test_sample_size_boundary(minimum, accepted):
    settings = Settings(min_return_observations=minimum)
    dataset = tiny_dataset()
    if accepted:
        assert prepare_market_data(dataset, ["A", "B", "C"], *WINDOW, settings).sample_count == 5
    else:
        with pytest.raises(DataValidationError, match="Only 5"):
            prepare_market_data(dataset, ["A", "B", "C"], *WINDOW, settings)


@pytest.mark.parametrize("window", [("2025-01-02", "2025-01-02"), ("2025-01-02", "2025-01-03")])
def test_zero_and_one_return_rows_rejected(window):
    with pytest.raises(DataValidationError):
        prepare_market_data(tiny_dataset(), ["A", "B"], *window, TINY)


def test_settings_reject_minimum_below_two():
    with pytest.raises(ValueError):
        Settings(min_return_observations=1)


# ---------------------------------------------------------------- degenerate series


def test_constant_price_stock_rejected():
    content = prices_from_series({"A": [100, 110, 99, 108, 98, 107], "B": [50.0] * 6})
    dataset = load_dataset(fixture_bytes("tiny_metadata.csv"), content, TINY)
    with pytest.raises(DataValidationError, match="B"):
        prepare_market_data(dataset, ["A", "B"], *WINDOW, TINY)


def test_geometric_growth_stock_rejected():
    growth = [100 * 1.01**t for t in range(6)]
    content = prices_from_series({"A": [100, 110, 99, 108, 98, 107], "C": growth})
    dataset = load_dataset(fixture_bytes("tiny_metadata.csv"), content, TINY)
    with pytest.raises(DataValidationError, match="C"):
        prepare_market_data(dataset, ["A", "C"], *WINDOW, TINY)


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "lines, message",
    [
        (["symbol,name,sector", "A,Alpha,Banking", "a ,Alpha 2,Banking"], "duplicates"),
        (["symbol,name", "A,Alpha"], "missing required column"),
        (["symbol,name,sector", "A!,Alpha,Banking"], "invalid symbol"),
        (["symbol,name,sector", "A,,Banking"], "empty name"),
        (["symbol,symbol,sector", "A,A,Banking"], "duplicate column"),
    ],
)
def test_invalid_metadata_rejected(lines, message):
    with pytest.raises(DataValidationError, match=message):
        parse_metadata(csv_bytes(lines))


def test_missing_sector_becomes_unknown_with_warning():
    metadata, warnings = parse_metadata(csv_bytes(["symbol,name,sector,extra", "a.ns,Alpha,,x"]))
    assert metadata.to_dict("records") == [{"symbol": "A.NS", "name": "Alpha", "sector": "Unknown"}]
    assert any("Unknown" in w for w in warnings)
    assert any("extra" in w for w in warnings)


def test_non_utf8_rejected():
    with pytest.raises(DataValidationError, match="UTF-8"):
        parse_metadata(b"symbol,name,sector\nA,\xff\xfe,Banking\n")


@pytest.mark.parametrize(
    "row, message",
    [
        ("2025-01-10,A,0", "strictly positive"),
        ("2025-01-10,A,-5", "strictly positive"),
        ("2025-01-10,A,nan", "strictly positive"),
        ("2025-01-10,A,inf", "strictly positive"),
        ("2025-01-10,A,abc", "not a number"),
        ("2025-01-10,A,", "not a number"),
        ("10/01/2025,A,100", "ISO"),
        ("2025-02-30,A,100", "ISO"),
        ("2025-01-10T00:00,A,100", "ISO"),
        ("2025-01-10,ZZZ,100", "not in the metadata"),
        ("2025-01-02,A,100", "duplicate"),
    ],
)
def test_invalid_price_rows_rejected(row, message):
    content = fixture_bytes("tiny_prices.csv") + (row + "\n").encode()
    with pytest.raises(DataValidationError, match=message) as info:
        load_dataset(fixture_bytes("tiny_metadata.csv"), content, TINY)
    assert info.value.total == 1


def test_error_count_reports_all_problems():
    content = fixture_bytes("tiny_prices.csv") + b"2025-01-10,A,0\n2025-01-11,A,-1\n2025-01-12,Q,1\n"
    with pytest.raises(DataValidationError) as info:
        load_dataset(fixture_bytes("tiny_metadata.csv"), content, TINY)
    assert info.value.total == 3


def test_stock_without_prices_is_unavailable_and_rejected_if_selected():
    metadata = fixture_bytes("tiny_metadata.csv") + b"D,Delta,Energy\n"
    dataset = load_dataset(metadata, fixture_bytes("tiny_prices.csv"), TINY)
    assert "D" not in dataset.available_symbols
    assert any("D" in w for w in dataset.warnings)
    with pytest.raises(DataValidationError, match="D"):
        prepare_market_data(dataset, ["A", "D"], *WINDOW, TINY)


def test_selection_and_window_validation():
    dataset = tiny_dataset()
    with pytest.raises(ValueError, match="ZZ"):
        prepare_market_data(dataset, ["A", "ZZ"], *WINDOW, TINY)
    with pytest.raises(DataValidationError, match="after"):
        prepare_market_data(dataset, ["A"], "2025-02-01", "2025-01-01", TINY)
    with pytest.raises(DataValidationError, match="at least one"):
        prepare_market_data(dataset, [], *WINDOW, TINY)


# ---------------------------------------------------------------- resource limits


def test_oversized_file_rejected():
    settings = Settings(max_csv_bytes=50)
    with pytest.raises(DataValidationError, match="limit"):
        load_dataset(fixture_bytes("tiny_metadata.csv"), fixture_bytes("tiny_prices.csv"), settings)


def test_too_many_rows_rejected():
    settings = Settings(max_price_rows=10)
    with pytest.raises(DataValidationError, match="rows"):
        load_dataset(fixture_bytes("tiny_metadata.csv"), fixture_bytes("tiny_prices.csv"), settings)


def test_too_many_selected_and_dates_rejected():
    dataset = tiny_dataset()
    with pytest.raises(DataValidationError, match="limit"):
        prepare_market_data(
            dataset, ["A", "B", "C"], *WINDOW, Settings(min_return_observations=5, max_selected_stocks=2)
        )
    with pytest.raises(DataValidationError, match="price dates"):
        prepare_market_data(
            dataset, ["A", "B"], *WINDOW, Settings(min_return_observations=2, max_price_dates=3)
        )


def test_too_many_metadata_stocks_rejected():
    lines = ["symbol,name,sector"] + [f"S{i},Name {i},X" for i in range(5)]
    with pytest.raises(DataValidationError, match="limit"):
        parse_metadata(csv_bytes(lines), Settings(max_metadata_stocks=4))


# ---------------------------------------------------------------- correlation canonicalization


def test_canonicalize_repairs_only_tiny_deviations():
    raw = np.array([[1.0 + 1e-14, 0.5 + 1e-14], [0.5, 1.0]])
    matrix, notes = canonicalize_correlation(("A", "B"), raw, 1e-12)
    assert matrix[0][0] == 1.0 and matrix[0][1] == matrix[1][0]
    assert notes
    with pytest.raises(DataValidationError, match="asymmetric"):
        canonicalize_correlation(("A", "B"), np.array([[1.0, 0.5], [0.4, 1.0]]), 1e-12)
    with pytest.raises(DataValidationError, match="undefined"):
        canonicalize_correlation(("A", "B"), np.array([[1.0, np.nan], [np.nan, 1.0]]), 1e-12)


def test_tiny_boundary_overshoot_is_clipped():
    raw = np.array([[1.0, 1.0 + 1e-14], [1.0 + 1e-14, 1.0]])
    matrix, _ = canonicalize_correlation(("A", "B"), raw, 1e-12)
    assert matrix[0][1] == 1.0


# ---------------------------------------------------------------- teaching and bundled data


def test_teaching_matrix_is_psd_and_contract(teaching_data):
    raw = json.loads((DATA_DIR / "teaching_correlation.json").read_text())
    assert np.linalg.eigvalsh(np.array(raw["correlation"])).min() >= -1e-12
    assert teaching_data.sample_count is None
    assert teaching_data.return_dates == ()
    assert teaching_data.requested_start is None
    assert teaching_data.source_mode == "teaching_matrix"


def test_teaching_subset_selection():
    prepared = prepare_teaching_data(["C", "A"])
    assert prepared.symbols == ("A", "C")
    assert prepared.correlation == [[1.0, 0.3], [0.3, 1.0]]
    with pytest.raises(ValueError):
        prepare_teaching_data(["A", "Q"])


def test_bundled_dataset_hashes_and_invariants():
    sources = json.loads((DATA_DIR / "sources.json").read_text())
    entry = next(d for d in sources["datasets"] if d["dataset_id"] == "synthetic-demo-v1")
    assert entry["prices_sha256"] == sha256_bytes((DATA_DIR / "demo_prices.csv").read_bytes())
    assert "REPLACE" not in json.dumps(sources)
    dataset = load_bundled_dataset()
    assert len(dataset.universe) == 12
    assert set(dataset.sectors.values()) == {"Banking", "Energy", "Technology"}
    prepared = prepare_market_data(dataset, dataset.universe, *WINDOW)
    assert prepared.sample_count == 250
    matrix = np.array(prepared.correlation)
    assert np.allclose(matrix, matrix.T) and np.all(np.diag(matrix) == 1.0)
    assert np.linalg.eigvalsh(matrix).min() > -1e-12


def test_content_hash_changes_data_id():
    base = tiny_dataset()
    modified_bytes = fixture_bytes("tiny_prices.csv").replace(b"107.811", b"107.9")
    modified = load_dataset(fixture_bytes("tiny_metadata.csv"), modified_bytes, TINY)
    first = prepare_market_data(base, ["A", "B", "C"], *WINDOW, TINY)
    second = prepare_market_data(modified, ["A", "B", "C"], *WINDOW, TINY)
    assert first.data_id != second.data_id


@pytest.mark.skipif(
    not (DATA_DIR / "historical" / "prices.csv").exists(), reason="historical snapshot not fetched"
)
def test_historical_snapshot_hashes_provenance_and_invariants():
    from stockrelate.analysis import analyze
    from stockrelate.market_data import load_historical_dataset

    dataset = load_historical_dataset()
    entry = dataset.provenance
    assert entry["kind"] == "historical"
    for field_name in ("provider", "retrieved_at", "price_convention", "selection_basis", "source_url"):
        assert entry[field_name]
    bounds = dataset.date_bounds()
    prepared = prepare_market_data(dataset, dataset.universe, *bounds)
    assert prepared.source_mode == "historical"
    assert prepared.sample_count >= 30
    matrix = np.array(prepared.correlation)
    assert np.allclose(matrix, matrix.T) and np.all(np.diag(matrix) == 1.0)
    assert np.linalg.eigvalsh(matrix).min() > -1e-9
    for tick in range(0, 101, 5):
        result = analyze(prepared, tick / 100)  # analyze() raises if BFS and Warshall disagree
        assert sum(len(c) for c in result.components) == len(dataset.universe)
