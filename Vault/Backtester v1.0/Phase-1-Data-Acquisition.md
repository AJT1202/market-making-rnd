---
title: "Phase 1: Data Acquisition Pipeline"
created: 2026-04-02
tags:
  - backtester-v1
  - data-pipeline
  - thetadata
  - telonex
  - download
  - parquet
  - phase-1
status: planning
related:
  - "[[Data-Alignment-Architecture]]"
  - "[[ThetaData-Options-API]]"
  - "[[ThetaData-Stock-Index-Data]]"
  - "[[Telonex-Data-Platform]]"
  - "[[Telonex-Data-Quality-Report]]"
  - "[[Engine-Architecture-Plan]]"
---

# Phase 1: Data Acquisition Pipeline

> **Goal**: Build a robust, resumable, validated data download pipeline that acquires all historical data needed for backtesting. Two sources: ThetaData (options + stock) and Telonex (Polymarket L2 orderbook + trades). Output: a complete Parquet store conforming to the [[Data-Alignment-Architecture]] storage layout.
>
> **Entry criteria**: ThetaData STANDARD subscription active, Telonex Plus subscription active, Theta Terminal JAR available.
>
> **Exit criteria**: One full month of data downloaded and validated for all 11 tickers, all relevant Polymarket markets, with zero missing trading days and passing schema/continuity checks.

---

## Table of Contents

