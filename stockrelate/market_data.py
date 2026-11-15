"""CSV parsing, validation, aligned returns and Pearson correlation.

Follows Section 6.1 of the project plan exactly: union date grid, explicit
``P[t] / P[t-1] - 1`` returns, listwise removal of return rows that touch a
missing price, and no filling or gap bridging.
"""

import datetime as dt
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from stockrelate.config import DEFAULT_SETTINGS, PREPROCESSING_POLICY_VERSION, Settings
from stockrelate.models import FloatMatrix, PreparedData, Symbols, ValidationSummary
from stockrelate.sets import canonical_selection

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._&-]{0,31}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_REPORTED_ERRORS = 10
UPLOAD_PROVENANCE = "User supplied; provenance not independently verified."


class DataValidationError(ValueError):
    """Expected validation failure with representative messages and a total count."""

    def __init__(self, title: str, errors: list[str] | None = None, total: int | None = None):
        self.title = title
        self.errors = list(errors or [])[:MAX_REPORTED_ERRORS]
        self.total = total if total is not None else len(errors or [])
        message = title
        if self.errors:
            message += f" ({self.total} problem(s)). " + " | ".join(self.errors)
        super().__init__(message)


@dataclass(frozen=True)
class ValidatedDataset:
    """Cleaned tables stay inside the data layer; DM functions never see them."""

    source_mode: str
    metadata: pd.DataFrame  # symbol, name, sector (sorted by symbol)
    prices: pd.DataFrame  # date (ISO str), symbol, adjusted_close (sorted)
    metadata_sha256: str
    prices_sha256: str
    provenance: dict
    warnings: tuple[str, ...] = ()
    available_symbols: frozenset[str] = field(default_factory=frozenset)

    @property
    def fingerprint(self) -> str:
        return sha256_text(f"{self.source_mode}|{self.metadata_sha256}|{self.prices_sha256}")

    @property
    def universe(self) -> Symbols:
        return tuple(self.metadata["symbol"])

    @property
    def names(self) -> dict[str, str]:
        return dict(zip(self.metadata["symbol"], self.metadata["name"], strict=True))

    @property
    def sectors(self) -> dict[str, str]:
        return dict(zip(self.metadata["symbol"], self.metadata["sector"], strict=True))

    def date_bounds(self) -> tuple[str, str] | None:
        if self.prices.empty:
            return None
        return self.prices["date"].min(), self.prices["date"].max()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def normalize_symbol(raw: str) -> str:
    return raw.strip().upper()


def _read_csv(content: bytes, label: str, settings: Settings) -> pd.DataFrame:
    if len(content) > settings.max_csv_bytes:
        raise DataValidationError(
            f"{label} is {len(content):,} bytes; the limit is {settings.max_csv_bytes:,} bytes."
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DataValidationError(f"{label} is not valid UTF-8 text (byte offset {error.start}).") from None
    if not text.strip():
        raise DataValidationError(f"{label} is empty.")
    header = text.splitlines()[0].split(",")
    stripped_header = [column.strip() for column in header]
    duplicates = sorted({c for c in stripped_header if stripped_header.count(c) > 1})
    if duplicates:
        raise DataValidationError(f"{label} has duplicate column name(s): {', '.join(duplicates)}.")
    try:
        frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, na_filter=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as error:
        raise DataValidationError(f"{label} could not be parsed as CSV: {error}") from None
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def _check_columns(frame: pd.DataFrame, required: list[str], label: str) -> list[str]:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise DataValidationError(
            f"{label} is missing required column(s): {', '.join(missing)}. "
            f"Expected columns: {', '.join(required)}."
        )
    extra = [column for column in frame.columns if column not in required]
    if extra:
        return [f"{label}: ignored unrelated column(s) {', '.join(extra)}."]
    return []


def parse_metadata(content: bytes, settings: Settings = DEFAULT_SETTINGS):
    """Return (metadata frame, warnings). Raises DataValidationError on any invalid row."""
    label = "Metadata CSV"
    frame = _read_csv(content, label, settings)
    warnings = _check_columns(frame, ["symbol", "name", "sector"], label)
    if len(frame) > settings.max_metadata_stocks:
        raise DataValidationError(
            f"{label} has {len(frame)} stocks; the limit is {settings.max_metadata_stocks}."
        )
    errors: list[str] = []
    rows: list[dict] = []
    first_line: dict[str, int] = {}
    for position, record in enumerate(frame.to_dict("records")):
        line = position + 2  # header is line 1
        symbol = normalize_symbol(record["symbol"])
        name = record["name"].strip()
        sector = record["sector"].strip()
        if not SYMBOL_PATTERN.match(symbol):
            errors.append(f"line {line}: invalid symbol {record['symbol']!r}")
            continue
        if symbol in first_line:
            errors.append(
                f"line {line}: symbol {symbol!r} duplicates line {first_line[symbol]} after normalization"
            )
            continue
        first_line[symbol] = line
        if not name:
            errors.append(f"line {line}: empty name for {symbol!r}")
            continue
        if not sector:
            sector = "Unknown"
            warnings.append(f"{symbol}: missing sector replaced with 'Unknown'.")
        rows.append({"symbol": symbol, "name": name, "sector": sector})
    if errors:
        raise DataValidationError(f"{label} rejected", errors, len(errors))
    if not rows:
        raise DataValidationError(f"{label} contains no stocks.")
    metadata = pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True)
    return metadata, warnings


