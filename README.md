# StockRelate — Sets, Relations and Reachability

An offline Streamlit teaching application for a Discrete Mathematics course. It turns
stock-return correlations into a binary relation, checks reflexivity, symmetry and
transitivity with counterexamples, and builds equivalence classes from graph reachability.

> A correlation-threshold relation need not be transitive. Reachability (including paths of
> length zero) in its undirected graph is an equivalence relation, and its classes are exactly
> the connected components.

## Modules

| # | Module | Discrete mathematics |
|---|---|---|
| 1 | Stock Set Builder | sets, membership, cardinality, subsets, ∪, ∩, \\ |
| 2 | Correlation Binary Relation Builder | Cartesian product, binary relation Rτ, Boolean matrix, graph |
| 3 | Relation Property Analyzer | reflexive / symmetric / transitive checks with witnesses |
| 4 | Reachability Equivalence Classes | BFS components and paths, Warshall closure, partitions |
| 5 | Interactive Threshold Explorer | relation inclusion, partition refinement as τ changes |

## Setup (Windows PowerShell)

Tested with **Python 3.12.7** on Windows 11.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m streamlit run app.py
```

On macOS/Linux use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`.

Pinned, tested versions: streamlit 1.66.1, pandas 3.1.0, numpy 2.5.4, plotly 7.1.0,
pytest 9.2.0, ruff 0.17.3 (plus yfinance 1.7.0 only for the optional fetch script). No network access, API keys or credentials are needed at runtime.

## Data sources

The app opens on **real historical prices** (16 US stocks from Yahoo Finance, all applied,
threshold 0.65) when `data/historical/` exists, and on the synthetic A–E teaching matrix
otherwise. Bundled sources apply all their stocks as soon as you pick them; uploads wait for
**Apply selection**. Tables, selectors and paths show stocks as `TICKER · Company name`;
charts keep tickers only so 16+ labels stay readable. The A–E matrix keeps its made-up
labels on purpose: its correlations are invented, so real company names would misrepresent them.

| Source | Contents | Provenance |
|---|---|---|
| Teaching matrix | `data/teaching_correlation.json` + `data/teaching_metadata.csv` | Synthetic correlations written for the course; positive definite |
| Bundled synthetic prices | `data/demo_metadata.csv`, `data/demo_prices.csv` — DEMO01–DEMO12, 3 sectors, 251 weekday dates | Generated once by `scripts/generate_demo_data.py` (seed 2026); hashes in `data/sources.json`; **not market data** |
| Historical prices | `data/historical/` — 16 US stocks (JPM, BAC, WFC, C, AAPL, MSFT, NVDA, GOOGL, XOM, CVX, COP, SLB, KO, PEP, PG, WMT), 2024-01-02 to 2025-12-31 | Real adjusted closes from Yahoo Finance via `scripts/fetch_yahoo_data.py`; retrieval date and hashes in `data/sources.json` |
| Upload CSV pair | Your own metadata + price files | Labeled "User supplied; provenance not independently verified" |

### Real historical data from Yahoo Finance

Fetching needs internet; the app then reads the saved CSVs offline.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-fetch.txt
.\.venv\Scripts\python.exe scripts\fetch_yahoo_data.py
```

Options: `--start`/`--end` (inclusive) and `--tickers` with entries like `AAPL:Technology`
or `TCS.NS:Technology` (any Yahoo ticker, including Indian `.NS` symbols). Then choose
**Historical prices** in the sidebar and press **Apply selection**. Yahoo data is for
personal/educational use; redistribution is not confirmed, so do not publish those CSVs.
See `data/historical/README.md`.

What the default snapshot shows: at τ = 0.50 the reachability classes match the sectors
(banks, energy, consumer staples; tech splits), and at τ = 0.70 real data breaks
transitivity with the witness (SLB, COP, XOM).

### Metadata CSV

```csv
symbol,name,sector
A,Demo Alpha,Banking
B,Demo Beta,Banking
```

Symbols are trimmed and uppercased and must match `[A-Z0-9][A-Z0-9._&-]{0,31}`; duplicates
after normalization are rejected. A blank sector becomes `Unknown` with a warning.

### Price CSV (long format)

```csv
date,symbol,adjusted_close
2025-01-02,A,100.0
2025-01-02,B,200.0
```

Dates are strict ISO `YYYY-MM-DD`. Prices must be finite and strictly positive. Duplicate
`(date, symbol)` rows, unknown symbols and malformed values reject the whole file with
representative line numbers. A missing observation is an absent row.

## How the analysis works

1. Returns are `P[t]/P[t-1] − 1` on the union of observed dates. Any return row that touches a
   missing price is discarded **for every stock** (no forward-fill, no gap bridging), so all
   correlations use the same dates. At least 30 aligned returns are required.
2. Pearson correlation is computed with pandas; series with return standard deviation
   ≤ 1e-12 are rejected by name.
3. `Rτ = {(a,b) : C[a][b] ≥ τ}` — signed, inclusive and unrounded. Negative correlations never
   create edges. The relation matrix keeps its diagonal; the graph hides self-loops.
4. Properties, BFS, shortest paths and Warshall's closure are **implemented by hand** in
   `stockrelate/relations.py` and `stockrelate/graphs.py`. No graph library is used.
5. Every analysis is cross-checked: BFS components must agree with Warshall reachability cell by
   cell, the classes must partition S, and `Σ|class|² = |Q|`.

Downloads (`relation_matrix.csv`, `reachability_matrix.csv`, `ordered_pairs.csv`, `edges.csv`,
`components.csv`, `analysis.json`) always come from the analysis currently shown.

## Project layout

```text
app.py                  Streamlit entry point, session state and invalidation
stockrelate/
  config.py             limits and defaults
  models.py             dataclass contracts (PreparedData, RelationResult, ...)
  sets.py               manual set operations, canonical selection, sector subsets
  market_data.py        CSV validation, aligned returns, correlation, teaching data
  relations.py          threshold relation and property checkers
  graphs.py             neighbor lists, BFS components/paths, Warshall closure
  analysis.py           orchestration + consistency checks
  exports.py            CSV/JSON downloads
  charts.py             Plotly figures (fixed circular layout)
  ui.py                 five tab render functions
data/                   bundled datasets and sources.json
scripts/                demo-data generator, performance measurement
tests/                  pytest suite and fixtures
docs/                   mathematics.md, demo.md, validation.md
```

## Limitations

- Correlation describes association in the chosen sample; it is not causation, a forecast, or
  financial advice.
- Results depend on selection, dates and threshold. Changing the selection can change the
  retained dates and hence the correlations of stocks that stay selected.
- The date grid cannot detect a date missing for every selected stock; no exchange calendar is used.
- A reachability class is not a clique: a long chain can join weakly correlated endpoints.
- The app is bounded to 30 selected stocks; the property and closure algorithms are cubic.
- Session state is temporary; exports are the reproducibility mechanism.

See `docs/validation.md` for the checks actually run and measured timings.
