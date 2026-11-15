"""Generate the bundled synthetic 12-stock price dataset and data/sources.json.

Run once and commit the output; the application never regenerates it:

    python scripts/generate_demo_data.py

Daily return = 0.005 * common + 0.01 * sector_factor + 0.005 * stock_noise,
standard-normal factors, clipped to [-0.2, 0.2], compounded from 100.
Dates are a plain weekday sequence, not an exchange trading calendar.
"""

import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np

SEED = 2026
OBSERVATIONS = 251
START = dt.date(2025, 1, 2)
SECTORS = ("Banking", "Energy", "Technology")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def weekday_dates(start: dt.date, count: int) -> list[str]:
    dates: list[str] = []
    current = start
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current.isoformat())
        current += dt.timedelta(days=1)
    return dates


def main() -> None:
    rng = np.random.default_rng(SEED)
    symbols = [f"DEMO{i:02d}" for i in range(1, 13)]
    sector_of = {symbol: SECTORS[(index) // 4] for index, symbol in enumerate(symbols)}
    dates = weekday_dates(START, OBSERVATIONS)
    returns_count = OBSERVATIONS - 1

    common = rng.standard_normal(returns_count)
    sector_factors = {sector: rng.standard_normal(returns_count) for sector in SECTORS}
    noise = {symbol: rng.standard_normal(returns_count) for symbol in symbols}

    metadata_lines = ["symbol,name,sector"]
    for index, symbol in enumerate(symbols, start=1):
        metadata_lines.append(f"{symbol},Synthetic Demo Stock {index:02d},{sector_of[symbol]}")

    prices: dict[str, list[float]] = {}
    for symbol in symbols:
        daily = 0.005 * common + 0.01 * sector_factors[sector_of[symbol]] + 0.005 * noise[symbol]
        daily = np.clip(daily, -0.2, 0.2)
        series = [100.0]
        for value in daily:
            series.append(series[-1] * (1.0 + float(value)))
        prices[symbol] = [round(price, 6) for price in series]

    price_lines = ["date,symbol,adjusted_close"]
    for t, date in enumerate(dates):
        for symbol in symbols:
            price_lines.append(f"{date},{symbol},{prices[symbol][t]:.6f}")

    metadata_bytes = ("\n".join(metadata_lines) + "\n").encode("utf-8")
    prices_bytes = ("\n".join(price_lines) + "\n").encode("utf-8")
    (DATA_DIR / "demo_metadata.csv").write_bytes(metadata_bytes)
    (DATA_DIR / "demo_prices.csv").write_bytes(prices_bytes)

    teaching_bytes = (DATA_DIR / "teaching_correlation.json").read_bytes()
    teaching_metadata_bytes = (DATA_DIR / "teaching_metadata.csv").read_bytes()
    sources = {
        "schema_version": 1,
        "datasets": [
            {
                "dataset_id": "synthetic-demo-v1",
                "kind": "synthetic",
                "description": "Deterministic teaching prices; not market observations",
                "source_url": None,
                "retrieved_at": None,
                "license_or_permission": "Generated for this course project",
                "price_convention": "Synthetic positive prices (treated as adjusted closes)",
                "calendar": "Plain weekday sequence; not an exchange trading calendar",
                "date_range": [dates[0], dates[-1]],
                "generator": "scripts/generate_demo_data.py",
                "generator_seed": SEED,
                "generating_rule": (
                    "r = clip(0.005*common + 0.01*sector + 0.005*noise, -0.2, 0.2), "
                    "standard-normal factors, compounded from 100"
                ),
                "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
                "prices_sha256": hashlib.sha256(prices_bytes).hexdigest(),
            },
            {
                "dataset_id": "teaching-correlation-v1",
                "kind": "synthetic_correlation",
                "description": "Five-stock teaching correlation matrix; not market values",
                "source_url": None,
                "retrieved_at": None,
                "license_or_permission": "Written for this course project",
                "correlation_sha256": hashlib.sha256(teaching_bytes).hexdigest(),
                "metadata_sha256": hashlib.sha256(teaching_metadata_bytes).hexdigest(),
            },
            {
                "dataset_id": "historical-nifty50-snapshot",
                "kind": "historical",
                "status": "outstanding optional deliverable; not shipped",
                "description": (
                    "No historical market data is bundled. Add a documented snapshot under "
                    "data/historical/ with provider, retrieval date, adjustment convention, "
                    "selection basis and permission before describing any data as historical."
                ),
            },
        ],
    }
    (DATA_DIR / "sources.json").write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(symbols)} stocks x {OBSERVATIONS} dates ({dates[0]} to {dates[-1]}).")


if __name__ == "__main__":
    main()