def _parse_iso_date(raw: str) -> str | None:
    value = raw.strip()
    if not DATE_PATTERN.match(value):
        return None
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return None
    return value


def parse_prices(content: bytes, known_symbols: set[str], settings: Settings = DEFAULT_SETTINGS):
    """Return (prices frame, warnings). Rejects the whole file on any invalid row."""
    label = "Price CSV"
    frame = _read_csv(content, label, settings)
    warnings = _check_columns(frame, ["date", "symbol", "adjusted_close"], label)
    if len(frame) > settings.max_price_rows:
        raise DataValidationError(
            f"{label} has {len(frame):,} rows; the limit is {settings.max_price_rows:,}."
        )
    errors: list[str] = []
    dates: list[str] = []
    symbols: list[str] = []
    values: list[float] = []
    seen: dict[tuple[str, str], int] = {}
    for position, (raw_date, raw_symbol, raw_price) in enumerate(
        zip(frame["date"], frame["symbol"], frame["adjusted_close"], strict=True)
    ):
        line = position + 2
        date = _parse_iso_date(raw_date)
        if date is None:
            errors.append(f"line {line}: date {raw_date!r} is not strict ISO YYYY-MM-DD")
            continue
        symbol = normalize_symbol(raw_symbol)
        if symbol not in known_symbols:
            errors.append(f"line {line}: symbol {raw_symbol!r} is not in the metadata")
            continue
        try:
            price = float(raw_price.strip())
        except ValueError:
            errors.append(f"line {line}: adjusted_close {raw_price!r} is not a number")
            continue
        if not math.isfinite(price) or price <= 0:
            errors.append(f"line {line}: adjusted_close {raw_price!r} must be finite and strictly positive")
            continue
        key = (date, symbol)
        if key in seen:
            errors.append(f"line {line}: duplicate record for {symbol} on {date} (see line {seen[key]})")
            continue
        seen[key] = line
        dates.append(date)
        symbols.append(symbol)
        values.append(price)
    if errors:
        raise DataValidationError(f"{label} rejected", errors, len(errors))
    prices = pd.DataFrame({"date": dates, "symbol": symbols, "adjusted_close": values})
    prices = prices.sort_values(["date", "symbol"]).reset_index(drop=True)
    return prices, warnings


def load_dataset(
    metadata_bytes: bytes,
    prices_bytes: bytes,
    settings: Settings = DEFAULT_SETTINGS,
    source_mode: str = "upload",
    provenance: dict | None = None,
) -> ValidatedDataset:
    """Validate a metadata/price CSV pair as one dataset."""
    metadata, warnings = parse_metadata(metadata_bytes, settings)
    prices, price_warnings = parse_prices(prices_bytes, set(metadata["symbol"]), settings)
    warnings = warnings + price_warnings
    available = frozenset(prices["symbol"].unique())
    unavailable = sorted(set(metadata["symbol"]) - available)
    if unavailable:
        warnings.append("No price records (unavailable for analysis): " + ", ".join(unavailable))
    metadata_hash = sha256_bytes(metadata_bytes)
    prices_hash = sha256_bytes(prices_bytes)
    record = dict(provenance) if provenance else {"description": UPLOAD_PROVENANCE}
    record["metadata_sha256"] = metadata_hash
    record["prices_sha256"] = prices_hash
    return ValidatedDataset(
        source_mode=source_mode,
        metadata=metadata,
        prices=prices,
        metadata_sha256=metadata_hash,
        prices_sha256=prices_hash,
        provenance=record,
        warnings=tuple(warnings),
        available_symbols=available,
    )


