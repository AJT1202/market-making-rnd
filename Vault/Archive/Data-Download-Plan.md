---
title: "Telonex Data Download Plan"
created: 2026-04-01
updated: 2026-04-01
tags:
  - data-pipeline
  - telonex
  - polymarket
  - download
  - backtesting
related:
  - "[[Backtesting-Architecture]]"
  - "[[Engine-Architecture-Plan]]"
  - "[[Orderbook-Backtesting-with-Telonex]]"
  - "[[Telonex-Data-Quality-Report]]"
---

# Telonex Data Download Plan

Complete plan for downloading historical orderbook and trade data from Telonex for all stock/index binary event markets on Polymarket. This data feeds the backtesting engine described in [[Engine-Architecture-Plan]].

---

## 1. Scope

### Tickers

| Ticker | Type  | Daily Close-Above | Weekly Close-Above | Monthly Close-Above | Weekly Range | Monthly Range | Up/Down |
| ------ | ----- | :---------------: | :----------------: | :-----------------: | :----------: | :-----------: | :-----: |
| NFLX   | Stock |                   | 21 events          | 6 events            | 18 events    | 6 events      | 113     |
| MSFT   | Stock | 46 events         | 21 events          | 6 events            | 20 events    | 5 events      | 114     |
| PLTR   | Stock |                   | 21 events          | 6 events            | 20 events    | 5 events      | 112     |
| GOOGL  | Stock | 47 events         | 21 events          | 6 events            | 20 events    | 6 events      | 114     |
| AAPL   | Stock | 48 events         | 21 events          | 6 events            | 20 events    | 5 events      | 113     |
| TSLA   | Stock | 46 events         | 21 events          | 6 events            | 20 events    | 6 events      | 114     |
| META   | Stock | 47 events         | 21 events          | 6 events            | 19 events    | 5 events      | 114     |
| AMZN   | Stock | 48 events         | 21 events          | 6 events            | 20 events    | 6 events      | 114     |
| NVDA   | Stock | 46 events         | 21 events          | 6 events            | 20 events    | 6 events      | 114     |
| SPX    | Index |                   | 2 events           | 5 events            |              | 7 events      | 162     |
| NDX    | Index |                   | 2 events           | 2 events            |              | 2 events      | 114     |

**Notes:**
- NFLX and PLTR have no daily close-above events (this product launched later for those tickers)
- SPX and NDX have no weekly range events and no daily close-above
- Data goes back to approximately October 11, 2025

### Event Categories

| Category | Slug Pattern (stocks) | Recurrence | Strikes/Event | Structure |
| --- | --- | --- | --- | --- |
| Daily close-above | `{ticker}-close-above-on-{date}` | daily | ~5 | "Will X close above $Y today?" |
| Weekly close-above | `{ticker}-above-on-{date}` | weekly | ~12 | "Will X finish week above $Y?" |
| Monthly close-above | `{ticker}-above-in-{month}-{year}` | monthly | ~13 | "Will X close above $Y end of month?" |
| Weekly range | `{ticker}-week-{date}` | weekly | ~11 | "Will X close at $A-$B this week?" |
| Monthly range | `what-price-will-{ticker}-hit-in-{month}` | monthly | ~14 | "Will X reach/dip to $Y?" |
| Up/down | `{ticker}-up-or-down-on-{date}` | daily | 1 | "X up or down today?" (single binary) |

**Index tickers (SPX, NDX) have inconsistent slug patterns** — e.g., `sp-500-spx-above-end-of-march`, `spx-above-on-november-14-2025`, `what-will-spx-hit-in-march-2026`. These are handled by dedicated regex patterns in the discovery script.

### Recurrence Classification

The slug alone does not indicate recurrence. Two authoritative sources:

1. **Gamma API** (`GET /events/slug/{slug}`) — returns `series[].recurrence` field ("daily", "weekly", "monthly"). This is definitive.
2. **Telonex `start_date_us` / `end_date_us`** — duration can confirm: daily ~19-104h, weekly ~74-265h, monthly ~601-838h.

