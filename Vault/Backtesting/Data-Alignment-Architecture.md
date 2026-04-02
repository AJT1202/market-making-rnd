---
title: "Data Alignment Architecture"
created: 2026-04-02
tags: [architecture, data-alignment, backtesting, dataprovider, parquet, tick-level]
status: specification
related:
  - "[[Engine-Architecture-Plan]]"
  - "[[Backtesting-Architecture]]"
  - "[[Orderbook-Backtesting-with-Telonex]]"
  - "[[Fill-Simulation-Research]]"
  - "[[ThetaData-Options-API]]"
  - "[[Polymarket-Data-API]]"
  - "[[Telonex-Data-Platform]]"
---

# Data Alignment Architecture

> **Purpose**: Specification for the data alignment layer that unifies three independent data sources (Polymarket L2 orderbook, Polymarket trades, ThetaData options) into a single, time-consistent interface for the backtesting engine. Enforces no-lookahead guarantees and provides efficient binary-search access across heterogeneous Parquet stores.
>
> **Audience**: Implementation team. All interfaces, schemas, algorithms, and performance constraints are specified for direct implementation.
>
> **Lineage**: Extends [[Engine-Architecture-Plan]] Layer 1 (Data Ingestion) with a formal `DataProvider` abstraction. Builds on the `DataLoader`/`DataStore` pattern in `backtesting-engine/bt_engine/data/` and the Telonex integration plan in [[Orderbook-Backtesting-with-Telonex]].

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Data Storage Layout](#2-data-storage-layout)
3. [Download Pipeline](#3-download-pipeline)
4. [Polymarket Event Stream Construction](#4-polymarket-event-stream-construction)
5. [DataProvider Interface](#5-dataprovider-interface)
6. [Time Alignment Strategy](#6-time-alignment-strategy)
7. [No-Lookahead Enforcement](#7-no-lookahead-enforcement)
8. [Indexing and Performance](#8-indexing-and-performance)
9. [Concrete Walkthrough](#9-concrete-walkthrough)
10. [Data Quality Checks](#10-data-quality-checks)
11. [Integration Points](#11-integration-points)

---

## 1. System Overview

Three independent data sources feed the backtesting engine through a unified `DataProvider` that maintains a monotonically advancing time cursor. At simulation time `t`, every query returns data with timestamps <= `t` only -- no lookahead is possible by construction.

```
                          RAW SOURCES
          +------------------+-------------------+------------------+
          |                  |                   |                  |
    Telonex API        Telonex API         ThetaData Terminal
    (book_snapshot_full)   (trades)         (tick NBBO, EOD Greeks, OI)
          |                  |                   |
          v                  v                   v
     +---------+       +---------+         +-----------+
     | Download |       | Download |         | Download  |
     | SDK      |       | SDK      |         | Script    |
     +---------+       +---------+         +-----------+
          |                  |                   |
          v                  v                   v
   +------+------------------+-------------------+------+
   |                  PARQUET STORE                      |
   |                                                     |
   |  telonex/                  thetadata/               |
   |    book_raw/                 tick_quotes/            |
   |      {slug}/{date}.parquet     {date}/{tkr}_{exp}   |
   |    trades_raw/               eod/                   |
   |      {slug}/{date}.parquet     {date}/greeks.parq   |
   |    events/                     {date}/oi.parquet    |
   |      {slug}/{date}.parquet                          |
   |                                                     |
   |  aligned/                                           |
   |    market_registry.parquet                          |
   +-----------------------------------------------------+
                          |
                          v
              +------------------------+
              |     DataProvider        |
              |  - time cursor (t)     |
              |  - binary search index |
              |  - forward-fill cache  |
              |  - no-lookahead guard  |
              +------------------------+
                          |
                          v
              +------------------------+
              |  Backtester Event Loop  |
              |  (bt_engine/engine/)    |
              +------------------------+
                          |
                          v
              +------------------------+
              |       Strategy          |
              |  (fair value, quotes)   |
              +------------------------+
```

### Data Flow Summary

| Stage | Input | Output | Frequency |
|-------|-------|--------|-----------|
| **Download** | API calls | Raw Parquet files | Once per day (batch) |
| **ETL / Merge** | Raw book + trades | Event stream Parquet | Once per day (batch) |
| **DataProvider init** | Parquet directory | In-memory indexes | Once per backtest run |
| **DataProvider query** | Time cursor `t` + key | Data as-of `t` | Per event in simulation |

### Design Principles

1. **Separation of storage and access**: Data is stored in source-native Parquet files. The `DataProvider` is a read-only access layer -- it never mutates the store.
2. **Binary search, not sequential scan**: All timestamp lookups use `np.searchsorted` on pre-sorted arrays for O(log n) access.
3. **Lazy loading**: Only load data for tickers, expiries, and markets actually queried by the strategy. A full day of tick data for 11 tickers is 2-4 GB; lazy loading keeps working memory under control.
4. **Forward-fill semantics**: "Latest as of `t`" means the most recent update with timestamp <= `t`. This is the universal semantic for all data sources.
5. **Source timestamps are authoritative**: We never re-timestamp data. Telonex microsecond timestamps and ThetaData millisecond timestamps are preserved as-is, normalized to a common timezone.

---

## 2. Data Storage Layout

All data lives under a configurable `data/` root. Directories are organized by source, then by logical entity, then partitioned by date.

```
data/
  thetadata/
    tick_quotes/
      {YYYY-MM-DD}/
        {TICKER}_{EXPIRY_YYYYMMDD}.parquet     # Tick-level NBBO per option contract
        # Example: NVDA_20260417.parquet
        #          AAPL_20260410.parquet
        #          SPX_20260403.parquet
    eod/
      {YYYY-MM-DD}/
        greeks_{YYYY-MM-DD}.parquet             # EOD Greeks for all tickers
        oi_{YYYY-MM-DD}.parquet                 # Open interest for all tickers
  telonex/
    events/
      {market_slug}/
        {YYYY-MM-DD}_events.parquet             # Merged book + trade event stream
        # Example: will-nvidia-nvda-close-above-165-on-march-30-2026/
        #            2026-03-30_events.parquet
    book_raw/
      {market_slug}/
        {YYYY-MM-DD}_book.parquet               # Raw book_snapshot_full
    trades_raw/
      {market_slug}/
        {YYYY-MM-DD}_trades.parquet             # Raw trades
  aligned/
    market_registry.parquet                     # Maps markets to tickers, strikes, expiries
```

### Market Registry Schema

The `market_registry.parquet` maps Polymarket markets to their financial parameters. This is built from the Telonex markets dataset + manual annotation.

| Column | Type | Description |
|--------|------|-------------|
| `market_slug` | string | Telonex/Polymarket slug |
| `ticker` | string | Underlying ticker (NVDA, AAPL, SPX, ...) |
| `strike` | float | Strike price |
| `expiry` | date | Market expiry/resolution date |
| `asset_id_yes` | string | YES token ID (from Telonex markets dataset) |
| `asset_id_no` | string | NO token ID |
| `market_id` | string | Polymarket condition ID |
| `event_slug` | string | Parent event slug |
| `resolution` | string | `YES`, `NO`, or `null` (unresolved) |
| `data_from` | date | First date with book_snapshot_full data |
| `data_to` | date | Last date with book_snapshot_full data |

### Parquet Configuration

All Parquet files use these settings for optimal read performance:

- **Row group size**: 128 MB (enables predicate pushdown on timestamp)
- **Compression**: Snappy (fast decompression, ~2:1 ratio)
- **Sorting**: Every file is sorted by timestamp (ascending) -- this is critical for binary search
- **Timestamp column**: Always named `timestamp_us` (int64, microseconds since epoch, UTC)

### ThetaData Tick Quotes Schema

Each `tick_quotes/{date}/{TICKER}_{EXPIRY}.parquet` file:

| Column | Type | Description |
|--------|------|-------------|
| `timestamp_us` | int64 | Microseconds since epoch (UTC) |
| `symbol` | string | OPRA symbol |
| `strike` | int64 | Strike price in thousandths (e.g., 165000 = $165) |
| `right` | string | `C` or `P` |
| `expiration` | string | `YYYY-MM-DD` |
| `bid` | float64 | NBBO bid price |
| `bid_size` | int64 | Bid size in contracts |
| `ask` | float64 | NBBO ask price |
| `ask_size` | int64 | Ask size in contracts |
| `underlying_price` | float64 | Underlying spot at quote time |
| `iv` | float64 | Implied volatility (if provided by ThetaData) |

### Telonex Event Stream Schema

Each `events/{slug}/{date}_events.parquet` file (merged book + trades):

| Column | Type | Description |
|--------|------|-------------|
| `timestamp_us` | int64 | Microseconds since epoch (UTC) |
| `event_type` | string | `BOOK_UPDATE` or `TRADE` |
| `token_side` | string | `YES` or `NO` |
| `bid_price_0` .. `bid_price_N` | float64 | Book bid levels (null for TRADE events) |
| `bid_size_0` .. `bid_size_N` | float64 | Book bid sizes |
| `ask_price_0` .. `ask_price_N` | float64 | Book ask levels |
| `ask_size_0` .. `ask_size_N` | float64 | Book ask sizes |
| `trade_price` | float64 | Trade price (null for BOOK_UPDATE) |
| `trade_size` | float64 | Trade size (null for BOOK_UPDATE) |
| `trade_taker_side` | string | `buy` or `sell` (null for BOOK_UPDATE) |

---

## 3. Download Pipeline

### 3.1 ThetaData Tick-Level Quotes

Extend the existing `scripts/download_options.py` with a new `tick-quotes` command that downloads tick-level NBBO data for all options contracts within our filtering criteria.

**New command**:

```bash
# Download tick-level NBBO for a single date
python scripts/download_options.py tick-quotes --date 2026-04-01

# Download for a date range
python scripts/download_options.py tick-quotes --start 2026-03-24 --end 2026-04-01

# Single ticker
python scripts/download_options.py tick-quotes --ticker NVDA --date 2026-04-01
```

**Implementation outline**:

```python
async def download_tick_quotes(
    dt: str,
    tickers: list[str] | None = None,
    max_dte: int = 30,
    atm_pct: float = 0.20,
) -> None:
    """Download tick-level NBBO for all filtered options on a date.

    Filtering:
    1. Query expirations for each ticker, keep expiry within max_dte
    2. Query strikes for each ticker/expiry, keep within atm_pct of current
    3. Download tick NBBO for each (ticker, expiry) pair
    4. Save as data/thetadata/tick_quotes/{date}/{ticker}_{expiry}.parquet
    """
    tickers = tickers or ALL_TICKERS
    target_date = datetime.strptime(dt, "%Y-%m-%d").date()

    # Step 1: Discover relevant expiries per ticker
    ticker_expiries = await _get_relevant_expiries(tickers, target_date, max_dte)

    # Step 2: Get ATM reference prices and filter strikes
    ticker_strikes = await _get_filtered_strikes(
        ticker_expiries, target_date, atm_pct
    )

    # Step 3: Download tick NBBO per (ticker, expiry) -- all strikes included
    # ThetaData returns all strikes for a given root+expiry in one request
    sem = asyncio.Semaphore(CONCURRENCY)  # 4 parallel for STANDARD tier
    async with httpx.AsyncClient(timeout=180.0) as client:
        tasks = []
        for ticker, expiries in ticker_expiries.items():
            for exp in expiries:
                tasks.append(
                    _download_one_tick_nbbo(client, sem, ticker, exp, dt)
                )
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Step 4: Save to Parquet, one file per (ticker, expiry)
    out_dir = DATA_DIR / "tick_quotes" / dt
    out_dir.mkdir(parents=True, exist_ok=True)
    for (ticker, exp), df in results:
        if df is not None and not df.is_empty():
            # Filter to relevant strikes
            relevant = ticker_strikes.get((ticker, exp), set())
            if relevant:
                df = df.filter(pl.col("strike").is_in(relevant))
            path = out_dir / f"{ticker}_{exp.replace('-', '')}.parquet"
            df.write_parquet(path, compression="snappy")
```

**ThetaData endpoint**: `GET /v3/option/history/quote/tick`

Parameters:
- `symbol`: Root ticker (e.g., `NVDA`)
- `expiration`: `YYYYMMDD`
- `strike`: `*` (all strikes, filter client-side)
- `right`: `both`
- `start_date` / `end_date`: same date for single-day download
- `format`: `ndjson`

**Smart filtering**:

| Filter | Criterion | Rationale |
|--------|-----------|-----------|
| Expiry | DTE <= 30 | Our B-L pipeline uses near-term options; far-dated have low gamma and don't price binaries well |
| Strike | Within 20% of ATM | Deep OTM/ITM have wide spreads and stale quotes; useless for probability extraction |
| Right | Both calls and puts | Need both sides for put-call parity cross-checks and full B-L integration |

**Concurrency and rate limiting**:
- STANDARD tier: 4 concurrent requests max (`asyncio.Semaphore(4)`)
- Retry on 429 with `Retry-After` header value
- Retry on 472 (NO_DATA) is not retried -- skip silently
- Exponential backoff on timeout: 2s, then 3s with extended timeout (180s)
- Same pattern as existing `fetch()` in `download_options.py`

**Estimated download time per day**:

| Item | Count | Rows/File (est.) | Size/File (est.) | Total |
|------|-------|-------------------|------------------|-------|
| Tickers | 11 (9 stock + SPX + SPXW) | -- | -- | -- |
| Expiries per ticker | ~4-6 (within 30 DTE) | -- | -- | -- |
| Unique (ticker, expiry) pairs | ~55 | -- | -- | -- |
| Strikes per pair (after ATM filter) | ~20-50 | -- | -- | -- |
| Tick quotes per pair per day | ~50K-200K | 100K avg | ~15 MB | ~800 MB |
| **Total per day** | -- | ~5.5M rows | -- | **~5-8 GB** |
| Download time (4 parallel, ~2s each) | 55 requests | -- | -- | **~30 seconds** |

### 3.2 Telonex Data Acquisition

Telonex data is downloaded using the Telonex Python SDK (`pip install telonex[dataframe]`).

**Script**: `scripts/download_telonex.py` (new file)

```python
from telonex import download
import polars as pl

def download_market_day(
    api_key: str,
    market_slug: str,
    outcome: str,       # "Yes" or "No"
    date: str,          # "YYYY-MM-DD"
    data_dir: Path,
) -> dict[str, Path]:
    """Download book_snapshot_full + trades for one token on one date.

    Returns dict of channel -> local file path.
    """
    files = {}
    for channel in ("book_snapshot_full", "trades"):
        downloaded = download(
            api_key=api_key,
            exchange="polymarket",
            channel=channel,
            slug=market_slug,
            outcome=outcome,
            from_date=date,
            to_date=date,
            download_dir=str(data_dir / "telonex" / f"{channel}_raw"),
            concurrency=5,
            force_download=False,
        )
        if downloaded:
            files[channel] = Path(downloaded[0])
    return files
```

**Download scope per day**: For a 5-strike event on one underlying:
- 5 strikes x 2 tokens (YES + NO) x 2 channels (book + trades) = **20 downloads**
- Typical size: book_snapshot_full = 5-20 MB/market/day, trades = 1-5 MB/market/day
- Total: ~60-250 MB per underlying per day
- With Telonex Plus ($79/month): unlimited downloads

**After download**: Run the ETL merge step (Section 4) to produce the event stream Parquet.

### 3.3 EOD Greeks and Open Interest

Already implemented in `scripts/download_options.py` via the `eod` command. No changes needed -- the existing pipeline produces:
- `data/thetadata/eod/{date}/greeks_{date}.parquet`
- `data/thetadata/eod/{date}/oi_{date}.parquet`

**Publication delays** (affects availability, not download logic):
- EOD Greeks: available after ~17:15 ET on the trading day
- Open Interest: available after ~06:30 ET the following morning, represents previous day's close

---

## 4. Polymarket Event Stream Construction

The event stream merges `book_snapshot_full` and `trades` into a single chronologically-sorted stream per market per day. This mirrors the `TimelineEvent` pattern in `bt_engine/data/schema.py` but serialized to Parquet for persistence.

### 4.1 Merge Algorithm

```python
import polars as pl
from pathlib import Path


def build_event_stream(
    book_path: Path,
    trades_path: Path,
    output_path: Path,
    token_side: str,   # "YES" or "NO"
) -> pl.DataFrame:
    """Merge book snapshots + trades into a unified event stream.

    Both inputs must be sorted by timestamp_us (Telonex guarantees this).
    Output is sorted by (timestamp_us, event_type_priority).

    Event type priority: BOOK_UPDATE=0, TRADE=1
    (book state is updated before trades at the same timestamp)
    """
    # --- Load book snapshots ---
    book_df = pl.read_parquet(book_path)
    book_df = book_df.with_columns([
        pl.lit("BOOK_UPDATE").alias("event_type"),
        pl.lit(token_side).alias("token_side"),
        pl.lit(0).alias("_priority"),
        # Null trade columns
        pl.lit(None).cast(pl.Float64).alias("trade_price"),
        pl.lit(None).cast(pl.Float64).alias("trade_size"),
        pl.lit(None).cast(pl.String).alias("trade_taker_side"),
    ])

    # --- Load trades ---
    trades_df = pl.read_parquet(trades_path)
    trades_df = trades_df.with_columns([
        pl.lit("TRADE").alias("event_type"),
        pl.lit(token_side).alias("token_side"),
        pl.lit(1).alias("_priority"),
    ])
    # Rename trade columns to match schema
    trades_df = trades_df.rename({
        "price": "trade_price",
        "size": "trade_size",
        "side": "trade_taker_side",
    })
    # Add null book columns (all bid_price_N, bid_size_N, etc.)
    book_cols = [c for c in book_df.columns
                 if c.startswith(("bid_", "ask_")) and c not in trades_df.columns]
    for col in book_cols:
        trades_df = trades_df.with_columns(
            pl.lit(None).cast(pl.Float64).alias(col)
        )

    # --- Merge and sort ---
    # Use diagonal_relaxed to handle column mismatches gracefully
    merged = pl.concat([book_df, trades_df], how="diagonal_relaxed")
    merged = merged.sort(["timestamp_us", "_priority"])
    merged = merged.drop("_priority")

    # --- Write ---
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.write_parquet(output_path, compression="snappy")

    return merged
```

### 4.2 Edge Cases

| Edge Case | Resolution |
|-----------|------------|
| **Simultaneous timestamps** (book + trade at same `timestamp_us`) | Book update processed first (priority 0 < 1). The book state reflects the pre-trade state, then the trade fires against it. This matches the BTC engine's "external before internal" principle. |
| **Trades between snapshots** (trade at T, but nearest snapshots are T-2s and T+1s) | Normal and expected. The trade event carries its own data (price, size, side). The book state used for queue position is the most recent BOOK_UPDATE with timestamp <= trade timestamp. |
| **Duplicate timestamps** (two book snapshots at the same microsecond) | Rare but possible if Telonex captures two WebSocket messages in the same microsecond. Keep both -- the later one in file order is the more recent state. The `_priority` + stable sort preserves file order within same timestamp. |
| **Missing trades file** (market had no trades on a given day) | Valid scenario for illiquid markets. Event stream contains only BOOK_UPDATE events. Fill simulation correctly produces zero fills. |
| **Crossed book** (best_bid >= best_ask in a snapshot) | Flag in data quality checks (Section 10) but do not discard. Crossed books occur briefly during Polymarket's matching engine latency and are informative for the strategy. |

### 4.3 Per-Market Stream Construction

For a dual-book market (YES + NO tokens), we build **two separate event streams** per day:

```
events/will-nvidia-nvda-close-above-165-on-march-30-2026/
  2026-03-30_events_YES.parquet
  2026-03-30_events_NO.parquet
```

Or alternatively, a single merged file with the `token_side` column distinguishing events. The single-file approach is preferred for simpler loading:

```
events/will-nvidia-nvda-close-above-165-on-march-30-2026/
  2026-03-30_events.parquet    # Contains both YES and NO events, sorted by timestamp
```

This aligns with the existing `bt_engine/data/loader.py` pattern where the `DataLoader` iterates over `market.token_side_available` and loads data for each token side.

---

## 5. DataProvider Interface

The `DataProvider` is the single point of access for all historical data during a backtest. It maintains a time cursor and guarantees no-lookahead.

### 5.1 Return Type Dataclasses

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """Polymarket L2 orderbook state at a point in time.

    Prices are in [0.0, 1.0] (Polymarket binary token prices).
    Sizes are in shares (float, Polymarket's native unit).
    """
    timestamp_us: int
    token_id: str
    token_side: str                     # "YES" or "NO"
    bids: list[tuple[float, float]]     # [(price, size), ...] descending by price
    asks: list[tuple[float, float]]     # [(price, size), ...] ascending by price

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 1.0

    @property
    def mid(self) -> float:
        bb, ba = self.best_bid, self.best_ask
        if bb > 0 and ba < 1:
            return (bb + ba) / 2.0
        return 0.5

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def is_valid(self) -> bool:
        return bool(self.bids) and bool(self.asks) and self.best_ask > self.best_bid


@dataclass(frozen=True, slots=True)
class Trade:
    """A single Polymarket trade."""
    timestamp_us: int
    token_id: str
    token_side: str         # "YES" or "NO"
    price: float
    size: float
    taker_side: str         # "buy" or "sell"


@dataclass(frozen=True, slots=True)
class OptionsChain:
    """NBBO options chain for a single underlying + expiry, as-of a point in time.

    Contains the most recent tick quote for each (strike, right) combination.
    """
    timestamp_us: int       # Timestamp of the most recent quote in the chain
    ticker: str
    expiry: date
    underlying_price: float
    quotes: list[OptionQuote]

    def calls(self) -> list[OptionQuote]:
        return [q for q in self.quotes if q.right == "C"]

    def puts(self) -> list[OptionQuote]:
        return [q for q in self.quotes if q.right == "P"]

    def get(self, strike: float, right: str) -> OptionQuote | None:
        for q in self.quotes:
            if abs(q.strike - strike) < 0.005 and q.right == right:
                return q
        return None


@dataclass(frozen=True, slots=True)
class OptionQuote:
    """Single option contract NBBO quote."""
    timestamp_us: int
    symbol: str             # OPRA symbol
    strike: float
    right: str              # "C" or "P"
    expiration: date
    bid: float
    bid_size: int
    ask: float
    ask_size: int
    underlying_price: float
    iv: float | None        # Implied vol, may be None if not provided


@dataclass(frozen=True, slots=True)
class IVQuote:
    """Implied volatility snapshot for a single contract."""
    timestamp_us: int
    ticker: str
    strike: float
    right: str
    expiry: date
    iv: float
    underlying_price: float
    bid: float
    ask: float
    mid: float
```

### 5.2 DataProvider Class

```python
from pathlib import Path
from datetime import date, datetime
from functools import lru_cache

import numpy as np
import polars as pl


class DataProvider:
    """Unified data access with time-cursor enforcement.

    All queries return data as-of the current time cursor. The cursor
    is advanced by the backtester event loop; it never moves backward.

    Internally, each data source is loaded lazily into sorted numpy
    timestamp arrays. Lookups use np.searchsorted for O(log n) access.
    """

    def __init__(self, data_dir: Path, market_date: date):
        self._data_dir = data_dir
        self._market_date = market_date
        self._cursor_us: int = 0           # Current simulation time
        self._max_cursor_us: int = 0       # High-water mark (for monotonicity check)

        # Lazy-loaded data stores
        # Key: (market_slug, token_side) -> (timestamps_us: np.ndarray, dataframe: pl.DataFrame)
        self._book_cache: dict[tuple[str, str], tuple[np.ndarray, pl.DataFrame]] = {}
        self._trade_cache: dict[tuple[str, str], tuple[np.ndarray, pl.DataFrame]] = {}

        # Key: (ticker, expiry_str) -> (timestamps_us: np.ndarray, dataframe: pl.DataFrame)
        self._options_cache: dict[tuple[str, str], tuple[np.ndarray, pl.DataFrame]] = {}

        # Key: ticker -> (timestamps_us: np.ndarray, prices: np.ndarray)
        self._underlying_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}

        # EOD data (loaded eagerly since it is small)
        self._greeks_df: pl.DataFrame | None = None
        self._oi_df: pl.DataFrame | None = None

        # Market registry
        self._registry: pl.DataFrame | None = None

    # ------------------------------------------------------------------
    # Time cursor
    # ------------------------------------------------------------------

    def advance_time(self, t: datetime) -> None:
        """Advance the simulation clock. t must be >= current cursor.

        Args:
            t: New simulation time (timezone-aware, will be converted to
               microseconds since epoch internally).

        Raises:
            ValueError: If t < current cursor (time cannot move backward).
        """
        t_us = int(t.timestamp() * 1_000_000)
        if t_us < self._cursor_us:
            raise ValueError(
                f"Time cannot move backward: {t_us} < {self._cursor_us}"
            )
        self._cursor_us = t_us
        self._max_cursor_us = max(self._max_cursor_us, t_us)

    def advance_time_us(self, t_us: int) -> None:
        """Advance the simulation clock using raw microseconds."""
        if t_us < self._cursor_us:
            raise ValueError(
                f"Time cannot move backward: {t_us} < {self._cursor_us}"
            )
        self._cursor_us = t_us
        self._max_cursor_us = max(self._max_cursor_us, t_us)

    @property
    def current_time_us(self) -> int:
        return self._cursor_us

    # ------------------------------------------------------------------
    # Polymarket: Orderbook
    # ------------------------------------------------------------------

    def book(self, token_id: str) -> BookSnapshot | None:
        """Latest L2 book snapshot as-of current time.

        Returns the most recent BOOK_UPDATE event with timestamp_us <= cursor.
        Returns None if no data is available yet.
        """
        slug, token_side = self._resolve_token(token_id)
        ts_arr, df = self._load_books(slug, token_side)
        idx = np.searchsorted(ts_arr, self._cursor_us, side="right") - 1
        if idx < 0:
            return None
        row = df.row(idx, named=True)
        return self._row_to_book_snapshot(row, token_id, token_side)

    def midpoint(self, token_id: str) -> float | None:
        """Convenience: return book midpoint as-of current time."""
        snap = self.book(token_id)
        if snap is None or not snap.is_valid:
            return None
        return snap.mid

    # ------------------------------------------------------------------
    # Polymarket: Trades
    # ------------------------------------------------------------------

    def trades(self, token_id: str, since: datetime) -> list[Trade]:
        """All trades in (since, current_time] for a token.

        Args:
            token_id: Polymarket token ID.
            since: Start of window (exclusive). Must be <= current_time.

        Returns:
            List of Trade objects sorted by timestamp.
        """
        slug, token_side = self._resolve_token(token_id)
        ts_arr, df = self._load_trades(slug, token_side)
        since_us = int(since.timestamp() * 1_000_000)

        # Binary search for the range [since_us+1, cursor_us]
        lo = np.searchsorted(ts_arr, since_us, side="right")
        hi = np.searchsorted(ts_arr, self._cursor_us, side="right")

        if lo >= hi:
            return []

        result = []
        for i in range(lo, hi):
            row = df.row(i, named=True)
            result.append(Trade(
                timestamp_us=row["timestamp_us"],
                token_id=token_id,
                token_side=token_side,
                price=row["trade_price"],
                size=row["trade_size"],
                taker_side=row["trade_taker_side"],
            ))
        return result

    # ------------------------------------------------------------------
    # Options: Chain
    # ------------------------------------------------------------------

    def options_chain(self, ticker: str, expiry: date) -> OptionsChain | None:
        """Full NBBO options chain as-of current time.

        Returns the most recent tick quote for each (strike, right)
        combination where the quote timestamp <= cursor.

        Returns None if no data is available for this ticker/expiry.
        """
        exp_str = expiry.strftime("%Y%m%d")
        ts_arr, df = self._load_options(ticker, exp_str)
        if len(ts_arr) == 0:
            return None

        # Find all rows with timestamp_us <= cursor
        hi = np.searchsorted(ts_arr, self._cursor_us, side="right")
        if hi == 0:
            return None

        # Get the subset of data up to the cursor
        subset = df.slice(0, hi)

        # For each (strike, right), take the last row (most recent quote)
        latest = subset.group_by(["strike", "right"]).last()

        if latest.is_empty():
            return None

        quotes = []
        underlying_price = 0.0
        max_ts = 0
        for row in latest.iter_rows(named=True):
            q = OptionQuote(
                timestamp_us=row["timestamp_us"],
                symbol=row.get("symbol", ""),
                strike=row["strike"] / 1000.0 if row["strike"] > 1000 else row["strike"],
                right=row["right"],
                expiration=expiry,
                bid=row["bid"],
                bid_size=int(row.get("bid_size", 0)),
                ask=row["ask"],
                ask_size=int(row.get("ask_size", 0)),
                underlying_price=row.get("underlying_price", 0.0),
                iv=row.get("iv"),
            )
            quotes.append(q)
            if row.get("underlying_price", 0.0) > 0:
                underlying_price = row["underlying_price"]
            max_ts = max(max_ts, row["timestamp_us"])

        return OptionsChain(
            timestamp_us=max_ts,
            ticker=ticker,
            expiry=expiry,
            underlying_price=underlying_price,
            quotes=quotes,
        )

    # ------------------------------------------------------------------
    # Options: Single IV lookup
    # ------------------------------------------------------------------

    def implied_vol(
        self, ticker: str, strike: float, right: str, expiry: date
    ) -> IVQuote | None:
        """Implied volatility for a single contract as-of current time.

        More efficient than loading the full chain when you need one contract.
        """
        chain = self.options_chain(ticker, expiry)
        if chain is None:
            return None
        q = chain.get(strike, right)
        if q is None or q.iv is None:
            return None
        return IVQuote(
            timestamp_us=q.timestamp_us,
            ticker=ticker,
            strike=q.strike,
            right=right,
            expiry=expiry,
            iv=q.iv,
            underlying_price=q.underlying_price,
            bid=q.bid,
            ask=q.ask,
            mid=(q.bid + q.ask) / 2.0,
        )

    # ------------------------------------------------------------------
    # EOD Data
    # ------------------------------------------------------------------

    def greeks_eod(self, ticker: str) -> pd.DataFrame:
        """Previous day's EOD Greeks for a ticker.

        Returns a pandas DataFrame with columns: symbol, strike, right,
        expiration, delta, gamma, theta, vega, rho, iv, underlying_price.

        Availability: only if current_time > previous_close + 17:15 ET.
        Returns empty DataFrame if not yet available.
        """
        self._ensure_eod_loaded()
        if self._greeks_df is None or self._greeks_df.is_empty():
            return pd.DataFrame()

        # Check availability: EOD data for date D is available after D 17:15 ET
        # The file we loaded is for the previous trading day
        prev_date = self._previous_trading_date(self._market_date)
        avail_us = self._et_to_us(prev_date, 17, 15)
        if self._cursor_us < avail_us:
            return pd.DataFrame()

        filtered = self._greeks_df.filter(
            pl.col("symbol").str.starts_with(ticker)
            | (pl.col("root") == ticker)
        )
        return filtered.to_pandas()

    def open_interest(self, ticker: str) -> pd.DataFrame:
        """Previous day's open interest for a ticker.

        Availability: only if current_time > market_date 06:30 ET.
        Returns empty DataFrame if not yet available.
        """
        self._ensure_eod_loaded()
        if self._oi_df is None or self._oi_df.is_empty():
            return pd.DataFrame()

        # OI is published ~06:30 ET on the current date, represents previous day
        avail_us = self._et_to_us(self._market_date, 6, 30)
        if self._cursor_us < avail_us:
            return pd.DataFrame()

        filtered = self._oi_df.filter(
            pl.col("symbol").str.starts_with(ticker)
            | (pl.col("root") == ticker)
        )
        return filtered.to_pandas()

    # ------------------------------------------------------------------
    # Underlying Price
    # ------------------------------------------------------------------

    def underlying_price(self, ticker: str) -> float | None:
        """Latest underlying stock/index price as-of current time.

        Derived from the `underlying_price` field in tick-level options
        quotes. This is the NBBO midpoint for the underlying reported
        alongside each option quote, so it updates at tick frequency
        during market hours.

        Returns None if no options data has been loaded for this ticker.
        """
        # Aggregate underlying prices across all loaded expiries for this ticker
        best_ts = -1
        best_price = None
        for (tkr, exp), (ts_arr, df) in self._options_cache.items():
            if tkr != ticker:
                continue
            idx = np.searchsorted(ts_arr, self._cursor_us, side="right") - 1
            if idx < 0:
                continue
            row = df.row(idx, named=True)
            row_ts = row["timestamp_us"]
            if row_ts > best_ts and row.get("underlying_price", 0.0) > 0:
                best_ts = row_ts
                best_price = row["underlying_price"]
        return best_price

    # ------------------------------------------------------------------
    # Internal: Lazy loading
    # ------------------------------------------------------------------

    def _load_books(self, slug: str, token_side: str) -> tuple[np.ndarray, pl.DataFrame]:
        key = (slug, token_side)
        if key not in self._book_cache:
            path = self._find_event_file(slug, token_side, "BOOK_UPDATE")
            df = pl.read_parquet(path)
            df = df.filter(pl.col("event_type") == "BOOK_UPDATE")
            df = df.filter(pl.col("token_side") == token_side)
            df = df.sort("timestamp_us")
            ts = df["timestamp_us"].to_numpy().astype(np.int64)
            self._book_cache[key] = (ts, df)
        return self._book_cache[key]

    def _load_trades(self, slug: str, token_side: str) -> tuple[np.ndarray, pl.DataFrame]:
        key = (slug, token_side)
        if key not in self._trade_cache:
            path = self._find_event_file(slug, token_side, "TRADE")
            df = pl.read_parquet(path)
            df = df.filter(pl.col("event_type") == "TRADE")
            df = df.filter(pl.col("token_side") == token_side)
            df = df.sort("timestamp_us")
            ts = df["timestamp_us"].to_numpy().astype(np.int64)
            self._trade_cache[key] = (ts, df)
        return self._trade_cache[key]

    def _load_options(self, ticker: str, exp_str: str) -> tuple[np.ndarray, pl.DataFrame]:
        key = (ticker, exp_str)
        if key not in self._options_cache:
            date_str = self._market_date.strftime("%Y-%m-%d")
            path = self._data_dir / "thetadata" / "tick_quotes" / date_str / f"{ticker}_{exp_str}.parquet"
            if not path.exists():
                self._options_cache[key] = (np.array([], dtype=np.int64), pl.DataFrame())
                return self._options_cache[key]
            df = pl.read_parquet(path)
            df = df.sort("timestamp_us")
            ts = df["timestamp_us"].to_numpy().astype(np.int64)
            self._options_cache[key] = (ts, df)
        return self._options_cache[key]

    def _ensure_eod_loaded(self) -> None:
        if self._greeks_df is not None:
            return
        prev = self._previous_trading_date(self._market_date)
        prev_str = prev.strftime("%Y-%m-%d")
        greeks_path = self._data_dir / "thetadata" / "eod" / prev_str / f"greeks_{prev_str}.parquet"
        oi_path = self._data_dir / "thetadata" / "eod" / prev_str / f"oi_{prev_str}.parquet"
        self._greeks_df = pl.read_parquet(greeks_path) if greeks_path.exists() else pl.DataFrame()
        self._oi_df = pl.read_parquet(oi_path) if oi_path.exists() else pl.DataFrame()

    def _find_event_file(self, slug: str, token_side: str, event_type: str) -> Path:
        date_str = self._market_date.strftime("%Y-%m-%d")
        # Try merged events file first
        merged = self._data_dir / "telonex" / "events" / slug / f"{date_str}_events.parquet"
        if merged.exists():
            return merged
        # Fall back to raw files
        if event_type == "BOOK_UPDATE":
            return self._data_dir / "telonex" / "book_raw" / slug / f"{date_str}_book.parquet"
        else:
            return self._data_dir / "telonex" / "trades_raw" / slug / f"{date_str}_trades.parquet"

    def _resolve_token(self, token_id: str) -> tuple[str, str]:
        """Resolve a token_id to (market_slug, token_side) using the registry."""
        if self._registry is None:
            reg_path = self._data_dir / "aligned" / "market_registry.parquet"
            self._registry = pl.read_parquet(reg_path)

        # Check YES side
        match = self._registry.filter(pl.col("asset_id_yes") == token_id)
        if not match.is_empty():
            return match.row(0, named=True)["market_slug"], "YES"

        # Check NO side
        match = self._registry.filter(pl.col("asset_id_no") == token_id)
        if not match.is_empty():
            return match.row(0, named=True)["market_slug"], "NO"

        raise KeyError(f"Token ID {token_id} not found in market registry")

    def _row_to_book_snapshot(
        self, row: dict, token_id: str, token_side: str
    ) -> BookSnapshot:
        """Convert a Polars row dict to a BookSnapshot dataclass."""
        bids = []
        asks = []
        for i in range(50):  # Up to 50 levels
            bp = row.get(f"bid_price_{i}")
            bs = row.get(f"bid_size_{i}")
            if bp is not None and bp > 0:
                bids.append((float(bp), float(bs)))
            else:
                break
        for i in range(50):
            ap = row.get(f"ask_price_{i}")
            asf = row.get(f"ask_size_{i}")
            if ap is not None and ap > 0:
                asks.append((float(ap), float(asf)))
            else:
                break
        return BookSnapshot(
            timestamp_us=row["timestamp_us"],
            token_id=token_id,
            token_side=token_side,
            bids=bids,
            asks=asks,
        )

    @staticmethod
    def _previous_trading_date(d: date) -> date:
        """Return the previous US trading date (skip weekends)."""
        from datetime import timedelta
        prev = d - timedelta(days=1)
        while prev.weekday() >= 5:  # Saturday=5, Sunday=6
            prev -= timedelta(days=1)
        return prev

    @staticmethod
    def _et_to_us(d: date, hour: int, minute: int) -> int:
        """Convert a date + ET time to microseconds since epoch."""
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=et)
        return int(dt.timestamp() * 1_000_000)
```

---

## 6. Time Alignment Strategy

### 6.1 The Problem

Three data sources have fundamentally different temporal characteristics:

| Source | Update Frequency | Availability Hours | Timestamp Precision |
|--------|------------------|--------------------|---------------------|
| Polymarket book snapshots | ~0.1-3s per change | 24/7 (most active 9:30-16:00 ET) | Microsecond |
| Polymarket trades | Tick-level (per trade) | 24/7 | Microsecond |
| ThetaData tick NBBO | Tick-level (per quote change) | 9:30-16:00 ET only | Millisecond |
| ThetaData EOD Greeks | Once per day | Available after 17:15 ET | Daily |
| ThetaData Open Interest | Once per day | Available after 06:30 ET next day | Daily |

### 6.2 Timestamp Normalization

**Decision: All timestamps stored and queried as UTC microseconds since epoch (`int64`).**

Rationale:
- Telonex already provides `timestamp_us` in UTC
- ThetaData timestamps are millisecond UTC (multiply by 1000)
- UTC avoids DST ambiguity
- Integer microseconds enable exact `np.searchsorted` without floating-point issues
- ET conversion is only needed for display and market-hours checks

**Conversion functions**:

```python
def ms_to_us(ms: int) -> int:
    """ThetaData milliseconds to our microsecond convention."""
    return ms * 1000

def et_datetime_to_us(dt_str: str, time_str: str) -> int:
    """Convert 'YYYY-MM-DD' + 'HH:MM:SS' ET to UTC microseconds."""
    from zoneinfo import ZoneInfo
    from datetime import datetime
    et = ZoneInfo("America/New_York")
    dt = datetime.strptime(f"{dt_str} {time_str}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=et)
    return int(dt.timestamp() * 1_000_000)
```

### 6.3 Forward-Fill Semantics

Every `DataProvider` query returns the **most recent data with timestamp <= cursor**. This is the "forward-fill" or "as-of" semantic:

```
                  Options NBBO updates    Polymarket book updates
Timeline:  --------Q1----Q2---------Q3--------B1---B2---B3----B4--------->
                                   ^
                                cursor = t

options_chain() returns: data from Q2  (most recent options quote <= t)
book()          returns: data from B3  (most recent book snapshot <= t)
```

This means:
- **During market hours (9:30-16:00 ET)**: Both options and Polymarket data update frequently. Typical staleness is < 1 second for both.
- **Outside market hours**: Options data is stale (last update from 16:00 ET). Polymarket book still updates but with lower frequency. The strategy can detect this via the timestamp on returned data.
- **Pre-market**: OI data becomes available at ~06:30 ET. EOD Greeks from the previous close are available from ~17:15 ET the prior day.

### 6.4 Market Hours Alignment

```
  00:00 ET                06:30 ET        09:30 ET              16:00 ET    17:15 ET    24:00 ET
  |                       |               |                     |           |           |
  |--- Polymarket 24/7 ---|--- Polymarket ---|--- BOTH ACTIVE ---|--- PM ---|--- PM ----|
  |                       |               |                     |           |           |
  | No options data       | OI available  | Options tick NBBO   | Options   | EOD avail |
  | (use prev close EOD)  | from prev day | streaming           | close     |           |
  |                       |               |                     |           |           |
  | Strategy: can still   | Strategy:     | Strategy: full      | Strategy: | Strategy: |
  | quote using stale FV  | OI signals    | B-L pipeline active | use last  | new EOD   |
  |                       |               |                     | tick data | available  |
```

**Key implication for the strategy**: The B-L pipeline should only compute fresh probabilities during options market hours (9:30-16:00 ET). Outside this window, the strategy can either:
1. Use the last computed probability (forward-fill the signal)
2. Widen quotes to account for stale fair value
3. Stop quoting entirely

This is a strategy decision, not a `DataProvider` decision. The `DataProvider` simply returns whatever data is available as-of `t`.

### 6.5 Cross-Source Clock Skew

Telonex and ThetaData capture timestamps from different sources:
- Telonex: WebSocket message receipt time at Telonex's servers
- ThetaData: Exchange-reported quote time from OPRA

Expected skew: < 100ms between an options quote and a Polymarket event at the "same" real-world time. This is negligible for our purposes since:
1. Our strategy operates on ~1-second timescales (not microsecond HFT)
2. The B-L pipeline uses the options chain as a whole, not individual tick timing
3. Latency modeling in the engine (200ms submit latency) dwarfs clock skew

No cross-source timestamp adjustment is applied.

---

## 7. No-Lookahead Enforcement

No-lookahead is the most critical correctness property of the backtester. If any data query returns information from the future, the entire backtest is invalid.

### 7.1 Enforcement Mechanism

The `DataProvider` enforces no-lookahead at two levels:

**Level 1: Cursor filtering** (primary enforcement)

Every query method uses `np.searchsorted(ts_arr, cursor_us, side="right")` to find the boundary index. Only indices `< boundary` are returned. Since timestamp arrays are sorted, this guarantees all returned data has `timestamp_us <= cursor_us`.

**Level 2: Assertion guard** (defense in depth)

```python
def _assert_no_lookahead(self, data_timestamp_us: int, context: str = "") -> None:
    """Raise if a data point has a future timestamp.

    Called on every data point returned to the caller.
    This is a defense-in-depth check -- if this ever fires,
    there is a bug in the binary search logic.
    """
    if data_timestamp_us > self._cursor_us:
        raise LookaheadError(
            f"LOOKAHEAD VIOLATION: data timestamp {data_timestamp_us} > "
            f"cursor {self._cursor_us} (context: {context})"
        )


class LookaheadError(RuntimeError):
    """Raised when a no-lookahead invariant is violated."""
    pass
```

### 7.2 EOD Data Availability Rules

EOD data requires special treatment because it represents end-of-day values but becomes available hours later:

| Data | Represents | Available After | DataProvider Rule |
|------|-----------|-----------------|-------------------|
| EOD Greeks | Closing values on date D | D + 17:15 ET | `greeks_eod()` returns empty if `cursor < D + 17:15 ET` |
| Open Interest | End-of-day OI on date D | D+1 + 06:30 ET | `open_interest()` returns empty if `cursor < D+1 + 06:30 ET` |

**Example**: Backtesting April 2, 2026:
- At 09:30 ET: `greeks_eod("NVDA")` returns April 1 EOD data (available since April 1 17:15 ET)
- At 09:30 ET: `open_interest("NVDA")` returns April 1 OI (available since April 2 06:30 ET)
- At 05:00 ET: `open_interest("NVDA")` returns **empty** (April 2 OI not published yet)

### 7.3 Monotonic Cursor

The cursor can only advance forward:

```python
def advance_time(self, t: datetime) -> None:
    t_us = int(t.timestamp() * 1_000_000)
    if t_us < self._cursor_us:
        raise ValueError(f"Time cannot move backward: {t_us} < {self._cursor_us}")
    self._cursor_us = t_us
```

This prevents a subtle bug: if the cursor could move backward, a strategy could "peek" at future data by advancing, reading, then retreating.

### 7.4 Verification Function

Run this after every backtest to verify no-lookahead was maintained:

```python
def verify_no_lookahead(audit_log: list[dict]) -> bool:
    """Verify that no data point in the audit log has a future timestamp.

    The audit log records every data access: {cursor_us, data_timestamp_us, source}.

    Returns True if all accesses are valid. Raises AssertionError with
    details of the first violation if not.
    """
    for i, entry in enumerate(audit_log):
        cursor = entry["cursor_us"]
        data_ts = entry["data_timestamp_us"]
        source = entry["source"]
        if data_ts > cursor:
            delta_ms = (data_ts - cursor) / 1000
            raise AssertionError(
                f"Lookahead violation at audit entry {i}: "
                f"source={source}, data_ts={data_ts}, cursor={cursor}, "
                f"lookahead={delta_ms:.1f}ms"
            )
    return True
```

To enable auditing, the `DataProvider` can optionally record every access:

```python
class DataProvider:
    def __init__(self, ..., audit: bool = False):
        self._audit = audit
        self._audit_log: list[dict] = []

    def _record_access(self, data_ts_us: int, source: str) -> None:
        if self._audit:
            self._audit_log.append({
                "cursor_us": self._cursor_us,
                "data_timestamp_us": data_ts_us,
                "source": source,
            })
            self._assert_no_lookahead(data_ts_us, source)
```

---

## 8. Indexing and Performance

### 8.1 Binary Search on Sorted Timestamps

The core access pattern is: given a sorted array of timestamps and a target time `t`, find the index of the last element <= `t`.

```python
idx = np.searchsorted(ts_array, t_us, side="right") - 1
```

This is O(log n) and operates on a contiguous `int64` numpy array, which is cache-friendly and fast even for millions of elements.

**Performance**: For a 5M-row tick quote file, `np.searchsorted` completes in ~1 microsecond. This is negligible compared to the data access itself.

### 8.2 Parquet Predicate Pushdown

When loading Parquet files, use row group metadata to skip irrelevant data:

```python
# Polars does this automatically with scan_parquet + filter
df = (
    pl.scan_parquet(path)
    .filter(pl.col("timestamp_us").is_between(start_us, end_us))
    .collect()
)
```

For files sorted by timestamp with 128 MB row groups:
- A 1 GB file has ~8 row groups
- Predicate pushdown on `timestamp_us` skips row groups whose min/max don't overlap the query range
- Typical speedup: 2-4x for partial-day queries

### 8.3 Lazy Loading Strategy

Data is loaded into memory **only when first queried** for a given (source, key) combination:

| Data Source | Cache Key | Loaded When | Memory per Entry |
|-------------|-----------|-------------|------------------|
| Book snapshots | `(slug, token_side)` | First `book()` call for that token | ~50 MB for 30K snapshots with 25 levels |
| Trades | `(slug, token_side)` | First `trades()` call | ~5 MB for 5K trades |
| Tick quotes | `(ticker, expiry)` | First `options_chain()` call for that pair | ~15 MB for 100K quotes |
| EOD Greeks | global | First `greeks_eod()` call | ~2 MB |
| Open Interest | global | First `open_interest()` call | ~1 MB |

### 8.4 Memory Budget

Worst-case memory for a full day of all tickers:

| Component | Count | Memory | Total |
|-----------|-------|--------|-------|
| Polymarket books (5 strikes x 2 tokens) | 10 streams | 50 MB each | 500 MB |
| Polymarket trades (5 strikes x 2 tokens) | 10 streams | 5 MB each | 50 MB |
| Tick quotes (11 tickers x 5 expiries avg) | 55 files | 15 MB each | 825 MB |
| EOD + OI | 2 files | 3 MB total | 3 MB |
| Numpy index arrays | ~75 arrays | 1 MB each | 75 MB |
| **Total** | -- | -- | **~1.5 GB** |

This is well within the 2-4 GB budget. In practice, lazy loading means only actively-used data is resident.

### 8.5 LRU Cache for Repeated Lookups

The `options_chain()` method is expensive because it does a `group_by().last()` on every call. For strategies that call it multiple times at the same or nearby timestamps, we cache the result:

```python
from functools import lru_cache

# Cache the last 16 options chain lookups
# Key: (ticker, expiry_str, cursor_bucket)
# cursor_bucket = cursor_us // 1_000_000 (round to nearest second)
@lru_cache(maxsize=16)
def _cached_options_chain(self, ticker: str, exp_str: str, cursor_bucket: int):
    # ... actual computation ...
    pass
```

The `cursor_bucket` groups nearby timestamps (within 1 second) into the same cache key, since the options chain is unlikely to change within a second.

---

## 9. Concrete Walkthrough

### Scenario: NVDA > $165, March 30, 2026 at 10:30:15.123 AM ET

The strategy is market-making on the "Will NVIDIA close above $165 on March 30?" binary market. The simulation time is 10:30:15.123 AM ET (14:30:15.123000 UTC).

```python
from datetime import datetime, date
from zoneinfo import ZoneInfo

et = ZoneInfo("America/New_York")
t = datetime(2026, 3, 30, 10, 30, 15, 123000, tzinfo=et)

provider.advance_time(t)
# cursor_us = 1774893015123000 (hypothetical)
```

**Step 1: Get Polymarket book state**

```python
nvda_165_yes_token = "21742633143463906290569050155826241533..."  # from registry
snap = provider.book(nvda_165_yes_token)
```

Internally:
1. `_resolve_token()` maps token ID to `("will-nvidia-nvda-close-above-165-on-march-30-2026", "YES")`
2. `_load_books()` lazy-loads the merged events file, filters to `BOOK_UPDATE` + `YES`
3. `np.searchsorted([..., 1774893014800000, 1774893015050000, 1774893015200000, ...], 1774893015123000, side="right")` returns index pointing to the snapshot at 14:30:14.800 UTC

Result: `BookSnapshot` from 10:30:14.800 ET with bids `[(0.38, 500), (0.37, 1200), ...]` and asks `[(0.40, 300), (0.41, 800), ...]`.

**Step 2: Get recent trades**

```python
since = datetime(2026, 3, 30, 10, 30, 10, 0, tzinfo=et)
recent = provider.trades(nvda_165_yes_token, since=since)
# Returns 3 trades in the last 5.123 seconds:
#   Trade(ts=10:30:11.2, price=0.39, size=200, taker_side="buy")
#   Trade(ts=10:30:13.1, price=0.38, size=100, taker_side="sell")
#   Trade(ts=10:30:14.5, price=0.39, size=150, taker_side="buy")
```

**Step 3: Get options chain**

```python
chain = provider.options_chain("NVDA", date(2026, 3, 30))
# Returns OptionsChain with 47 strikes (within 20% of ATM)
# Most recent quote timestamp: 10:30:14.900 ET
# underlying_price: 164.82
```

Internally:
1. `_load_options("NVDA", "20260330")` lazy-loads `data/thetadata/tick_quotes/2026-03-30/NVDA_20260330.parquet`
2. Binary search finds all rows with timestamp <= cursor
3. `group_by(["strike", "right"]).last()` takes the most recent quote per contract
4. 47 contracts are within the pre-filtered ATM range

**Step 4: Get underlying price**

```python
spot = provider.underlying_price("NVDA")
# Returns 164.82 (from the underlying_price field in the most recent options quote)
```

**Step 5: Strategy computes fair value**

```python
# Breeden-Litzenberger integration using the call spread:
# P(S > 165) = -dC/dK evaluated at K=165 using interpolated call prices
# Result: bl_prob = 0.42

# Polymarket midpoint from the book:
# mid = (0.38 + 0.40) / 2 = 0.39

edge = bl_prob - snap.mid  # 0.42 - 0.39 = 0.03 (3 cent edge)
```

**Step 6: Strategy places orders**

```python
# Edge is positive -> fair value above market -> buy YES
# Place bid at 0.40 (inside the ask), ask at 0.44 (above fair value)
# The strategy submits these to the engine's order manager
# with latency: orders become active at t + 200ms, visible at t + 800ms
```

**Data staleness at this moment**:

| Source | Data Timestamp | Staleness |
|--------|---------------|-----------|
| Polymarket book | 10:30:14.800 ET | 0.323 seconds |
| Most recent trade | 10:30:14.500 ET | 0.623 seconds |
| Options chain | 10:30:14.900 ET | 0.223 seconds |
| Underlying price | 10:30:14.900 ET | 0.223 seconds |
| EOD Greeks | Previous close (March 29) | ~18 hours |
| Open Interest | March 29 close | ~18 hours |

All staleness values are sub-second during active market hours -- excellent for our use case.

---

## 10. Data Quality Checks

Run these checks during the ETL step and before each backtest. Quality issues are logged as warnings (non-fatal) unless they indicate data corruption.

### 10.1 Timestamp Monotonicity

```python
def check_monotonicity(ts_array: np.ndarray, source: str) -> list[str]:
    """Verify timestamps are strictly non-decreasing."""
    issues = []
    diffs = np.diff(ts_array)
    inversions = np.where(diffs < 0)[0]
    if len(inversions) > 0:
        issues.append(
            f"[{source}] {len(inversions)} timestamp inversions detected. "
            f"First at index {inversions[0]}: "
            f"{ts_array[inversions[0]]} > {ts_array[inversions[0]+1]}"
        )
    return issues
```

### 10.2 Gap Detection

```python
def check_gaps(
    ts_array: np.ndarray,
    source: str,
    max_gap_us: int = 5 * 60 * 1_000_000,  # 5 minutes
    market_hours_only: bool = True,
) -> list[str]:
    """Flag gaps > threshold during market hours."""
    issues = []
    diffs = np.diff(ts_array)
    large_gaps = np.where(diffs > max_gap_us)[0]
    for idx in large_gaps:
        gap_sec = diffs[idx] / 1_000_000
        ts_start = ts_array[idx]
        if market_hours_only and not _is_market_hours_us(ts_start):
            continue
        issues.append(
            f"[{source}] {gap_sec:.1f}s gap at {_format_ts(ts_start)}"
        )
    return issues
```

### 10.3 Cross-Source Consistency

```python
def check_underlying_consistency(
    options_price: float,
    stock_price: float,
    tolerance: float = 0.50,  # $0.50
) -> list[str]:
    """Check that underlying_price in options data ~= stock price."""
    issues = []
    diff = abs(options_price - stock_price)
    if diff > tolerance:
        issues.append(
            f"Underlying price mismatch: options={options_price:.2f}, "
            f"stock={stock_price:.2f}, diff={diff:.2f}"
        )
    return issues
```

### 10.4 Book Integrity (No-Arbitrage)

For Polymarket binary markets, the complementary token constraint must hold:

```python
def check_book_integrity(
    yes_book: BookSnapshot,
    no_book: BookSnapshot,
) -> list[str]:
    """Verify YES bid + NO ask <= 1.00 (no-arb condition).

    If YES bid + NO ask > 1.00, there is a riskless arbitrage:
    sell YES at bid, sell NO at ask, pay $1 at settlement, keep the spread.
    This should not persist in equilibrium.
    """
    issues = []
    if yes_book.is_valid and no_book.is_valid:
        yes_bid = yes_book.best_bid
        no_ask = no_book.best_ask
        if yes_bid + no_ask > 1.005:  # 0.5 cent tolerance for rounding
            issues.append(
                f"No-arb violation: YES bid={yes_bid:.3f} + NO ask={no_ask:.3f} "
                f"= {yes_bid + no_ask:.3f} > 1.00"
            )

        yes_ask = yes_book.best_ask
        no_bid = no_book.best_bid
        if yes_ask + no_bid > 1.005:
            issues.append(
                f"No-arb violation: YES ask={yes_ask:.3f} + NO bid={no_bid:.3f} "
                f"= {yes_ask + no_bid:.3f} > 1.00"
            )
    return issues
```

### 10.5 Stale Data Detection

```python
def check_staleness(
    data_ts_us: int,
    cursor_us: int,
    source: str,
    max_stale_us: int = 30 * 60 * 1_000_000,  # 30 minutes
) -> list[str]:
    """Flag if the latest data point is stale relative to cursor."""
    issues = []
    stale = cursor_us - data_ts_us
    if stale > max_stale_us:
        stale_min = stale / (60 * 1_000_000)
        issues.append(
            f"[{source}] Data is {stale_min:.1f} minutes stale at cursor time"
        )
    return issues
```

### 10.6 Summary Report

```python
def run_all_quality_checks(provider: DataProvider, markets: list[dict]) -> dict:
    """Run all quality checks and return a summary report."""
    report = {
        "monotonicity": [],
        "gaps": [],
        "consistency": [],
        "integrity": [],
        "staleness": [],
        "total_issues": 0,
    }
    # ... run each check, accumulate issues ...
    report["total_issues"] = sum(len(v) for v in report.values() if isinstance(v, list))
    return report
```

---

## 11. Integration Points

### 11.1 Engine Architecture Plan (bt_engine/)

The `DataProvider` maps directly to **Layer 1: Data Ingestion** in the [[Engine-Architecture-Plan]]:

| Engine Architecture Layer | DataProvider Role |
|--------------------------|-------------------|
| **A1: Telonex book_snapshot_full Loader** | `DataProvider.book()` -- returns `BookSnapshot` |
| **A2: Telonex trades Loader** | `DataProvider.trades()` -- returns `list[Trade]` |
| **A3: Underlying Price Loader** | `DataProvider.underlying_price()` -- returns `float` |
| **A4: Options Chain Loader** | `DataProvider.options_chain()` -- returns `OptionsChain` |

The existing `bt_engine/data/loader.py` `DataLoader` class builds a `DataStore` with a pre-sorted `timeline` of `TimelineEvent` objects. The `DataProvider` complements this by providing **on-demand, time-filtered queries** rather than pre-loading everything into a timeline.

**Recommended integration**: The `DataLoader` continues to build the Polymarket event timeline (BOOK_SNAPSHOT + TRADE events), which drives the engine's event loop. The `DataProvider` is called **within** the event loop by the strategy and fair value layer to access options data, underlying prices, and cross-market lookups -- data that is not part of the event timeline but needed at each simulation step.

```
Engine Event Loop (bt_engine/engine/loop.py)
    |
    |-- for each TimelineEvent:
    |       |
    |       |-- BOOK_SNAPSHOT: update TokenBook state (from DataStore)
    |       |-- TRADE: check fills, drain queue (from DataStore)
    |       |
    |       |-- provider.advance_time_us(event.timestamp_us)
    |       |
    |       |-- Strategy.on_event():
    |               |-- provider.options_chain("NVDA", expiry)  # for B-L
    |               |-- provider.underlying_price("NVDA")        # for B-S
    |               |-- provider.greeks_eod("NVDA")              # for risk
    |               |-- ... compute fair value, decide quotes ...
```

### 11.2 Existing Backtester Code

**`Telonex testing/src/`** (NVDA POC):
- `data_loader.py`: Loads `book_snapshot_25` + NVDA prices. The `DataProvider` supersedes this with full-depth books, tick-level options, and the registry-based token resolution.
- `engine.py`: Event loop processes snapshots chronologically. The `DataProvider.advance_time_us()` call integrates naturally into this loop pattern.
- `fill_simulator.py`: Uses `BookSnapshot` for queue position. The `DataProvider.book()` return type mirrors this.
- `fair_value.py`: Computes B-S fair values using NVDA price + fixed sigma. The `DataProvider.options_chain()` enables switching to B-L with real options data.

**`backtesting-engine/bt_engine/`** (production engine):
- `data/schema.py`: `BookSnapshot`, `TradeEvent`, `UnderlyingPrice` -- same concepts as the `DataProvider` return types, but using integer ticks/centishares. The `DataProvider` uses float prices to match source data; the engine's internal types handle conversion.
- `data/loader.py`: `DataLoader` builds a `DataStore` with timeline. The `DataProvider` adds options data access that `DataLoader` does not currently handle.
- `data/store.py`: `DataStore` holds pre-loaded data. The `DataProvider` operates alongside it for data not in the timeline.
- `config.py`: `EngineConfig.data_dir` aligns with `DataProvider.__init__(data_dir=...)`.
- `types.py`: `EventKind.OPTIONS_CHAIN` and `EventKind.UNDERLYING_PRICE` are already defined, anticipating options data integration.

### 11.3 Download Scripts

**`scripts/download_options.py`**:
- Add `tick-quotes` command (Section 3.1)
- Existing `eod` command already produces the right output format
- Add `--atm-pct` and `--max-dte` flags to the existing `intraday-iv` command for consistency

**`scripts/download_telonex.py`** (new):
- Downloads book_snapshot_full + trades per market per day
- Builds event stream (Section 4)
- Updates market registry

**`scripts/build_registry.py`** (new):
- Queries Telonex markets dataset
- Filters to stock/index markets
- Annotates with ticker, strike, expiry
- Saves `data/aligned/market_registry.parquet`

### 11.4 Migration Path

| Phase | Scope | Deliverable |
|-------|-------|-------------|
| **Phase 1** | Download pipeline | `tick-quotes` command in `download_options.py`, `download_telonex.py`, `build_registry.py` |
| **Phase 2** | Event stream ETL | `build_event_stream()` function, integrated into download pipeline |
| **Phase 3** | DataProvider core | `DataProvider` class with `book()`, `trades()`, `options_chain()`, `underlying_price()` |
| **Phase 4** | Engine integration | Wire `DataProvider` into `bt_engine/engine/loop.py`, strategy calls `provider.options_chain()` for B-L |
| **Phase 5** | Quality + verification | All Section 10 checks, audit log, `verify_no_lookahead()` |
| **Phase 6** | Performance tuning | LRU cache, lazy loading benchmarks, memory profiling |