HISTORICAL_DATASET_ID = "historical-yahoo-snapshot"
HISTORICAL_DIR = DATA_DIR / "historical"


def _load_recorded_dataset(
    dataset_id: str, metadata_path: Path, prices_path: Path, source_mode: str, settings: Settings
) -> ValidatedDataset:
    """Load a dataset listed in sources.json and verify its recorded SHA-256 hashes."""
    sources = json.loads((DATA_DIR / "sources.json").read_text(encoding="utf-8"))
    entry = next((item for item in sources["datasets"] if item["dataset_id"] == dataset_id), None)
    if entry is None or "prices_sha256" not in entry:
        raise DataValidationError(f"No provenance entry for {dataset_id!r} in data/sources.json.")
    if not metadata_path.exists() or not prices_path.exists():
        raise DataValidationError(f"Files for {dataset_id!r} are missing from {metadata_path.parent}.")
    metadata_bytes = metadata_path.read_bytes()
    prices_bytes = prices_path.read_bytes()
    dataset = load_dataset(metadata_bytes, prices_bytes, settings, source_mode, entry)
    if dataset.metadata_sha256 != entry["metadata_sha256"]:
        raise DataValidationError(f"{dataset_id}: metadata does not match its recorded SHA-256.")
    if dataset.prices_sha256 != entry["prices_sha256"]:
        raise DataValidationError(f"{dataset_id}: prices do not match their recorded SHA-256.")
    return dataset


def load_bundled_dataset(settings: Settings = DEFAULT_SETTINGS) -> ValidatedDataset:
    """Load the committed synthetic 12-stock dataset and verify its recorded hashes."""
    return _load_recorded_dataset(
        "synthetic-demo-v1",
        DATA_DIR / "demo_metadata.csv",
        DATA_DIR / "demo_prices.csv",
        "synthetic_prices",
        settings,
    )


def historical_dataset_available() -> bool:
    return (HISTORICAL_DIR / "metadata.csv").exists() and (HISTORICAL_DIR / "prices.csv").exists()


def load_historical_dataset(settings: Settings = DEFAULT_SETTINGS) -> ValidatedDataset:
    """Load the Yahoo Finance snapshot written by scripts/fetch_yahoo_data.py."""
    return _load_recorded_dataset(
        HISTORICAL_DATASET_ID,
        HISTORICAL_DIR / "metadata.csv",
        HISTORICAL_DIR / "prices.csv",
        "historical",
        settings,
    )


def canonicalize_correlation(
    symbols: Symbols, raw: np.ndarray, tolerance: float
) -> tuple[FloatMatrix, list[str]]:
    """Validate a numeric correlation matrix and repair only tiny deviations.

    Non-finite values or deviations above ``tolerance`` are rejected with the
    offending cell. Accepted output is symmetrized, clipped to [-1, 1] and
    given an exact unit diagonal. Returns (matrix, normalization notes).
    """
    size = len(symbols)
    values = np.asarray(raw, dtype=float)
    if values.shape != (size, size):
        raise DataValidationError(f"Correlation matrix shape {values.shape} does not match {size} symbols.")
    for i in range(size):
        for j in range(size):
            if not math.isfinite(values[i, j]):
                raise DataValidationError(
                    f"Correlation ({symbols[i]}, {symbols[j]}) is undefined ({values[i, j]})."
                )
    notes: list[str] = []
    largest_asymmetry = 0.0
    largest_overshoot = 0.0
    largest_diagonal = 0.0
    for i in range(size):
        diagonal_error = abs(values[i, i] - 1.0)
        if diagonal_error > tolerance:
            raise DataValidationError(
                f"Correlation diagonal ({symbols[i]}, {symbols[i]}) = {values[i, i]!r}; expected 1."
            )
        largest_diagonal = max(largest_diagonal, diagonal_error)
        for j in range(size):
            asymmetry = abs(values[i, j] - values[j, i])
            if asymmetry > tolerance:
                raise DataValidationError(
                    f"Correlation matrix is asymmetric at ({symbols[i]}, {symbols[j]}): "
                    f"{values[i, j]!r} vs {values[j, i]!r}."
                )
            largest_asymmetry = max(largest_asymmetry, asymmetry)
            overshoot = max(0.0, abs(values[i, j]) - 1.0)
            if overshoot > tolerance:
                raise DataValidationError(
                    f"Correlation ({symbols[i]}, {symbols[j]}) = {values[i, j]!r} lies outside [-1, 1]."
                )
            largest_overshoot = max(largest_overshoot, overshoot)

    matrix: FloatMatrix = [[0.0] * size for _ in range(size)]
    for i in range(size):
        matrix[i][i] = 1.0
        for j in range(i + 1, size):
            average = (float(values[i, j]) + float(values[j, i])) / 2.0
            clipped = min(1.0, max(-1.0, average))
            matrix[i][j] = clipped
            matrix[j][i] = clipped
    if largest_asymmetry > 0:
        notes.append(f"Averaged symmetric entries (largest asymmetry {largest_asymmetry:.3e}).")
    if largest_overshoot > 0:
        notes.append(f"Clipped entries to [-1, 1] (largest overshoot {largest_overshoot:.3e}).")
    if largest_diagonal > 0:
        notes.append(f"Set diagonal to exactly 1 (largest deviation {largest_diagonal:.3e}).")
    return matrix, notes