The discovery script (`scripts/discover_markets.py`) uses slug pattern matching for initial classification and Gamma API for authoritative confirmation.

---

## 2. Data Channels

Per market (one strike within one event), Telonex provides:

| Channel | Description | Download Unit | Use Case |
| --- | --- | --- | --- |
| `book_snapshot_full` | Full orderbook snapshots at every tick | 1 channel x 1 date x 1 asset | Fill simulation, spread analysis, depth profiling |
| `trades` | Individual trade events | 1 channel x 1 date x 1 asset | Fill triggering (only trades trigger fills), volume analysis |

Each Polymarket market has two tokens (YES and NO), so per market per date we need **4 downloads**:

```
book_snapshot_full x YES
book_snapshot_full x NO
trades x YES
trades x NO
```

---

## 3. Download Volume

| Category | Events | Markets | Market-Days | Downloads (x4) |
| --- | ---: | ---: | ---: | ---: |
| Daily close-above | 328 | 1,640 | 6,104 | 24,416 |
| Weekly close-above | 193 | 2,347 | 18,720 | 74,880 |
| Monthly close-above | 61 | 741 | 20,259 | 81,036 |
| Weekly range | 177 | 1,947 | 16,507 | 66,028 |
| Monthly range | 59 | 825 | 22,363 | 89,452 |
| Up/down | 1,298 | 1,298 | 5,060 | 20,240 |
| **Total** | **2,116** | **8,798** | **89,013** | **356,052** |

### Estimated Size

Based on the [[Telonex-Data-Quality-Report|NVDA POC]] reference files:
- `book_snapshot_25` averages ~1.1 MB per market-day per outcome
- `book_snapshot_full` estimated at ~1.9 MB (1.7x for double the depth levels)
- `trades` estimated at ~150 KB per market-day per outcome

**~4.1 MB per market-day** (all 4 files) => **~356 GB total**.

---

## 4. Discovery Pipeline

Discovery identifies all markets, classifies them, and produces a download manifest. Already implemented in `scripts/discover_markets.py`.

### Step 1: Telonex Free Markets Dataset

```python
from telonex import get_markets_dataframe
markets = get_markets_dataframe(exchange="polymarket")
```

Free, no downloads used. Provides for every market:
- `slug`, `event_slug`, `question`
- `asset_id_0` (YES), `asset_id_1` (NO)
- `start_date_us`, `end_date_us`, `created_at_us`
- `book_snapshot_full_from`, `book_snapshot_full_to` — date range of available data
- `trades_from`, `trades_to`
- `status`, `result_id` — resolution outcome

### Step 2: Classification

Regex-based slug pattern matching assigns each event to a (category, recurrence) pair. See `STOCK_PATTERNS` and `INDEX_EXTRA_PATTERNS` in the discovery script.

### Step 3: Gamma API Enrichment

For each event, `GET https://gamma-api.polymarket.com/events/slug/{slug}` provides:
- `series[].recurrence` — authoritative daily/weekly/monthly
- `series[].slug`, `series[].id` — series grouping
- `markets[].clobTokenIds` — on-chain token IDs
- `markets[].groupItemThreshold` — strike ordering
- `markets[].groupItemTitle` — human-readable strike label (e.g., "↑ $212", "$175-$180")
- `negRisk` — whether the event uses neg-risk contract structure

Responses are cached to `data/discovery/gamma_cache/{event_slug}.json`.

### Step 4: Output

- `data/discovery/market_inventory.json` — complete structured inventory (ticker -> category -> recurrence -> events -> markets)
- `data/discovery/classified_markets.parquet` — flat DataFrame for analysis

### Running

```bash
# Full discovery with Gamma enrichment (~9 min, cached after first run)
python scripts/discover_markets.py

# Fast, no API calls
python scripts/discover_markets.py --skip-gamma

# Subset of tickers
python scripts/discover_markets.py --ticker NVDA META TSLA
```

