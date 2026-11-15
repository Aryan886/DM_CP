"""Measure preparation and threshold-only analysis times (Section 11.6).

Builds a synthetic 30-stock x 2000-date dataset in memory (not committed),
then times data preparation once and core analysis for every threshold tick.

    python scripts/measure_performance.py
"""

import datetime as dt
import platform
import statistics
import time

import numpy as np

from stockrelate.analysis import analyze
from stockrelate.market_data import load_dataset, prepare_market_data


def build_csvs(stocks: int = 30, dates: int = 2000) -> tuple[bytes, bytes]:
    rng = np.random.default_rng(7)
    symbols = [f"PERF{i:02d}" for i in range(stocks)]
    metadata = ["symbol,name,sector"] + [f"{s},Perf {s},Sector{i % 5}" for i, s in enumerate(symbols)]
    day = dt.date(2015, 1, 1)
    grid = []
    while len(grid) < dates:
        if day.weekday() < 5:
            grid.append(day.isoformat())
        day += dt.timedelta(days=1)
    common = rng.standard_normal(dates)
    sector = rng.standard_normal((5, dates))
    lines = ["date,symbol,adjusted_close"]
    prices = {}
    for i, s in enumerate(symbols):
        r = np.clip(0.004 * common + 0.008 * sector[i % 5] + 0.006 * rng.standard_normal(dates), -0.2, 0.2)
        prices[s] = 100 * np.cumprod(1 + r)
    for t, d in enumerate(grid):
        for s in symbols:
            lines.append(f"{d},{s},{prices[s][t]:.6f}")
    return ("\n".join(metadata) + "\n").encode(), ("\n".join(lines) + "\n").encode()


def main() -> None:
    metadata, prices = build_csvs()
    start = time.perf_counter()
    dataset = load_dataset(metadata, prices)
    load_ms = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    prepared = prepare_market_data(dataset, dataset.universe, "2000-01-01", "2030-01-01")
    prepare_ms = (time.perf_counter() - start) * 1000

    timings = []
    for tick in range(101):
        start = time.perf_counter()
        analyze(prepared, tick / 100)
        timings.append((time.perf_counter() - start) * 1000)

    print(f"Python {platform.python_version()} on {platform.platform()} ({platform.processor()})")
    print(
        f"Dataset: {len(dataset.universe)} stocks, {prepared.validation_summary.date_count} dates, "
        f"{prepared.sample_count} aligned returns"
    )
    print(f"CSV load + validation: {load_ms:.0f} ms")
    print(f"Price preparation (returns + correlation): {prepare_ms:.0f} ms")
    print(
        f"Core analysis per threshold (101 ticks): median {statistics.median(timings):.1f} ms, "
        f"max {max(timings):.1f} ms"
    )


if __name__ == "__main__":
    main()