def prepare_market_data(
    dataset: ValidatedDataset,
    symbols,
    start_date: str,
    end_date: str,
    settings: Settings = DEFAULT_SETTINGS,
) -> PreparedData:
    """Steps 2–10 of Section 6.1: selection, grid, returns, alignment, correlation."""
    # Step 2: validate selection and window.
    selection = canonical_selection(symbols, dataset.universe)
    if not selection:
        raise DataValidationError("Select at least one stock to run the analysis.")
    if len(selection) > settings.max_selected_stocks:
        raise DataValidationError(
            f"{len(selection)} stocks selected; the limit is {settings.max_selected_stocks}."
        )
    start = _parse_iso_date(str(start_date))
    end = _parse_iso_date(str(end_date))
    if start is None or end is None:
        raise DataValidationError("Start and end dates must be ISO YYYY-MM-DD dates.")
    if start > end:
        raise DataValidationError(f"Start date {start} is after end date {end}.")

    # Step 3: filter to selected stocks and the inclusive window (no lookback row).
    prices = dataset.prices
    in_window = prices[prices["symbol"].isin(selection) & (prices["date"] >= start) & (prices["date"] <= end)]
    priced = set(in_window["symbol"].unique())
    without_prices = [s for s in selection if s not in priced]
    if without_prices:
        raise DataValidationError(
            "No prices in the selected window for: "
            + ", ".join(without_prices)
            + ". Change the date range or the selection."
        )

    # Step 4: union of observed dates; pivot onto the grid in canonical column order.
    wide = in_window.pivot(index="date", columns="symbol", values="adjusted_close")
    wide = wide.sort_index().reindex(columns=list(selection))
    if len(wide) > settings.max_price_dates:
        raise DataValidationError(
            f"The window contains {len(wide)} price dates; the limit is {settings.max_price_dates}."
        )
    missing_price_counts = {s: int(wide[s].isna().sum()) for s in selection}

    # Step 5: explicit return formula; no fill, interpolation or row dropping first.
    returns = wide / wide.shift(1) - 1

    # Step 6: drop the first grid row; keep only rows finite for every stock.
    candidates = returns.iloc[1:]
    finite = np.isfinite(candidates.to_numpy(dtype=float)).all(axis=1)
    retained = candidates[finite]
    removed_dates = tuple(str(d) for d in candidates.index[~finite])

    # Step 7: minimum sample size.
    sample_count = len(retained)
    if sample_count < settings.min_return_observations:
        raise DataValidationError(
            f"Only {sample_count} aligned return observation(s) remain; at least "
            f"{settings.min_return_observations} are required. Widen the date range or "
            "change the selection."
        )

    # Step 8: reject numerically degenerate (constant-return) series.
    deviations = retained.std(ddof=1)
    degenerate = [s for s in selection if not deviations[s] > settings.min_return_std]
    if degenerate:
        raise DataValidationError(
            "Constant or near-constant returns make correlation undefined for: "
            + ", ".join(degenerate)
            + ". Change the selection or the date range."
        )

    # Step 9: Pearson correlation on the complete common table, then canonicalize.
    raw = retained.corr(method="pearson").reindex(index=list(selection), columns=list(selection))
    correlation, notes = canonicalize_correlation(
        selection, raw.to_numpy(dtype=float), settings.correlation_tolerance
    )

    # Step 10: PreparedData with a content-derived identifier.
    return_dates = tuple(str(d) for d in retained.index)
    warnings = list(dataset.warnings)
    if removed_dates:
        warnings.append(
            f"{len(removed_dates)} return date(s) discarded because a selected stock lacked a "
            "price at one of the two endpoints."
        )
    summary = ValidationSummary(
        metadata_rows=len(dataset.metadata),
        price_rows=len(in_window),
        date_count=len(wide),
        candidate_return_rows=len(candidates),
        retained_return_rows=sample_count,
        removed_return_dates=removed_dates,
        removed_return_count=len(removed_dates),
        missing_price_counts=missing_price_counts,
        warnings=tuple(warnings),
        normalization_notes=tuple(notes),
    )
    data_id = sha256_text(
        json.dumps(
            {
                "source": dataset.fingerprint,
                "symbols": list(selection),
                "start": start,
                "end": end,
                "policy": PREPROCESSING_POLICY_VERSION,
                "min_obs": settings.min_return_observations,
            },
            sort_keys=True,
        )
    )
    names = dataset.names
    sectors = dataset.sectors
    return PreparedData(
        data_id=data_id,
        source_mode=dataset.source_mode,
        symbols=selection,
        correlation=correlation,
        sample_count=sample_count,
        return_dates=return_dates,
        requested_start=start,
        requested_end=end,
        validation_summary=summary,
        provenance=dict(dataset.provenance),
        names={s: names[s] for s in selection},
        sectors={s: sectors[s] for s in selection},
    )