---

## 5. Data Storage Layout

### Raw Layer (untouched Telonex downloads)

```
data/
  raw/
    telonex/
      markets.parquet                                # Free markets dataset
      polymarket/
        {market_slug}/                               # One dir per market (one strike)
          book_snapshot_full_yes_{date}.parquet
          book_snapshot_full_no_{date}.parquet
          trades_yes_{date}.parquet
          trades_no_{date}.parquet

    gamma/                                           # Cached Gamma API responses
      events/{event_slug}.json
      series/{series_slug}.json
```

Raw files are stored exactly as Telonex delivers them. No renaming, no processing. This preserves the ability to re-process if the structured format changes.

### Structured Layer (engine-ready)

```
data/
  structured/
    events/
      {ticker}/
        {recurrence}/                                # daily, weekly, monthly
          {event_slug}/
            metadata.json                            # See schema below
            book_yes_{strike}_{date}.parquet          # Types converted, BBO pre-computed
            book_no_{strike}_{date}.parquet
            trades_yes_{strike}_{date}.parquet
            trades_no_{strike}_{date}.parquet
            timeline.parquet                          # Unified chronological event stream

  underlying/                                        # Stock/index prices
    {ticker}/
      ohlcv_1m_{date}.parquet
      options_chain_{date}.parquet
```

### metadata.json Schema

Each event directory contains a `metadata.json` with everything the backtesting engine needs:

```json
{
  "event_slug": "nvda-close-above-on-march-30-2026",
  "ticker": "NVDA",
  "ticker_type": "stock",
  "category": "close_above",
  "recurrence": "daily",
  "series_slug": "nvidia-multi-strikes-daily",
  "series_id": "10500",
  "start_date": "2026-03-30T13:30:00Z",
  "end_date": "2026-03-30T20:00:00Z",
  "neg_risk": true,
  "strikes": [
    {
      "strike": 160,
      "group_item_threshold": "1",
      "market_slug": "nvda-close-above-160-on-march-30-2026",
      "clob_token_id_yes": "...",
      "clob_token_id_no": "...",
      "asset_id_yes": "...",
      "asset_id_no": "...",
      "resolution": "YES",
      "data_dates": ["2026-03-29", "2026-03-30"],
      "channels": ["book_snapshot_full", "trades"]
    }
  ],
  "raw_sources": [
    "raw/telonex/polymarket/nvda-close-above-160-on-march-30-2026/"
  ]
}
```

---

## 6. Download Script Architecture

The download script reads the inventory from discovery and downloads all data systematically.

### Design Principles

1. **Idempotent**: Re-running skips already-downloaded files. Checks file existence before each download.
2. **Resumable**: Tracks progress in a state file. Can be killed and restarted.
3. **Rate-limited**: Respects Telonex API rate limits with configurable delay between requests.
4. **Raw-first**: Downloads go directly to `data/raw/telonex/polymarket/`. Processing into structured format is a separate step.

### Download Order

Downloads are prioritized for maximum backtesting value early:

1. **Daily close-above** — highest market activity, most events, simplest structure
2. **Weekly close-above** — next most liquid
3. **Weekly range** — complements weekly close-above
4. **Monthly close-above** — longer duration, good for strategy calibration
5. **Monthly range** — complements monthly close-above
6. **Up/down** — single binary, lowest priority

Within each category, download in reverse chronological order (newest first) so we can begin backtesting recent data while older data downloads.

### Progress Tracking

```json
{
  "started_at": "2026-04-01T...",
  "total_downloads": 356052,
  "completed": 12400,
  "failed": 3,
  "last_completed": "nvda-close-above-160-on-march-30-2026/book_snapshot_full_yes_2026-03-30",
  "failures": [
    {"market_slug": "...", "channel": "...", "error": "404", "timestamp": "..."}
  ]
}
```