1. [Scope & Deliverables](#1-scope--deliverables)
2. [ThetaData Download Spec](#2-thetadata-download-spec)
3. [Telonex Download Spec](#3-telonex-download-spec)
4. [Script Architecture](#4-script-architecture)
5. [Download Orchestration](#5-download-orchestration)
6. [Data Validation](#6-data-validation)
7. [Storage Estimates](#7-storage-estimates)
8. [Task Breakdown](#8-task-breakdown)
9. [Risks & Mitigations](#9-risks--mitigations)

---

## 1. Scope & Deliverables

### What Gets Downloaded

| # | Dataset | Source | Endpoint / Channel | Granularity | Purpose |
|---|---------|--------|--------------------|-------------|---------|
| 1 | **Tick-level NBBO quotes** | ThetaData | `/v3/option/history/quote?interval=tick` | Every NBBO update | Primary signal: reconstruct option chain state at any millisecond |
| 2 | **EOD Greeks** | ThetaData | `/v3/option/history/greeks/eod` | Daily 17:15 ET | Full chain with IV, all Greeks, underlying_price. Breeden-Litzenberger input |
| 3 | **Open Interest** | ThetaData | `/v3/option/history/open_interest` | Daily 06:30 ET | Liquidity weighting, contract filtering |
| 4 | **Trade-Quote** | ThetaData | `/v3/option/history/trade_quote` | Every trade | Flow analysis: each trade paired with NBBO at execution time |
| 5 | **Stock/Index OHLCV** | ThetaData | `/v3/stock/history/ohlc` + `/v3/index/history/ohlc` | 1-minute bars | Underlying price time series, cross-validation with `underlying_price` in Greeks |
| 6 | **L2 Book Snapshots** | Telonex | `book_snapshot_full` channel | Tick-level (~200ms median) | Polymarket orderbook depth for fill simulation |
| 7 | **Polymarket Trades** | Telonex | `trades` channel | Every trade | Trade-tick matching, volume analysis, fill calibration |

### Tickers

**Equity options**: AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, NFLX, PLTR (9 tickers)
**Index options**: SPX, SPXW (2 symbols -- both required for full SPX chain; NDX excluded due to missing index price data)
**Total**: 11 option symbols + 10 underlying price series (9 stocks + SPX index)

### Date Range

**Initial target**: 1 month -- 2026-03-02 through 2026-04-01 (~22 trading days). This covers the NVDA POC date (March 30) and provides enough data for strategy calibration.

**Stretch target**: 3 months (2026-01-02 through 2026-04-01) for out-of-sample testing.

### Deliverables

1. Extended `download_options.py` with new commands: `tick-quotes`, `trade-quote`, `stock-ohlc`
2. New `download_telonex.py` for Polymarket data
3. Validation script `validate_data.py`
4. Complete Parquet store under `Code/data/` matching [[Data-Alignment-Architecture]] layout
5. Download manifest JSON tracking what has been downloaded, when, and validation status

---

## 2. ThetaData Download Spec

### 2.1 Tick-Level NBBO Quotes (Largest Dataset)

**Endpoint**: `GET /v3/option/history/quote`

**Parameters per request**:
```
symbol:     {TICKER}
expiration: {YYYYMMDD}         # One expiry per request (required for tick)
strike:     *                  # All strikes (filtered post-download)
right:      both
date:       {YYYYMMDD}         # Single day (tick data: 1-day max)
interval:   tick
start_time: 09:30:00
end_time:   16:15:00           # Include 15 min after close for settlement
format:     ndjson
```

**Critical constraint**: Tick-level data requires `interval=tick` and is limited to **1 day per request**. You must also specify a single expiration -- wildcard `*` is not supported for tick interval across multiple expiries in a single request.

**Smart filtering algorithm** -- determining which contracts to download:

```
For each (ticker, date):
  1. Query EOD Greeks for that date (already downloaded in dataset #2)
  2. Extract underlying_price from any row -> spot_price
  3. Filter contracts where:
     a. strike is within 20% of spot_price:
        strike >= spot_price * 0.80  AND  strike <= spot_price * 1.20
     b. expiration is within 30 DTE of the download date:
        0 <= (expiration - date).days <= 30
  4. Collect the unique expiration dates passing both filters
  5. Download tick quotes for each (ticker, expiration, date) tuple
```

This filtering is critical because downloading *every* expiry would be wasteful -- most volume concentrates in near-term, near-ATM contracts. For a stock like NVDA at $165:
- 20% ATM range: $132 -- $198
- 30 DTE: expiries through ~May 2
- Typical result: 4-8 relevant expiry dates per ticker

**Request count per day**: ~11 tickers x ~6 expiries avg = ~66 requests/day. At 4 concurrent, ~17 batches x ~5s each = ~85 seconds per trading day (excluding transfer time).

**Response fields**: `symbol`, `expiration`, `strike`, `right`, `timestamp`, `bid_size`, `bid_exchange`, `bid`, `bid_condition`, `ask_size`, `ask_exchange`, `ask`, `ask_condition`

**Post-download processing**:
- Parse ndjson to Polars DataFrame
- Convert timestamps to `timestamp_us` (int64 microseconds, UTC)
- Filter to strikes within 20% ATM (remove far OTM/ITM noise)
- Sort by `timestamp` ascending (required for binary search in DataProvider)
- Save as `tick_quotes/{YYYY-MM-DD}/{TICKER}_{EXPIRY_YYYYMMDD}.parquet` with zstd compression

**Estimated size**: Each NBBO update is ~100 bytes compressed. A liquid equity option chain generates ~500K-2M tick updates per day across all near-ATM strikes. Per ticker per day: **50-200 MB**. For 11 tickers: **~500 MB - 2 GB/day**. Actual size depends heavily on market activity and number of active contracts.

Conservative estimate for 22 trading days: **11 - 44 GB total**.

### 2.2 EOD Greeks

**Endpoint**: `GET /v3/option/history/greeks/eod`

**Parameters per request**:
```
symbol:     {TICKER}
expiration: *                  # All expirations
strike:     *                  # All strikes
right:      both
start_date: {YYYYMMDD}
end_date:   {YYYYMMDD}        # Can span up to 1 month
format:     ndjson
```

**Already implemented** in `download_options.py` as the `eod` command. Current implementation downloads one day at a time per ticker, saving combined output per date.

**Response fields**: Full Greeks set -- `delta`, `gamma`, `theta`, `vega`, `rho`, `vanna`, `charm`, `vomma`, `veta`, `vera`, `speed`, `zomma`, `color`, `ultima`, `implied_vol`, `iv_error`, `underlying_price`, `d1`, `d2`, plus OHLC and bid/ask.

**Existing data**: We already have March 30, March 31, and April 1 downloaded:
- `greeks_20260401.parquet`: 5.2 MB (all 11 tickers, all contracts)
- `oi_20260401.parquet`: 377 KB

**Estimated size per day**: ~5 MB (Greeks) + ~0.4 MB (OI) = **~5.4 MB/day**
**22 trading days**: ~119 MB total. Trivial.

**Storage path**: `thetadata/eod/{YYYYMMDD}/greeks_{YYYYMMDD}.parquet`

### 2.3 Open Interest

**Endpoint**: `GET /v3/option/history/open_interest`

**Parameters per request**:
```
symbol:     {TICKER}
expiration: *
date:       {YYYYMMDD}
format:     ndjson
```

**Already implemented** in `download_options.py` as part of the `eod` command (unless `--skip-oi` is passed).

**Response fields**: `symbol`, `expiration`, `strike`, `right`, `timestamp`, `open_interest`

OI is reported once daily by OPRA at ~06:30 ET, representing previous-day EOD figures.

**Estimated size**: ~0.4 MB/day. **22 days**: ~9 MB total.

**Storage path**: `thetadata/eod/{YYYYMMDD}/oi_{YYYYMMDD}.parquet`

### 2.4 Trade-Quote (Trades Paired with NBBO)

**Endpoint**: `GET /v3/option/history/trade_quote`

**Parameters per request**:
```
symbol:     {TICKER}
expiration: {YYYYMMDD}         # Single expiry for tick-level
strike:     *
right:      both
date:       {YYYYMMDD}         # 1-day max
format:     ndjson
```

**Response fields**: `symbol`, `expiration`, `strike`, `right`, `timestamp`, `price`, `size`, `condition`, `exchange`, `bid`, `bid_size`, `ask`, `ask_size`, `bid_exchange`, `ask_exchange`

This pairs every executed trade with the prevailing NBBO at the time of execution. Critical for:
- Measuring effective spread (trade price vs. midpoint)
- Identifying informed vs. uninformed flow
- Calibrating adverse selection parameters for the market-making strategy

**Filtering**: Same smart filter as tick quotes (20% ATM, 30 DTE). Download in lockstep with tick quotes -- same (ticker, expiry, date) tuples.

**Estimated size**: Trade events are less frequent than quote updates. Roughly 10-30% the size of tick quotes. Per day: **~50-300 MB** for all tickers.

**Storage path**: `thetadata/trade_quote/{YYYY-MM-DD}/{TICKER}_{EXPIRY_YYYYMMDD}.parquet`

### 2.5 Stock/Index OHLCV (1-Minute Bars)

**Endpoint**: `GET /v3/stock/history/ohlc` (stocks) and `GET /v3/index/history/ohlc` (SPX)

**Parameters**:
```
# Stocks (can do multi-day, up to 1 month per request)
symbol:     {TICKER}
start_date: {YYYYMMDD}
end_date:   {YYYYMMDD}
interval:   1m
start_time: 09:30:00
end_time:   16:00:00
format:     ndjson

# Index (SPX) -- same params but /v3/index/history/ohlc
symbol:     SPX
```

**Response fields**: `timestamp`, `open`, `high`, `low`, `close`, `volume`, `count`, `vwap`

**Ticker notes**:
- AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, NFLX: NASDAQ/UTP tape, full history from 2016
- PLTR: NYSE/CTA tape, history from 2020-09-30 (IPO) -- verify availability on STANDARD tier
- SPX: Use `/v3/index/history/ohlc`, CGIF feed, STANDARD gets ~1-second resolution from 2022-01-01
- NDX: **Not available** from ThetaData -- skip

Multi-day requests are supported (up to 1 month), so we can download all 22 days in a single request per ticker. Total: 10 requests.

**Estimated size**: ~390 1-minute bars/day x 22 days x 10 tickers = ~85,800 bars. At ~50 bytes/bar compressed: **~4 MB total**. Negligible.

**Storage path**: `thetadata/stock_ohlc/{TICKER}_1m.parquet` (single file per ticker for the date range)

---

## 3. Telonex Download Spec

### 3.1 Data Channels Required

| Channel | Description | Use Case |
|---------|-------------|----------|
| `book_snapshot_full` | Complete L2 orderbook state at every captured snapshot | Fill simulation, depth analysis, queue position |
| `trades` | Every executed trade on Polymarket | Trade-tick matching, volume, fill calibration |

We skip `quotes` (BBO-only -- redundant with `book_snapshot_full`), `book_snapshot_5`/`book_snapshot_25` (subset of full), and `onchain_fills` (blockchain-level -- useful later for wallet analysis but not needed for Phase 1).

### 3.2 Market Discovery Pipeline

Before downloading, we need to identify which Polymarket markets correspond to our ticker/strike/expiry universe. The existing `discover_markets.py` script handles initial classification using the free Telonex markets dataset.

**Step 1**: Download the markets dataset (free, no auth):
```python
from telonex import get_markets_dataframe
markets = get_markets_dataframe(exchange="polymarket")
```

**Step 2**: Filter to stock/index markets with orderbook data:
```python
stock_markets = markets[
    markets['question'].str.contains('AAPL|MSFT|GOOGL|AMZN|META|NVDA|TSLA|NFLX|PLTR|SPX',
                                     case=False, na=False)
    & (markets['book_snapshot_full_from'].notna())
    & (markets['book_snapshot_full_from'] != '')
]
```

**Step 3**: Parse each market into structured fields using regex on the `question` or `slug`:
- **Ticker**: Extract from slug prefix (e.g., `nvda-close-above-165-on-march-30` -> NVDA)
- **Strike**: Extract numeric value (e.g., `above-165` -> $165)
- **Expiry**: Extract date (e.g., `on-march-30` -> 2026-03-30)
- **Market type**: `close_above` (daily), `above_on` (weekly), `up_or_down`, `range`

This logic already exists in `discover_markets.py` (the `STOCK_PATTERNS` and `INDEX_EXTRA_PATTERNS` regex maps). The output is saved as `classified_markets.parquet` at `Code/data/discovery/classified_markets.parquet`.

**Step 4**: Build the **market registry** conforming to the [[Data-Alignment-Architecture]] schema:

| Column | Source |
|--------|--------|
| `market_slug` | Telonex markets dataset `slug` column |
| `ticker` | Parsed from slug/question |
| `strike` | Parsed from slug/question |
| `expiry` | Parsed from `end_date_us` or slug |
| `asset_id_yes` | `asset_id_0` from markets dataset |
| `asset_id_no` | `asset_id_1` from markets dataset |
| `market_id` | `market_id` from markets dataset |
| `resolution` | Derived from `result_id` (0=YES, 1=NO, null=unresolved) |
| `data_from` | `book_snapshot_full_from` |
| `data_to` | `book_snapshot_full_to` |

Save to: `Code/data/aligned/market_registry.parquet`

### 3.3 Download Strategy

**SDK approach** (preferred): Use the Telonex Python SDK's `download()` function with `force_download=False` for automatic caching and resume.

```python
from telonex import download

for market in registry.iter_rows(named=True):
    slug = market["market_slug"]
    date_from = market["data_from"]
    date_to = market["data_to"]

    # Download book snapshots for YES token
    download(
        api_key=API_KEY,
        exchange="polymarket",
        channel="book_snapshot_full",
        slug=slug,
        outcome="Yes",
        from_date=date_from,
        to_date=date_to,
        download_dir=f"./data/telonex/book_raw/{slug}/",
        concurrency=5,
        verbose=True,
    )

    # Download trades for YES token
    download(
        api_key=API_KEY,
        exchange="polymarket",
        channel="trades",
        slug=slug,
        outcome="Yes",
        from_date=date_from,
        to_date=date_to,
        download_dir=f"./data/telonex/trades_raw/{slug}/",
        concurrency=5,
        verbose=True,
    )
```

**Per-market download**: Each market is a unique slug. For each market, download both `book_snapshot_full` and `trades` for every date in the market's active range. Download both YES and NO outcomes (both sides are needed for complete book reconstruction).

**SDK handles**:
- Retry with exponential backoff (up to 5 attempts)
- Rate limit respect via `Retry-After` header
- Atomic file writes (temp file + `os.replace()`)
- Caching (skip already-downloaded files)

### 3.4 Scale Estimate

From the [[Telonex-Data-Quality-Report]]:
- NVDA POC: 5 strikes x 1 day = 5 files, 5.62 MB total (book_snapshot_25)
- `book_snapshot_full` will be ~2-5x larger due to deeper depth levels

For the 1-month target:
- ~10 tickers x ~10 active daily markets per ticker x 22 trading days = ~2,200 market-days
- At ~2 MB/market-day for book_snapshot_full: **~4.4 GB total**
- Trades are smaller (~0.5 MB/market-day): **~1.1 GB total**
- **Total Telonex**: ~5.5 GB

### 3.5 What Needs Research

Several unknowns remain for the Telonex integration:

1. **Full book schema**: The `book_snapshot_full` schema has not been documented by Telonex. The POC used `book_snapshot_25` which has 107 columns (5 metadata + 100 price/size for 25 levels). `book_snapshot_full` likely has more depth levels -- need to download one file and inspect `df.columns` to determine exact schema.

2. **Date coverage per market**: Telonex off-chain data starts 2025-10-11. Our 1-month target (March 2-April 1, 2026) is well within range. However, individual markets may have shorter coverage if they were created after their underlying event was listed.

3. **Market-to-strike mapping accuracy**: The regex-based parsing in `discover_markets.py` handles common slug patterns but may miss edge cases (e.g., range markets, weekly finish markets). Need to validate the registry against a manual sample.

4. **YES vs NO token downloads**: Do we need both? The YES token orderbook already implies the NO side (YES_price + NO_price = $1.00), but downloading both independently provides a validation cross-check and captures any momentary deviations.

5. **Telonex rate limits**: The docs say "generous for typical data analysis workflows" but don't specify exact limits. The SDK's `concurrency=5` default should be safe. Monitor for 429 responses during bulk downloads.

---

## 4. Script Architecture

### 4.0 Cross-Platform & External SSD

All downloaded data lives on an **external SSD** (exFAT formatted for macOS + Windows compatibility). The data root is configured in `config.toml` at the project root:

```toml
[paths]
data_dir = "/Volumes/SSD/polymarket-data"  # macOS
# data_dir = "E:\\polymarket-data"          # Windows
```

All scripts read `data_dir` from this config. **Rules:**
- Use `pathlib.Path` everywhere — never hardcode `/` or `\`
- Never assume the data directory is under the project root
- Parquet files are binary-portable across platforms — no conversion needed
- Development/testing uses small datasets on local disk; full downloads target the SSD

### 4.1 Extending `download_options.py`

The existing script at `Code/scripts/download_options.py` has three commands (`eod`, `intraday-iv`, `intraday-quotes`) and shared infrastructure (async HTTP client with concurrency semaphore, ndjson parser, Parquet save helper). We extend it with three new commands.

**New commands**:

#### `tick-quotes` -- Tick-Level NBBO

```
python download_options.py tick-quotes --date 2026-03-30
python download_options.py tick-quotes --start 2026-03-02 --end 2026-04-01
python download_options.py tick-quotes --ticker NVDA --date 2026-03-30
```

Logic:
1. Load EOD Greeks for the target date (must already be downloaded)
2. Run smart filter to determine (ticker, expiry) pairs
3. For each pair, fetch tick-level quotes (1 day, 1 expiry per request)
4. Filter response to 20% ATM strikes
5. Convert timestamps, sort, save as Parquet

#### `trade-quote` -- Trades Paired with NBBO

```
python download_options.py trade-quote --date 2026-03-30
python download_options.py trade-quote --start 2026-03-02 --end 2026-04-01
```

Logic: Identical to `tick-quotes` but hits `/v3/option/history/trade_quote`. Downloads the same (ticker, expiry, date) tuples.

#### `stock-ohlc` -- Underlying Price Bars

```
python download_options.py stock-ohlc --start 2026-03-02 --end 2026-04-01
python download_options.py stock-ohlc --ticker NVDA --start 2026-03-02 --end 2026-04-01
```

Logic:
1. For each equity ticker: `GET /v3/stock/history/ohlc` with `interval=1m`
2. For SPX: `GET /v3/index/history/ohlc` with `interval=1m`
3. Multi-day request (up to 1 month in a single call)
4. Save one Parquet file per ticker

### 4.2 New Script: `download_telonex.py`

Separate script for Telonex downloads. Depends on the market registry produced by `discover_markets.py`.

```
python download_telonex.py --start 2026-03-02 --end 2026-04-01
python download_telonex.py --ticker NVDA --start 2026-03-30 --end 2026-03-30
python download_telonex.py --channel book_snapshot_full --start 2026-03-02 --end 2026-04-01
```

Logic:
1. Load market registry from `Code/data/aligned/market_registry.parquet`
2. Filter to requested tickers and date range
3. For each market, download `book_snapshot_full` and `trades` for both YES and NO outcomes
4. Use Telonex SDK `download()` with `force_download=False` for resume
5. Rename/move files to match [[Data-Alignment-Architecture]] layout

### 4.3 Shared Utilities

Extract common code into `Code/scripts/download_utils.py`:

```python
# download_utils.py

# Constants
BASE_URL = "http://127.0.0.1:25503/v3"
CONCURRENCY = 4
TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX", "PLTR"]
INDEX_TICKERS = ["SPX", "SPXW"]
ALL_TICKERS = TICKERS + INDEX_TICKERS
DATA_DIR = Path(__file__).parent.parent / "data"

# Shared functions
async def fetch(client, endpoint, params, semaphore) -> str | None
def ndjson_to_df(text: str) -> pl.DataFrame
def save_parquet(df, path, label)
def trading_days(start: date, end: date) -> list[date]
def load_eod_greeks(dt: date) -> pl.DataFrame
def smart_filter(greeks_df, pct_atm=0.20, max_dte=30) -> list[tuple[str, str]]
def load_manifest(path) -> dict
def update_manifest(path, key, status)
```

### 4.4 Error Handling and Resume-on-Failure

**ThetaData error codes**:
| Code | Meaning | Action |
|------|---------|--------|
| 200 | Success | Process response |
| 429 | Queue full / rate limit | Wait 2s, retry (max 3 retries) |
| 472 | NO_DATA | Log and skip -- mark as "no_data" in manifest |
| 5xx | Server error | Wait 5s, retry (max 3 retries) |
| Timeout | Read timeout (>120s) | Retry with 180s timeout, then 300s |

**Telonex error codes**:
| Code | Meaning | Action |
|------|---------|--------|
| 401 | Invalid API key | Abort with clear message |
| 403 | Entitlement error | Log `downloads_remaining`, abort if 0 |
| 404 | No data for market/date | Log and skip |
| 429 | Rate limited | Wait `retry_after` seconds from header |

**Resume mechanism**: The download manifest (JSON file at `Code/data/download_manifest.json`) tracks:

```json
{
  "thetadata": {
    "tick_quotes": {
      "2026-03-30": {
        "NVDA_20260403": {"status": "complete", "rows": 1234567, "size_mb": 85.2, "downloaded_at": "..."},
        "NVDA_20260410": {"status": "complete", "rows": 987654, "size_mb": 72.1, "downloaded_at": "..."},
        "AAPL_20260403": {"status": "failed", "error": "timeout", "attempts": 3},
        "SPX_20260403": {"status": "no_data"}
      }
    },
    "eod": { ... },
    "trade_quote": { ... }
  },
  "telonex": {
    "book_snapshot_full": {
      "nvda-close-above-165-on-march-30-2026/Yes": {
        "2026-03-30": {"status": "complete", "rows": 39346, "size_mb": 1.2}
      }
    }
  }
}
```

On restart, the script reads the manifest and skips any entry with `status: "complete"`. Failed entries are retried. `no_data` entries are skipped permanently.

---

## 5. Download Orchestration

### 5.1 Dependency Order

Downloads must proceed in this order due to data dependencies:

```
Phase A (no dependencies):
  [A1] EOD Greeks      -- all dates, all tickers
  [A2] Open Interest   -- all dates, all tickers
  [A3] Stock OHLCV     -- all dates, all tickers
  [A4] Market Registry -- discover and classify Polymarket markets

Phase B (depends on A1 + A4):
  [B1] Tick Quotes     -- uses EOD Greeks for smart filtering
  [B2] Trade-Quote     -- same filter as tick quotes
  [B3] Telonex Books   -- uses market registry for slug/asset_id mapping
  [B4] Telonex Trades  -- uses market registry
```

Phase A takes ~30 minutes total. Phase B is the heavy lift (~hours to days).

### 5.2 Parallelism Strategy

**ThetaData** (STANDARD tier: 4 concurrent requests):
- Use `asyncio.Semaphore(4)` to enforce the concurrency cap
- Within a single day: 11 tickers x ~6 expiries = ~66 requests for tick quotes. At 4 concurrent: ~17 sequential batches
- Across days: process one day at a time to keep memory bounded. After each day completes, write Parquet files and release memory
- Estimated throughput: ~2-5 minutes per ticker-day for tick quotes (depends on chain size and network)

**Telonex** (SDK default: 5 concurrent):
- The SDK handles concurrency internally via semaphore
- Download one market at a time (all dates for that market), then move to the next
- The SDK's `force_download=False` caching avoids re-downloading existing files
- Estimated throughput: ~2-3 seconds per file (S3 presigned URL download)

**Overall timeline for 1-month download**:

| Dataset | Requests | Est. Time | Parallelism |
|---------|----------|-----------|-------------|
| EOD Greeks | 11 tickers x 22 days = 242 | ~15 min | 4 concurrent |
| Open Interest | 11 x 22 = 242 | ~10 min | 4 concurrent |
| Stock OHLCV | 10 requests (1 per ticker, multi-day) | ~2 min | 4 concurrent |
| Tick Quotes | ~66/day x 22 days = ~1,452 | **~4-8 hours** | 4 concurrent |
| Trade-Quote | ~1,452 | **~2-4 hours** | 4 concurrent |
| Telonex Books | ~2,200 market-day files | **~2-3 hours** | 5 concurrent |
| Telonex Trades | ~2,200 market-day files | **~1-2 hours** | 5 concurrent |
| **Total** | | **~10-18 hours** | |

Recommendation: run Phase A immediately, then start Phase B overnight. The resume mechanism means you can safely interrupt and restart.

### 5.3 Checkpointing and Progress

**Progress tracking**: Each command prints a running summary:
```
[tick-quotes] Day 5/22 (2026-03-06) | NVDA exp=20260313 | 3/6 expiries done
[tick-quotes] Progress: 23% complete | 127 of 552 downloads | 4.2 GB written
[tick-quotes] ETA: ~3.5 hours remaining
```

**Checkpoint frequency**: Manifest is updated after every successful file write (not per-request, per-file). This means a crash mid-download loses at most one file's worth of work.

**Disk space monitoring**: Before each day's download begins, check available disk space. Abort if < 10 GB free (with a clear error message suggesting cleanup or alternative storage).

### 5.4 Running the Full Pipeline

```bash
# Step 1: Ensure Theta Terminal is running
java -jar ~/ThetaTerminalV3.jar &

# Step 2: Phase A downloads (fast, ~30 min)
cd /Users/alex/market-making-rnd/Code

python scripts/download_options.py eod --start 2026-03-02 --end 2026-04-01
python scripts/download_options.py stock-ohlc --start 2026-03-02 --end 2026-04-01
python scripts/discover_markets.py  # Refresh market registry

# Step 3: Phase B downloads (slow, run overnight)
python scripts/download_options.py tick-quotes --start 2026-03-02 --end 2026-04-01
python scripts/download_options.py trade-quote --start 2026-03-02 --end 2026-04-01
python scripts/download_telonex.py --start 2026-03-02 --end 2026-04-01

# Step 4: Validate
python scripts/validate_data.py --start 2026-03-02 --end 2026-04-01
```

---

## 6. Data Validation

### 6.1 Post-Download Checks

Run `validate_data.py` after all downloads complete. Checks are organized by severity.

**CRITICAL (fail the pipeline)**:
1. **File existence**: Every expected Parquet file exists for every trading day
2. **Schema match**: Column names and types match the [[Data-Alignment-Architecture]] spec
3. **Non-empty**: Every file has > 0 rows
4. **Timestamp monotonicity**: All files are sorted ascending by timestamp with no out-of-order rows
5. **No duplicate timestamps**: Within any single file, no two rows share the exact same (strike, right, timestamp) tuple

**WARNING (log but continue)**:
6. **Row count bounds**: Tick quote files should have > 1,000 rows per expiry per day (fewer suggests a data gap or illiquid expiry)
7. **Timestamp continuity**: No gaps > 30 minutes during regular trading hours (9:30-16:00 ET) for tick quotes
8. **Cross-source consistency**: EOD Greeks `underlying_price` should be within 0.5% of the stock OHLCV close for the same day
9. **Strike range sanity**: All strikes in tick_quotes files fall within 20% of the underlying price from EOD Greeks

**INFO (report only)**:
10. **Storage summary**: Total GB by dataset, average file size, largest file
11. **Coverage matrix**: 11 tickers x 22 days heatmap showing row counts (green = good, yellow = low, red = missing)
12. **Telonex cross-check**: For each market in the registry, verify book_snapshot_full and trades files exist for every date between data_from and data_to

### 6.2 Spot-Check Samples

For 3 randomly selected (ticker, date) combinations, the validator performs deep checks:
- Load tick quotes and verify bid <= ask for 99.9%+ of rows
- Load EOD Greeks and verify strike monotonicity (same expiry, calls: price decreases with strike)
- Load Telonex book snapshots and verify bid_price_0 < ask_price_0 for 99.9%+ of rows
- Cross-reference: option `underlying_price` at 15:55 ET vs stock OHLCV close

### 6.3 Validation Output

The validator writes `Code/data/validation_report.json`:

```json
{
  "run_at": "2026-04-02T18:00:00Z",
  "date_range": ["2026-03-02", "2026-04-01"],
  "status": "PASS",  // or "FAIL" or "WARN"
  "critical_failures": [],
  "warnings": [
    {"check": "row_count_bounds", "file": "tick_quotes/2026-03-14/PLTR_20260320.parquet", "rows": 847, "threshold": 1000}
  ],
  "summary": {
    "total_files": 1894,
    "total_size_gb": 28.4,
    "trading_days": 22,
    "tickers_complete": 11,
    "coverage_pct": 99.7
  }
}
```

---

## 7. Storage Estimates

### Per-Day Estimates

| Dataset | Per Day | Compression | Notes |
|---------|---------|-------------|-------|
| Tick Quotes | ~500 MB - 2 GB | zstd | Largest dataset; varies by market activity |
| Trade-Quote | ~50 - 300 MB | zstd | ~10-30% of tick quotes volume |
| EOD Greeks | ~5 MB | zstd | Already validated from existing downloads |
| Open Interest | ~0.4 MB | zstd | Already validated |
| Stock OHLCV | ~0.2 MB | zstd | 10 tickers x 390 bars |
| Telonex Books | ~100 - 200 MB | snappy (native) | ~2 MB/market x ~50-100 active markets |
| Telonex Trades | ~25 - 50 MB | snappy (native) | ~0.5 MB/market x ~50-100 active markets |
| **Daily Total** | **~700 MB - 2.8 GB** | | |

### Monthly Totals (22 Trading Days)

| Dataset | 1 Month | 3 Months (66 days) |
|---------|---------|---------------------|
| Tick Quotes | 11 - 44 GB | 33 - 132 GB |
| Trade-Quote | 1.1 - 6.6 GB | 3.3 - 20 GB |
| EOD Greeks | 110 MB | 330 MB |
| Open Interest | 9 MB | 27 MB |
| Stock OHLCV | 4 MB | 12 MB |
| Telonex Books | 2.2 - 4.4 GB | 6.6 - 13 GB |
| Telonex Trades | 0.5 - 1.1 GB | 1.5 - 3.3 GB |
| **Total** | **15 - 56 GB** | **45 - 169 GB** |

### Disk Space Requirements

- **Minimum for 1-month target**: 60 GB free (allows for 56 GB data + temporary download files)
- **Recommended for 3-month stretch**: 200 GB free
- **Current disk usage**: Check with `df -h /Users/alex/` before starting

### Download Bandwidth

At ~30 GB average for 1 month:
- On a 100 Mbps connection: ~40 minutes of pure transfer (but requests are the bottleneck, not bandwidth)
- ThetaData is local terminal -> network bottleneck is ThetaData's MDDS backend, not local bandwidth
- Telonex downloads from S3 presigned URLs -> network dependent, typically fast

---

## 8. Task Breakdown

### Phase A: Foundation (Est. 4-6 hours dev time)

| # | Task | Dependencies | Effort | Description |
|---|------|-------------|--------|-------------|
| A1 | Extract shared utilities into `download_utils.py` | None | 1h | Move `fetch()`, `ndjson_to_df()`, `save_parquet()`, constants into shared module. Add `trading_days()`, `smart_filter()`, `load_eod_greeks()`. Update `download_options.py` imports. |
| A2 | Implement download manifest | A1 | 1h | JSON-based manifest with read/update/query functions. Tracks status per file. Resume logic: skip `complete`, retry `failed`, skip `no_data`. |
| A3 | Add `stock-ohlc` command | A1 | 1h | New CLI command. Handles both stock and index endpoints. Multi-day per request. Single Parquet per ticker. |
| A4 | Backfill EOD Greeks for March 2-29 | A1 | 0.5h | Run existing `eod` command for remaining dates. Verify with manifest. |
| A5 | Update `discover_markets.py` to produce market registry | None | 1.5h | Extend output to include `asset_id_yes`, `asset_id_no`, `data_from`, `data_to`. Save as `aligned/market_registry.parquet` conforming to [[Data-Alignment-Architecture]] schema. |

### Phase B: ThetaData Tick-Level (Est. 6-8 hours dev time)

| # | Task | Dependencies | Effort | Description |
|---|------|-------------|--------|-------------|
| B1 | Implement smart filter function | A1, A4 | 1.5h | Load EOD Greeks -> extract underlying_price -> compute 20% ATM range and 30 DTE cutoff -> return list of (ticker, expiry) tuples. Handle edge cases: no Greeks for a date, SPX/SPXW combined. |
| B2 | Add `tick-quotes` command | A1, A2, B1 | 2h | CLI command with --date, --start/--end, --ticker flags. Iterates days, applies smart filter, downloads tick NBBO for each (ticker, expiry, date). Saves per [[Data-Alignment-Architecture]] layout. Post-download strike filter. Manifest integration. |
| B3 | Add `trade-quote` command | A1, A2, B1 | 1.5h | Nearly identical to B2 but hits `/v3/option/history/trade_quote`. Shares the same smart filter output. |
| B4 | Test on single day (NVDA, March 30) | B2, B3 | 1h | End-to-end test: download tick quotes + trade quotes for one ticker on the POC date. Verify schema, row counts, file sizes against expectations. Compare underlying_price with known NVDA prices from the [[Telonex-Data-Quality-Report]]. |
| B5 | Run full ThetaData download | B4 | 0h (runtime: ~6-12h) | Overnight batch job. Monitor for failures, inspect manifest next morning. |

### Phase C: Telonex (Est. 4-6 hours dev time)

| # | Task | Dependencies | Effort | Description |
|---|------|-------------|--------|-------------|
| C1 | Create `download_telonex.py` | A5 | 2h | New script. Loads market registry, iterates markets/dates, uses Telonex SDK `download()`. CLI with --start, --end, --ticker, --channel flags. Manifest integration. File renaming to match storage layout. |
| C2 | Investigate `book_snapshot_full` schema | C1 | 0.5h | Download one file, inspect columns. Document in vault. Determine if schema differs from `book_snapshot_25` (likely more depth columns). |
| C3 | Test on NVDA March 30 markets | C1, C2 | 1h | Download all NVDA strike markets for March 30 using `book_snapshot_full`. Compare with existing `book_snapshot_25` POC data from [[Telonex-Data-Quality-Report]]. Verify row counts are similar. |
| C4 | Run full Telonex download | C3 | 0h (runtime: ~3-5h) | Batch download all markets for 1-month range. |

### Phase D: Validation (Est. 3-4 hours dev time)

| # | Task | Dependencies | Effort | Description |
|---|------|-------------|--------|-------------|
| D1 | Create `validate_data.py` | A1 | 2h | Implement all checks from Section 6. Schema validation, row counts, timestamp checks, cross-source consistency. Coverage matrix generation. |
| D2 | Run validation on full dataset | B5, C4, D1 | 1h | Execute validator. Review report. Fix any failures by re-downloading specific files. |
| D3 | Write data inventory note | D2 | 0.5h | Vault note documenting final data inventory: row counts, file sizes, coverage, known gaps. |

### Total Estimated Effort

| Phase | Dev Time | Runtime |
|-------|----------|---------|
| A: Foundation | 5h | ~30 min |
| B: ThetaData Tick | 6h | ~6-12h (overnight) |
| C: Telonex | 3.5h | ~3-5h |
| D: Validation | 3.5h | ~30 min |
| **Total** | **18h dev** | **~10-18h runtime** |

Calendar estimate: **3-4 working days** (dev and runtime overlap since downloads run overnight).

---

## 9. Risks & Mitigations

### Data Availability Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| **ThetaData returns NO_DATA (472) for tick quotes on some ticker/expiry combinations** | High | Low | Expected for illiquid expiries. The manifest marks these as `no_data` and skips. Validate that the *liquid* combinations (near-ATM, weekly expiries) have data. |
| **NDX options have no underlying price** | Known | Medium | Already excluded NDX from target tickers. Use SPX/SPXW only for index exposure. Revisit if ThetaData adds NDX index data. |
| **PLTR stock data may be limited** | Medium | Low | PLTR is on CTA tape (NYSE). STANDARD tier provides 1-minute data from 2020-09-30. Verify on first download; fall back to EOD if intraday is missing. |
| **Telonex data gap for specific markets** | Medium | Low | Some Polymarket markets may have been created late or have thin data. The market registry's `data_from`/`data_to` columns show actual coverage. Skip markets with < 3 days of data. |

### Infrastructure Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| **Theta Terminal crashes during long download** | Medium | Medium | Manifest enables resume. Wrap long runs in a shell loop that restarts the script on failure. Monitor terminal process with `ps`. |
| **Rate limiting (429) slows downloads** | Medium | Low | Already handled with retry + backoff. If persistent, reduce concurrency from 4 to 3. |
| **Disk space exhaustion** | Low | High | Pre-check disk space before each day's download. Alert if < 10 GB free. Consider external SSD for large datasets. |
| **Network interruption** | Low | Low | Manifest resume handles this. Telonex SDK has built-in retry. ThetaData retry logic in `fetch()`. |

### Data Quality Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| **Tick quote timestamps have gaps during trading hours** | Medium | Medium | Gaps < 5 min are normal (contract may be illiquid). Gaps > 30 min during RTH are concerning -- flag in validation. Forward-fill logic in DataProvider handles gaps gracefully. |
| **EOD Greeks and tick quotes disagree on underlying_price** | Low | Low | EOD Greeks use 17:15 ET snapshot; tick quotes use real-time midpoint. Small differences are expected. Flag if > 1% divergence for spot-check. |
| **Telonex book snapshots have string-typed price columns** | Known | Low | The [[Telonex-Data-Quality-Report]] confirms all 100 price/size columns are strings. Post-download ETL must cast to float64. Handle null/empty strings gracefully. |
| **Schema changes between ThetaData versions** | Low | High | Pin Theta Terminal version. Validate schema against expected columns before processing. If schema changes, the validator catches it immediately. |

### Cost Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| **ThetaData subscription lapses** | Low | High | Keep subscription active. Data once downloaded is permanent (Parquet files). Prioritize downloading everything within the current billing cycle. |
| **Telonex changes pricing or limits** | Low | Medium | Download aggressively while on Plus plan. The Plus plan ($79/mo) is unlimited downloads -- no per-file cost risk. |
| **3-month stretch target exceeds disk budget** | Medium | Medium | Start with 1-month. Only expand to 3 months if 1-month results are promising and disk space allows. Consider compressing older data with higher zstd level (slower reads but smaller files). |

---

## Appendix: Key References

- [[ThetaData-Options-API]] -- Full endpoint reference, parameters, response schemas, tier access
- [[ThetaData-Stock-Index-Data]] -- Stock/index endpoints, tape coverage, SPX specifics
- [[Telonex-Data-Platform]] -- Telonex API, SDK, channels, schemas, pricing
- [[Data-Alignment-Architecture]] -- Storage layout spec, Parquet configuration, DataProvider interface
- [[Telonex-Data-Quality-Report]] -- NVDA POC data quality findings (update frequency, gaps, spread, depth)
- Existing script: `Code/scripts/download_options.py` -- Current EOD + intraday IV downloader
- Existing script: `Code/scripts/discover_markets.py` -- Market discovery and classification