def load_teaching_fixture() -> dict:
    """Read the five-stock synthetic correlation fixture and its metadata."""
    content = (DATA_DIR / "teaching_correlation.json").read_bytes()
    fixture = json.loads(content)
    metadata, _ = parse_metadata((DATA_DIR / "teaching_metadata.csv").read_bytes())
    fixture["sha256"] = sha256_bytes(content)
    fixture["names"] = dict(zip(metadata["symbol"], metadata["name"], strict=True))
    fixture["sectors"] = dict(zip(metadata["symbol"], metadata["sector"], strict=True))
    return fixture


def prepare_teaching_data(symbols) -> PreparedData:
    """Subset the teaching correlation matrix to the selected symbols.

    Bypasses price preparation explicitly: no sample count and no dates.
    """
    fixture = load_teaching_fixture()
    all_symbols = tuple(fixture["symbols"])
    selection = canonical_selection(symbols, all_symbols)
    if not selection:
        raise DataValidationError("Select at least one stock to run the analysis.")
    full = np.asarray(fixture["correlation"], dtype=float)
    eigenvalues = np.linalg.eigvalsh(full)
    if eigenvalues.min() < -1e-12:
        raise DataValidationError("Teaching matrix is not positive semidefinite.")
    index = [all_symbols.index(s) for s in selection]
    correlation, notes = canonicalize_correlation(
        selection, full[np.ix_(index, index)], DEFAULT_SETTINGS.correlation_tolerance
    )
    data_id = sha256_text(
        json.dumps({"source": fixture["sha256"], "symbols": list(selection)}, sort_keys=True)
    )
    summary = ValidationSummary(
        metadata_rows=len(all_symbols),
        warnings=(),
        normalization_notes=tuple(notes)
        + ("Teaching matrix: price preparation bypassed; no empirical observations.",),
    )
    return PreparedData(
        data_id=data_id,
        source_mode="teaching_matrix",
        symbols=selection,
        correlation=correlation,
        sample_count=None,
        return_dates=(),
        requested_start=None,
        requested_end=None,
        validation_summary=summary,
        provenance={
            "dataset_id": "teaching-correlation-v1",
            "kind": "synthetic_correlation",
            "description": "Synthetic five-stock teaching correlations; not market values.",
            "fixture_sha256": fixture["sha256"],
        },
        names={s: fixture["names"][s] for s in selection},
        sectors={s: fixture["sectors"][s] for s in selection},
    )
