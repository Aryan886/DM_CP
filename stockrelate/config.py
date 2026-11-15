"""Limits and defaults for StockRelate. No secrets or environment lookups."""

from dataclasses import dataclass

APP_VERSION = "1.0.0"
ALGORITHM_CONTRACT_VERSION = 1
PREPROCESSING_POLICY_VERSION = 1
EXPORT_SCHEMA_VERSION = 1

DEFAULT_THRESHOLD_TICK = 65  # threshold = tick / 100
MAX_SELECTED_STOCKS = 30
MIN_RETURN_OBSERVATIONS = 30
MAX_PRICE_DATES = 2000  # after selection/date filtering
MAX_CSV_BYTES = 10 * 1024 * 1024  # per uploaded file
MAX_PRICE_ROWS = 250000
MAX_METADATA_STOCKS = 100
CORRELATION_TOLERANCE = 1e-12
MIN_RETURN_STD = 1e-12
MAX_DISPLAYED_PAIRS = 100  # full result still available for download

TEACHING_PRESET_TICKS = (20, 65, 75, 90)


@dataclass(frozen=True)
class Settings:
    """Validation settings passed to pure data functions.

    The Streamlit UI always uses the defaults. Unit tests may lower
    ``min_return_observations`` to run small fixtures.
    """

    min_return_observations: int = MIN_RETURN_OBSERVATIONS
    max_selected_stocks: int = MAX_SELECTED_STOCKS
    max_price_dates: int = MAX_PRICE_DATES
    max_csv_bytes: int = MAX_CSV_BYTES
    max_price_rows: int = MAX_PRICE_ROWS
    max_metadata_stocks: int = MAX_METADATA_STOCKS
    correlation_tolerance: float = CORRELATION_TOLERANCE
    min_return_std: float = MIN_RETURN_STD

    def __post_init__(self) -> None:
        if self.min_return_observations < 2:
            raise ValueError(
                "min_return_observations must be at least 2 so that a sample standard deviation is defined."
            )


DEFAULT_SETTINGS = Settings()
