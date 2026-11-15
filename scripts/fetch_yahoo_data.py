"""Download a historical adjusted-close snapshot from Yahoo Finance.

This is a one-off build step that needs internet access; the app itself stays
offline and only reads the committed CSVs. Run from the repository root:

    python scripts/fetch_yahoo_data.py
    python scripts/fetch_yahoo_data.py --start 2023-01-01 --end 2024-12-31
    python scripts/fetch_yahoo_data.py --tickers AAPL:Technology MSFT:Technology JPM:Banking

Writes data/historical/metadata.csv and data/historical/prices.csv in the
app's CSV contract and records provenance and SHA-256 hashes in
data/sources.json. Missing observations stay absent rows; nothing is filled.
Requires: pip install -r requirements-fetch.txt
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import warnings
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_DIR = DATA_DIR / "historical"
DATASET_ID = "historical-yahoo-snapshot"

# Large US companies in four sectors, chosen so sector clusters are visible.
# Selection basis: hand-picked well-known constituents; not an index snapshot.
DEFAULT_TICKERS = {
    "JPM": ("JPMorgan Chase & Co.", "Banking"),
    "BAC": ("Bank of America Corporation", "Banking"),
    "WFC": ("Wells Fargo & Company", "Banking"),
    "C": ("Citigroup Inc.", "Banking"),
    "AAPL": ("Apple Inc.", "Technology"),
    "MSFT": ("Microsoft Corporation", "Technology"),
    "NVDA": ("NVIDIA Corporation", "Technology"),
    "GOOGL": ("Alphabet Inc. (Class A)", "Technology"),
    "XOM": ("Exxon Mobil Corporation", "Energy"),
    "CVX": ("Chevron Corporation", "Energy"),
    "COP": ("ConocoPhillips", "Energy"),
    "SLB": ("SLB N.V.", "Energy"),
    "KO": ("The Coca-Cola Company", "Consumer Staples"),
    "PEP": ("PepsiCo, Inc.", "Consumer Staples"),
    "PG": ("The Procter & Gamble Company", "Consumer Staples"),
    "WMT": ("Walmart Inc.", "Consumer Staples"),
}


def parse_tickers(raw: list[str] | None) -> dict[str, tuple[str, str]]:
    """Accept TICKER or TICKER:Sector; look up names/sectors only when not given."""
    if not raw:
        return dict(DEFAULT_TICKERS)
    import yfinance as yf

    result: dict[str, tuple[str, str]] = {}
    for item in raw:
        ticker, _, sector = item.partition(":")
        ticker = ticker.strip().upper()
        if ticker in DEFAULT_TICKERS and not sector:
            result[ticker] = DEFAULT_TICKERS[ticker]
            continue
        name = ticker
        try:
            info = yf.Ticker(ticker).info
            name = info.get("longName") or info.get("shortName") or ticker
            sector = sector or info.get("sector") or ""
        except Exception as error:  # metadata lookup is best effort
            print(f"  could not look up {ticker}: {error}")
        result[ticker] = (name, sector.strip())
    return result


def csv_field(text: str) -> str:
    if any(ch in text for ch in ',"\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", default="2024-01-01", help="first date (inclusive)")
    parser.add_argument("--end", default="2025-12-31", help="last date (inclusive)")
    parser.add_argument("--tickers", nargs="*", help="TICKER or TICKER:Sector entries")
    args = parser.parse_args()

    import yfinance as yf

    tickers = parse_tickers(args.tickers)
    symbols = sorted(tickers)
    end_exclusive = (dt.date.fromisoformat(args.end) + dt.timedelta(days=1)).isoformat()
    print(f"Downloading {len(symbols)} tickers {args.start} to {args.end} from Yahoo Finance...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        frame = yf.download(
            symbols,
            start=args.start,
            end=end_exclusive,
            auto_adjust=True,  # Close is split- and dividend-adjusted
            actions=False,
            progress=False,
            group_by="column",
            threads=True,
        )
    retrieved_at = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    if frame is None or frame.empty:
        raise SystemExit("No data returned. Check the tickers, dates and internet connection.")
    closes = frame["Close"]

    price_lines = ["date,symbol,adjusted_close"]
    counts: dict[str, int] = {}
    for date, row in closes.sort_index().iterrows():
        day = date.date().isoformat()
        for symbol in symbols:
            value = row.get(symbol)
            if value is None or not math.isfinite(float(value)) or float(value) <= 0:
                continue  # missing observation -> absent row, never filled
            price_lines.append(f"{day},{symbol},{float(value):.6f}")
            counts[symbol] = counts.get(symbol, 0) + 1

    missing = [s for s in symbols if not counts.get(s)]
    if missing:
        print("  WARNING: no prices for " + ", ".join(missing) + " (kept as unavailable)")

    metadata_lines = ["symbol,name,sector"]
    for symbol in symbols:
        name, sector = tickers[symbol]
        metadata_lines.append(f"{symbol},{csv_field(name)},{csv_field(sector)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    metadata_bytes = ("\n".join(metadata_lines) + "\n").encode("utf-8")
    prices_bytes = ("\n".join(price_lines) + "\n").encode("utf-8")
    (OUT_DIR / "metadata.csv").write_bytes(metadata_bytes)
    (OUT_DIR / "prices.csv").write_bytes(prices_bytes)

    dates = sorted({line.split(",")[0] for line in price_lines[1:]})
    entry = {
        "dataset_id": DATASET_ID,
        "kind": "historical",
        "description": "Historical daily adjusted closes downloaded from Yahoo Finance",
        "provider": "Yahoo Finance via the yfinance Python package (unofficial API)",
        "source_url": "https://finance.yahoo.com/",
        "retrieved_at": retrieved_at,
        "license_or_permission": (
            "Yahoo Finance terms of service; downloaded for personal/educational course use. "
            "Redistribution not confirmed - do not publish the CSV files."
        ),
        "price_convention": "Close with auto_adjust=True (adjusted for splits and dividends)",
        "calendar": "Trading dates as returned by the provider (US exchanges)",
        "selection_basis": (
            "Hand-picked large US companies in four sectors; not an index membership snapshot"
            if not args.tickers
            else "User-specified tickers: " + " ".join(args.tickers)
        ),
        "requested_range": [args.start, args.end],
        "date_range": [dates[0], dates[-1]] if dates else None,
        "observations_per_symbol": counts,
        "generator": "scripts/fetch_yahoo_data.py",
        "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        "prices_sha256": hashlib.sha256(prices_bytes).hexdigest(),
    }
    sources_path = DATA_DIR / "sources.json"
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    sources["datasets"] = [
        d for d in sources["datasets"] if d["dataset_id"] not in (DATASET_ID, "historical-nifty50-snapshot")
    ] + [entry]
    sources_path.write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {len(symbols)} stocks, {len(dates)} trading dates ({entry['date_range']}).")
    print(f"Provenance recorded in {sources_path.relative_to(DATA_DIR.parent)}.")


if __name__ == "__main__":
    main()