---

## 7. Processing Pipeline (Raw -> Structured)

After downloading, a separate processing script transforms raw data into the engine-ready structured format.

### Steps per Event

1. **Load raw parquet files** for all markets in the event
2. **Convert types**: string price/size columns to float64
3. **Compute BBO**: best_bid, best_ask, mid, spread columns
4. **Filter invalid rows**: remove snapshots with missing/crossed BBO
5. **Build unified timeline**: merge all strikes + both tokens into chronological event stream
6. **Write metadata.json**: aggregate all market metadata, strike info, resolution outcomes
7. **Write processed parquet files**: one per strike x token x channel x date
8. **Write timeline.parquet**: the unified event stream for the backtesting engine

### Idempotent Re-processing

If the processing logic changes (e.g., new BBO filter, additional computed columns), re-run from raw:

```bash
python scripts/process_raw.py                    # Process everything
python scripts/process_raw.py --ticker NVDA      # Just one ticker
python scripts/process_raw.py --force             # Reprocess even if structured files exist
```

---

## 8. Concurrent Event Simulation

The backtesting engine may simulate multiple concurrent events (e.g., NVDA daily close-above + NVDA weekly close-above + NVDA monthly range all active on the same day). The data layout supports this:

1. Each event is a self-contained directory with its own `metadata.json` and `timeline.parquet`
2. The engine loads multiple event directories and merges their timelines into a single priority queue
3. `metadata.json` contains `start_date` / `end_date` for finding overlapping events:

```python
# Find all events active on a given date for a ticker
active = [e for e in all_events
          if e["ticker"] == "NVDA"
          and e["start_date"] <= target_date <= e["end_date"]]
```

---

## 9. Appendix: Slug Pattern Reference

### Stock Tickers (AAPL, AMZN, GOOGL, META, MSFT, NFLX, NVDA, PLTR, TSLA)

| Category | event_slug pattern | market_slug pattern |
| --- | --- | --- |
| Daily close-above | `{t}-close-above-on-{date}` | `{t}-close-above-{strike}-on-{date}` |
| Weekly close-above | `{t}-above-on-{date}` | `{t}-above-{strike}-on-{date}` |
| Monthly close-above | `{t}-above-in-{month}-{year}` | `{t}-above-{strike}-on-{month-end-date}` |
| Weekly range | `{t}-week-{date}` | `will-{t}-close-{above\|below\|between}-{strike}-week-{date}` |
| Monthly range | `what-price-will-{t}-hit-in-{month}-{year}` | `will-{t}-{reach\|dip-to}-{strike}-in-{month}` |
| Up/down | `{t}-up-or-down-on-{date}` | `{t}-up-or-down-on-{date}` |

### Index Tickers (SPX, NDX)

Index slugs are less consistent. Examples:
- `spx-above-on-november-14-2025`
- `sp-500-spx-above-end-of-march`
- `spx-close-jan-2026-192`
- `what-will-spx-hit-in-march-2026`
- `what-will-sp-500-spx-hit-by-end-of-march`
- `spx-opens-up-or-down-on-{date}` (SPX has "opens" variant)

Handled by dedicated regex patterns in the discovery script.

---

## 10. Gamma API Series Reference

From the 416 events cached so far:

| Series Slug | Recurrence | Title | Example Tickers |
| --- | --- | --- | --- |
| `nvidia-multi-strikes-weekly` | weekly | Nvidia Multi Strikes Weekly | NVDA |
| `nvidia-hit-price-monthly` | monthly | Nvidia Hit Price Monthly | NVDA |
| `nvda-neg-risk-weekly` | weekly | NVDA Neg Risk Weekly | NVDA (range) |
| `microsoft-multi-strikes-monthly` | monthly | Microsoft Multi Strikes Monthly | MSFT |
| ... | ... | ... | ... |

Each ticker has its own series slugs. The full mapping is available in `data/discovery/gamma_cache/`.
