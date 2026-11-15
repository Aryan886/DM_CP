# Historical dataset (Yahoo Finance snapshot)

`metadata.csv` and `prices.csv` here are real daily adjusted closes downloaded from
Yahoo Finance with `scripts/fetch_yahoo_data.py`. Provider, retrieval timestamp,
adjustment convention, selection basis and SHA-256 hashes are recorded in
`data/sources.json` (entry `historical-yahoo-snapshot`). The app verifies those hashes
on load and refuses files that do not match.

Default snapshot: 16 large US companies in four sectors (Banking, Technology, Energy,
Consumer Staples), 2024-01-02 to 2025-12-31, 502 trading dates. The stocks were
hand-picked; they are **not** an index-membership snapshot.

## Refresh or change the snapshot (needs internet)

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-fetch.txt
.\.venv\Scripts\python.exe scripts\fetch_yahoo_data.py
.\.venv\Scripts\python.exe scripts\fetch_yahoo_data.py --start 2023-01-01 --end 2024-12-31
.\.venv\Scripts\python.exe scripts\fetch_yahoo_data.py --tickers AAPL:Technology RELIANCE.NS:Energy TCS.NS:Technology
```

Any Yahoo ticker that matches the symbol rule `[A-Z0-9][A-Z0-9._&-]{0,31}` works
(for example `RELIANCE.NS`, `BRK-B`). Index tickers such as `^GSPC` do not.
Fetching overwrites these files and the provenance entry; the app itself stays offline.

## Permission

The data comes from Yahoo Finance through the unofficial `yfinance` package under
Yahoo's terms of service, for personal and educational course use. Redistribution
has not been confirmed: keep these CSVs out of any public repository or release.
