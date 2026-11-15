# Validation log

Recorded 30 October 2026. Only checks listed here were actually run.

## Environment

- Windows 11 Home 10.0.26200, AMD64 Family 23 Model 160 (AMD), Python 3.12.7
- streamlit 1.66.1, pandas 3.1.0, numpy 2.5.4, plotly 7.1.0, pytest 9.2.0, ruff 0.17.3

## Commands and results

| Command | Result |
|---|---|
| `python -m pytest -q` | **144 passed** in ~10 s |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | 28 files already formatted |

### Test coverage by area

| Area | Where |
|---|---|
| Section 10.1 set fixture, oracle comparison, no mutation, sector partition | `tests/test_sets.py` |
| Metadata/price validation, alignment (missing middle price, unsorted, coverage, window), sample-size boundaries, constant and geometric-growth returns, resource limits, canonicalization, bundled hashes, content-hash invalidation | `tests/test_market_data.py` |
| Inclusive/unrounded threshold, invalid thresholds, invalid correlations, `n + 2m`, Section 10.4 fixtures, repeated-index witness, all 512 relations on 3 vertices against a set oracle with witness verification | `tests/test_relations.py` |
| Warshall (identity, directed chain, idempotence, no mutation), BFS components/paths, all 64 graphs on 4 vertices (BFS = Warshall, partition, transitive ⇔ components complete) | `tests/test_graphs.py` |
| Section 10.2 table at all 7 thresholds, metrics, preset rows, threshold sweep 0–100 (teaching and bundled), exports round-trip with strict JSON, committed `expected_analysis.json` | `tests/test_analysis.py` |
| AppTest: first launch on historical data (prepared once), teaching matrix applies A–E, slider without re-preparation (prepare counter), presets, selection shrink resets path endpoints, empty selection, "use result as S", switching bundled source replaces results, switching to upload clears outputs, two independent sessions | `tests/test_app.py` |

## Performance

`python scripts/measure_performance.py` (30 stocks × 2,000 in-memory synthetic dates):

| Step | Measured |
|---|---|
| CSV load + validation (60,000 price rows) | ~500 ms |
| Price preparation (returns + correlation) | 40 ms |
| Core analysis per threshold, 101 ticks | median 4.6 ms, max 8.6 ms (target < 100 ms) |

Warmed full-page threshold reruns in Streamlit AppTest with the 12-stock bundled dataset:
186–377 ms (first rerun slowest). A 30-stock browser rerun was not timed because AppTest
cannot drive file uploads; record it manually during the demo rehearsal.

Fix found while measuring: preparation initially took ~2.5 s because a set was rebuilt
inside a list comprehension; corrected in `market_data.prepare_market_data`.

## Browser checks performed

In the built-in browser at 800×600 and 1400×900:

- Teaching matrix at τ = 0.65: metrics 5 / 3 / 2, transitivity witness (A,B,C) with the three
  correlation comparisons, witness cells and edges highlighted, missing A–C pair dashed.
- Reachability tab: classes {A,B,C}, {D,E}; partition checks pass; Q \ R cells outlined.
- Bundled synthetic prices: 12 nodes, 18 edges, 3 classes (one per synthetic sector).
- Default launch: historical source applied automatically, 16 nodes, 12 edges, 10 classes at
  τ = 0.65; reachability class table lists members as `TICKER · Company`.
- No browser console errors.

## Historical snapshot (Yahoo Finance)

`python scripts/fetch_yahoo_data.py` on 30 October 2026 downloaded 16 tickers,
502 trading dates (2024-01-02 to 2025-12-31), 8,032 price rows with no missing
observations. Over the full window: 501 aligned returns. Selected results:

| τ | edges | R transitive | classes |
|---|---:|---|---|
| 0.40 | 24 | no, witness (AAPL, MSFT, NVDA) | 4 |
| 0.50 | 16 | yes | 7 — {BAC,C,JPM,WFC}, {COP,CVX,SLB,XOM}, {KO,PEP,PG}, {MSFT,NVDA}, 3 singletons |
| 0.70 | 11 | no, witness (SLB, COP, XOM) | 10 |
| 0.80 | 3 | no, witness (C, BAC, WFC) | 13 |

Tests: hash and provenance verification, symmetric/PSD correlation, and BFS/Warshall
agreement at every 5th threshold tick; an AppTest run through the Historical source.
In the browser the source applied with 16 nodes and 12 edges at τ = 0.65, no console errors.

## Not yet checked (manual follow-up)

- A real malformed CSV upload and a valid upload through the browser file picker
  (validation logic itself is covered by unit tests).
- Opening downloaded files in an external program (round-trip is covered by unit tests).
- Narrow/mobile layout readability.
- Running with the network adapter disabled. The app makes no network calls by design
  (`gatherUsageStats = false`, no remote assets), but this was not tested with networking off.

