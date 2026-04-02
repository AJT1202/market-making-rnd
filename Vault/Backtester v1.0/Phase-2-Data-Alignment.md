---
title: "Phase 2: Data Alignment Layer & DataProvider"
created: 2026-04-02
updated: 2026-04-02
tags:
  - backtester
  - phase-2
  - data-alignment
  - dataprovider
  - implementation-plan
  - parquet
  - tick-level
  - no-lookahead
status: plan
related:
  - "[[Data-Alignment-Architecture]]"
  - "[[Engine-Architecture-Plan]]"
  - "[[NVDA-POC-Results]]"
  - "[[Polymarket-Data-API]]"
  - "[[ThetaData-Options-API]]"
  - "[[Polymarket-CLOB-Mechanics]]"
  - "[[Telonex-Data-Platform]]"
---

# Phase 2: Data Alignment Layer & DataProvider

> **Purpose**: Implementation plan for the data alignment layer that transforms raw downloaded Parquet files into a unified, time-consistent interface the backtesting engine consumes. This is the bridge between Phase 1 (raw data download) and Phase 3 (engine event loop).
>
> **Scope**: Polymarket event stream construction, `DataProvider` class, tick-level options indexing, no-lookahead enforcement, and data quality pipeline.
>
> **Primary spec**: [[Data-Alignment-Architecture]] -- all interfaces and algorithms are defined there. This plan adds implementation ordering, testing strategy, integration contracts, and task breakdown.

---

## Table of Contents

1. [Architecture Diagram](#1-architecture-diagram)
2. [Event Stream Spec](#2-event-stream-spec)
3. [DataProvider API](#3-dataprovider-api)
4. [Tick-Level Options Indexing](#4-tick-level-options-indexing)
5. [No-Lookahead Implementation](#5-no-lookahead-implementation)
6. [Data Quality Pipeline](#6-data-quality-pipeline)
7. [Integration Contract](#7-integration-contract)
8. [Testing Strategy](#8-testing-strategy)
9. [Task Breakdown](#9-task-breakdown)

---

## 1. Architecture Diagram

```
                         RAW PARQUET STORE (Phase 1 output)
    =====================================================================
    |                                                                   |
    |  telonex/                          thetadata/                     |
    |    book_raw/                         tick_quotes/                 |
    |      {slug}/{date}_book.parquet        {date}/{TKR}_{EXP}.pqt   |
    |    trades_raw/                       eod/                        |
    |      {slug}/{date}_trades.parquet      {date}/greeks.parquet     |
    |                                        {date}/oi.parquet         |
    |  aligned/                                                        |
    |    market_registry.parquet                                       |
    =====================================================================
                  |                              |
                  v                              |
    +---------------------------+                |
    | Event Stream ETL          |                |
    | (build_event_stream)      |                |
    |                           |                |
    | book_raw + trades_raw     |                |
    |   --> merged, sorted      |                |
    |   --> events/{slug}/      |                |
    |       {date}_events.pqt   |                |
    +---------------------------+                |
                  |                              |
                  v                              v
    =====================================================================
    |                     DataProvider                                  |
    |                                                                   |
    |  +------------------+  +-------------------+  +----------------+ |
    |  | Polymarket Index |  | Options Index     |  | EOD Data       | |
    |  | book_cache:      |  | options_cache:    |  | greeks_df      | |
    |  |   (slug,side) -> |  |   (tkr,exp) ->   |  | oi_df          | |
    |  |   (ts[], df)     |  |   (ts[], df)     |  | (loaded once)  | |
    |  | trade_cache:     |  |                   |  |                | |
    |  |   (slug,side) -> |  | LRU chain cache:  |  |                | |
    |  |   (ts[], df)     |  |   16 entries      |  |                | |
    |  +------------------+  +-------------------+  +----------------+ |
    |                                                                   |
    |  Time Cursor: t_us (monotonically increasing, int64 microseconds)|
    |  No-Lookahead Guard: assert(data_ts <= cursor_us) on every query |
    |  Audit Log: optional recording of every data access              |
    =====================================================================
                  |
                  v
    +-------------------------------------------------------------------+
    |                  Backtester Event Loop (Phase 3)                  |
    |                  bt_engine/engine/loop.py                         |
    |                                                                   |
    |  for event in timeline:        # BOOK_SNAPSHOT, TRADE events     |
    |      provider.advance_time_us(event.timestamp_us)                |
    |      book = provider.book(token_id)       # L2 state as-of t    |
    |      trades = provider.trades(token_id, since)                   |
    |      chain = provider.options_chain(tkr, exp)  # for B-L        |
    |      spot = provider.underlying_price(tkr)     # for B-S        |
    |      greeks = provider.greeks_eod(tkr)         # for risk       |
    +-------------------------------------------------------------------+
                  |
                  v
    +-------------------------------------------------------------------+
    |                     Strategy Layer                                |
    |  - Breeden-Litzenberger probability from options chain            |
    |  - Black-Scholes fair value from underlying price                 |
    |  - Quote generation, inventory management                        |
    +-------------------------------------------------------------------+
```

### Data Volume Summary

| Source | Files/Day | Rows/File (est.) | Bytes/File | Total/Day |
|--------|-----------|-------------------|------------|-----------|
| Polymarket book_snapshot_full | 10 (5 strikes x 2 tokens) | 10-30K | 5-20 MB | 50-200 MB |
| Polymarket trades | 10 (5 strikes x 2 tokens) | 1-5K | 1-5 MB | 10-50 MB |
| Polymarket events (merged) | 10 | 11-35K | 6-25 MB | 60-250 MB |
| ThetaData tick quotes | ~55 (11 tkrs x 5 exp) | 50-200K | ~15 MB | 5-8 GB |
| ThetaData EOD Greeks | 1 | ~10K | ~2 MB | 2 MB |
| ThetaData Open Interest | 1 | ~10K | ~1 MB | 1 MB |

---

## 2. Event Stream Spec

### 2.1 Schema

Each `telonex/events/{slug}/{date}_events.parquet` file contains both BOOK_UPDATE and TRADE events for both YES and NO tokens, interleaved chronologically.

| Column | Type | Description | Null? |
|--------|------|-------------|-------|
| `timestamp_us` | int64 | Microseconds since epoch, UTC | Never |
| `event_type` | string | `BOOK_UPDATE` or `TRADE` | Never |
| `token_side` | string | `YES` or `NO` | Never |
| `bid_price_0` .. `bid_price_N` | float64 | Book bid levels, descending | Null for TRADE |
| `bid_size_0` .. `bid_size_N` | float64 | Book bid sizes | Null for TRADE |
| `ask_price_0` .. `ask_price_N` | float64 | Book ask levels, ascending | Null for TRADE |
| `ask_size_0` .. `ask_size_N` | float64 | Book ask sizes | Null for TRADE |
| `trade_price` | float64 | Trade execution price | Null for BOOK_UPDATE |
| `trade_size` | float64 | Trade size in shares | Null for BOOK_UPDATE |
| `trade_taker_side` | string | `buy` or `sell` | Null for BOOK_UPDATE |

**Sort order**: `(timestamp_us ASC, _priority ASC)` where `_priority` = 0 for BOOK_UPDATE, 1 for TRADE. The `_priority` column is used during construction only and dropped from the final file.

**Parquet settings**: Snappy compression, sorted by `timestamp_us`, row group size 128 MB.

### 2.2 Merge Algorithm (Polars)

The merge builds one events file per market per day. For each token side (YES and NO), book snapshots and trades are loaded, tagged, union-merged, and sorted. Both token sides are concatenated into one file.

```python
import polars as pl
from pathlib import Path


def build_event_stream(
    book_path: Path,          # book_raw/{slug}/{date}_book.parquet
    trades_path: Path,        # trades_raw/{slug}/{date}_trades.parquet
    output_path: Path,        # events/{slug}/{date}_events.parquet
    token_side: str,          # "YES" or "NO"
) -> pl.DataFrame:
    """Merge book snapshots and trades into a unified event stream.

    Both inputs must be sorted by timestamp_us (Telonex guarantees this).
    Output is sorted by (timestamp_us, _priority) where
    BOOK_UPDATE=0 < TRADE=1 at the same microsecond.
    """
    # --- Load and tag book snapshots ---
    book_df = pl.read_parquet(book_path)
    book_df = book_df.with_columns([
        pl.lit("BOOK_UPDATE").alias("event_type"),
        pl.lit(token_side).alias("token_side"),
        pl.lit(0).alias("_priority"),
        pl.lit(None).cast(pl.Float64).alias("trade_price"),
        pl.lit(None).cast(pl.Float64).alias("trade_size"),
        pl.lit(None).cast(pl.String).alias("trade_taker_side"),
    ])

    # --- Load and tag trades ---
    trades_df = pl.read_parquet(trades_path)
    trades_df = trades_df.with_columns([
        pl.lit("TRADE").alias("event_type"),
        pl.lit(token_side).alias("token_side"),
        pl.lit(1).alias("_priority"),
    ])
    trades_df = trades_df.rename({
        "price": "trade_price",
        "size": "trade_size",
        "side": "trade_taker_side",
    })
    # Add null book columns to trades
    book_cols = [c for c in book_df.columns
                 if c.startswith(("bid_", "ask_")) and c not in trades_df.columns]
    for col in book_cols:
        trades_df = trades_df.with_columns(
            pl.lit(None).cast(pl.Float64).alias(col)
        )

    # --- Union merge and sort ---
    merged = pl.concat([book_df, trades_df], how="diagonal_relaxed")
    merged = merged.sort(["timestamp_us", "_priority"])
    merged = merged.drop("_priority")

    # --- Write ---
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.write_parquet(output_path, compression="snappy")
    return merged


def build_market_events(
    slug: str,
    date_str: str,
    data_dir: Path,
) -> Path:
    """Build combined YES+NO event stream for a market on a date.

    Calls build_event_stream for each token side, then concatenates
    and re-sorts into a single file.
    """
    events_dir = data_dir / "telonex" / "events" / slug
    output_path = events_dir / f"{date_str}_events.parquet"

    frames = []
    for token_side in ("YES", "NO"):
        book_path = data_dir / "telonex" / "book_raw" / slug / f"{date_str}_book_{token_side.lower()}.parquet"
        trades_path = data_dir / "telonex" / "trades_raw" / slug / f"{date_str}_trades_{token_side.lower()}.parquet"

        if not book_path.exists():
            continue

        if trades_path.exists():
            df = build_event_stream(book_path, trades_path, output_path, token_side)
        else:
            # No trades file: emit book events only (illiquid market)
            df = pl.read_parquet(book_path).with_columns([
                pl.lit("BOOK_UPDATE").alias("event_type"),
                pl.lit(token_side).alias("token_side"),
                pl.lit(None).cast(pl.Float64).alias("trade_price"),
                pl.lit(None).cast(pl.Float64).alias("trade_size"),
                pl.lit(None).cast(pl.String).alias("trade_taker_side"),
            ])
        frames.append(df)

    if not frames:
        raise FileNotFoundError(f"No book data found for {slug} on {date_str}")

    combined = pl.concat(frames, how="diagonal_relaxed")
    combined = combined.sort("timestamp_us")
    combined.write_parquet(output_path, compression="snappy")
    return output_path
```

### 2.3 Edge Cases

| Edge Case | Resolution | Rationale |
|-----------|------------|-----------|
| **Simultaneous timestamps** (book + trade at same `timestamp_us`) | BOOK_UPDATE processed first (`_priority` 0 < 1) | Matches the BTC engine's "external before internal" principle: book state reflects pre-trade conditions, then the trade fires against it |
| **Trades between snapshots** (trade at T, nearest snapshots at T-2s and T+1s) | Normal. Trade carries its own price/size/side. Book state for queue position is the most recent BOOK_UPDATE with `timestamp_us <= T` | Trades are independent events; the engine uses the last known book state for context |
| **Duplicate timestamps** (two book snapshots at the same microsecond) | Keep both; stable sort preserves file order | Rare, but the later one in file order is the more recent state |
| **Missing trades file** (no trades on a day) | Event stream has only BOOK_UPDATE events | Valid for illiquid markets; fill simulation correctly produces zero fills |
| **Crossed book** (`best_bid >= best_ask` in a snapshot) | Flag in data quality (Section 6) but do not discard | Crossed books occur briefly during matching engine latency and are informative |
| **Stale NO token data** (NO book updates lag YES by minutes) | Accept the staleness; forward-fill means the last known state is used | The DataProvider surfaces staleness via returned timestamps; strategy can detect and adapt |

---

## 3. DataProvider API

### 3.1 Full Interface

The `DataProvider` class lives at `bt_engine/data/provider.py`. It is instantiated once per backtest run and passed to both the engine loop and the strategy.

```python
class DataProvider:
    """Unified data access with time-cursor enforcement.

    All queries return data as-of the current time cursor.
    The cursor advances monotonically; backward movement raises ValueError.
    Internally, sorted int64 numpy timestamp arrays enable O(log n) lookups
    via np.searchsorted.
    """

    def __init__(
        self,
        data_dir: Path,
        market_date: date,
        audit: bool = False,     # Enable access logging for post-hoc verification
    ) -> None: ...

    # ---- Time Cursor ----

    def advance_time(self, t: datetime) -> None:
        """Advance cursor to timezone-aware datetime t. Raises ValueError if t < current."""

    def advance_time_us(self, t_us: int) -> None:
        """Advance cursor to raw microsecond timestamp. Raises ValueError if t_us < current."""

    @property
    def current_time_us(self) -> int:
        """Current cursor position in microseconds since epoch."""

    # ---- Polymarket: Orderbook ----

    def book(self, token_id: str) -> BookSnapshot | None:
        """Most recent L2 book snapshot as-of cursor.
        Returns None if no data available yet for this token."""

    def midpoint(self, token_id: str) -> float | None:
        """Convenience: book midpoint as-of cursor. None if no valid book."""

    # ---- Polymarket: Trades ----

    def trades(self, token_id: str, since: datetime) -> list[Trade]:
        """All trades in (since, cursor] for a token.
        since must be <= cursor. Returns list sorted by timestamp."""

    # ---- Options: Chain ----

    def options_chain(self, ticker: str, expiry: date) -> OptionsChain | None:
        """Full NBBO options chain as-of cursor.
        Returns the most recent tick quote per (strike, right) with ts <= cursor.
        None if no data loaded for this ticker/expiry."""

    # ---- Options: Single IV Lookup ----

    def implied_vol(
        self, ticker: str, strike: float, right: str, expiry: date
    ) -> IVQuote | None:
        """IV for a single contract as-of cursor.
        More efficient than full chain when you need one contract."""

    # ---- EOD Data ----

    def greeks_eod(self, ticker: str) -> pd.DataFrame:
        """Previous day's EOD Greeks. Empty DataFrame if not yet available.
        Availability: cursor > previous_close + 17:15 ET."""

    def open_interest(self, ticker: str) -> pd.DataFrame:
        """Previous day's open interest. Empty DataFrame if not yet available.
        Availability: cursor > market_date 06:30 ET."""

    # ---- Underlying Price ----

    def underlying_price(self, ticker: str) -> float | None:
        """Latest underlying spot price as-of cursor.
        Derived from underlying_price field in tick-level options quotes.
        None if no options data loaded for this ticker."""

    # ---- Audit ----

    @property
    def audit_log(self) -> list[dict]:
        """Access log entries: {cursor_us, data_timestamp_us, source}."""

    def verify_no_lookahead(self) -> bool:
        """Post-hoc verification that no returned data had ts > cursor.
        Raises AssertionError with details on first violation."""
```

### 3.2 Return Type Dataclasses

All return types are frozen, slotted dataclasses for immutability and low memory overhead.

```python
@dataclass(frozen=True, slots=True)
class BookSnapshot:
    timestamp_us: int
    token_id: str
    token_side: str                     # "YES" or "NO"
    bids: list[tuple[float, float]]     # [(price, size), ...] descending
    asks: list[tuple[float, float]]     # [(price, size), ...] ascending

    # Properties: best_bid, best_ask, mid, spread, is_valid


@dataclass(frozen=True, slots=True)
class Trade:
    timestamp_us: int
    token_id: str
    token_side: str                     # "YES" or "NO"
    price: float
    size: float
    taker_side: str                     # "buy" or "sell"


@dataclass(frozen=True, slots=True)
class OptionsChain:
    timestamp_us: int                   # Most recent quote timestamp
    ticker: str
    expiry: date
    underlying_price: float
    quotes: list[OptionQuote]

    # Methods: calls(), puts(), get(strike, right)


@dataclass(frozen=True, slots=True)
class OptionQuote:
    timestamp_us: int
    symbol: str                         # OPRA symbol
    strike: float
    right: str                          # "C" or "P"
    expiration: date
    bid: float
    bid_size: int
    ask: float
    ask_size: int
    underlying_price: float
    iv: float | None


@dataclass(frozen=True, slots=True)
class IVQuote:
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

### 3.3 Error Handling

| Error Condition | Behavior | Rationale |
|-----------------|----------|-----------|
| `advance_time_us(t)` where `t < cursor` | `raise ValueError` | Backward cursor enables subtle lookahead bugs |
| Token ID not in market registry | `raise KeyError` | Configuration error; fail fast |
| Parquet file not found for a (slug, date) | Return `None` / empty list | Missing data is normal for illiquid markets |
| Data point returned with `ts > cursor` | `raise LookaheadError` (assertion guard) | Critical invariant; should never fire unless there is a binary search bug |
| Crossed book (`best_bid >= best_ask`) | Return as-is; flag in quality checks | Transient microstructure event; do not suppress data |

### 3.4 Thread Safety

The `DataProvider` is single-threaded by design. The backtester event loop is sequential and deterministic. No locks or thread-safety mechanisms are needed.

---

## 4. Tick-Level Options Indexing

### 4.1 The Challenge

Tick-level NBBO data for options is the largest data source: ~100K rows per (ticker, expiry) per day, with ~55 unique (ticker, expiry) pairs. A full day is 5-8 GB on disk and ~825 MB in memory after loading.

The access pattern is highly non-uniform: the strategy queries `options_chain()` at every Polymarket event (up to 35K events/day), but only for the tickers and expiries relevant to the current market.

### 4.2 How np.searchsorted Works on Parquet Data

```
                  Parquet on Disk                    In-Memory After Load
              +------------------+              +---------------------------+
              |  Row Group 1     |              |  ts_array: np.ndarray     |
              |   128 MB         |   read_pqt   |    dtype=int64            |
              |   sorted by ts   | -----------> |    [934000100, 934000200, |
              +------------------+              |     934000300, ...]       |
              |  Row Group 2     |              |    contiguous, sorted     |
              |   128 MB         |              +---------------------------+
              +------------------+              |  df: pl.DataFrame         |
              |  ...             |              |    all columns in memory  |
              +------------------+              +---------------------------+

  Query: "latest quote as-of t=934000250"

  idx = np.searchsorted(ts_array, 934000250, side="right") - 1
       = np.searchsorted([...100, 200, 300, ...], 250, side="right") - 1
       = 2 - 1
       = 1   (points to ts=934000200, which is the last ts <= 250)

  row = df.row(1, named=True)   # O(1) access into Polars DataFrame
```

**Performance characteristics**:

| Operation | Complexity | Latency (5M rows) |
|-----------|------------|-------------------|
| `np.searchsorted` on sorted int64 array | O(log n) | ~1 microsecond |
| `df.row(idx)` random access in Polars | O(1) | ~5 microseconds |
| `df.slice(0, hi).group_by().last()` | O(hi * num_groups) | ~1-5 ms for full chain |
| Total `options_chain()` call | -- | ~2-6 ms |

### 4.3 Memory Layout

```
  Per (ticker, expiry) loaded into _options_cache:
  +-------------------------------------------------+
  | Key: ("NVDA", "20260330")                       |
  |                                                  |
  | ts_array: np.ndarray[int64]   ~0.8 MB (100K x 8B) |
  | df: pl.DataFrame              ~14 MB (100K rows,   |
  |     11 columns x ~13 bytes avg)                    |
  |                                                  |
  | Total per entry: ~15 MB                          |
  +-------------------------------------------------+

  Worst case (55 entries loaded): 55 x 15 MB = 825 MB
  Typical case (1-2 tickers, 3-4 expiries): 4-8 x 15 MB = 60-120 MB
```

### 4.4 Lazy Loading Policy

Data is loaded into memory **only when first queried** for a given cache key:

| Data Source | Cache Key | Trigger | Eviction |
|-------------|-----------|---------|----------|
| Polymarket books | `(slug, token_side)` | First `book()` call | Never (small count, always needed) |
| Polymarket trades | `(slug, token_side)` | First `trades()` call | Never |
| Tick quotes | `(ticker, expiry_str)` | First `options_chain()` call | Never (lazy = only needed entries) |
| EOD Greeks | global | First `greeks_eod()` call | Never (loaded once, ~2 MB) |
| Open Interest | global | First `open_interest()` call | Never (~1 MB) |

Since the typical backtest runs on 1 underlying with 3-5 relevant expiries, lazy loading keeps memory well under the 1.5 GB worst-case budget.

### 4.5 LRU Cache for options_chain()

The `options_chain()` method is the most expensive query because it performs a `group_by(["strike", "right"]).last()` on the subset of data up to the cursor. Strategies that call it multiple times at nearby timestamps would re-do this work unnecessarily.

```python
# Implementation inside DataProvider:

@lru_cache(maxsize=16)
def _cached_chain_lookup(
    self, ticker: str, exp_str: str, cursor_bucket: int
) -> OptionsChain | None:
    """Cache key groups nearby timestamps (within 1 second) into the same bucket.

    cursor_bucket = cursor_us // 1_000_000

    Rationale: the options chain is unlikely to change within a single second.
    The 16-entry LRU handles the case where the strategy queries multiple
    tickers/expiries in a round-robin pattern.
    """
    # ... actual group_by computation ...
```

**Cache eviction**: The `lru_cache` with `maxsize=16` evicts least-recently-used entries automatically. With 1-2 tickers and 3-5 expiries, 16 slots provide ~2 seconds of history per (ticker, expiry) pair, which is more than sufficient for the event loop's sequential access pattern.

**Cache invalidation**: The `cursor_bucket` (cursor_us // 1_000_000) naturally invalidates the cache when time advances by more than 1 second. No explicit invalidation is needed.

---

## 5. No-Lookahead Implementation

No-lookahead is the most critical correctness property. If any query returns future data, the entire backtest is invalid. Enforcement operates at three levels.

### 5.1 Level 1: Cursor Filtering (Primary)

Every query method uses binary search to find the boundary index. Only data at or before the boundary is returned.

```python
# Pattern used in every query method:
idx = np.searchsorted(ts_array, self._cursor_us, side="right") - 1
if idx < 0:
    return None  # No data available yet
row = df.row(idx, named=True)
# row["timestamp_us"] is guaranteed <= self._cursor_us
```

The `side="right"` parameter is critical: it returns the insertion point *after* any existing entries equal to `cursor_us`, so subtracting 1 gives the last entry with `timestamp_us <= cursor_us`.

### 5.2 Level 2: Assertion Guard (Defense in Depth)

Every data point returned to the caller passes through an assertion:

```python
class LookaheadError(RuntimeError):
    """Raised when a no-lookahead invariant is violated."""
    pass

def _assert_no_lookahead(self, data_timestamp_us: int, context: str = "") -> None:
    """Defense-in-depth check. If this fires, there is a bug in the binary search."""
    if data_timestamp_us > self._cursor_us:
        raise LookaheadError(
            f"LOOKAHEAD VIOLATION: data_ts={data_timestamp_us} > "
            f"cursor={self._cursor_us} (context: {context})"
        )
```

This assertion is called on every `BookSnapshot`, `Trade`, `OptionsChain`, and `IVQuote` returned. The performance overhead is negligible (one integer comparison per return).

### 5.3 Level 3: Post-Hoc Verification (Audit Mode)

When `audit=True`, the DataProvider records every data access:

```python
def _record_access(self, data_ts_us: int, source: str) -> None:
    if self._audit:
        self._audit_log.append({
            "cursor_us": self._cursor_us,
            "data_timestamp_us": data_ts_us,
            "source": source,
        })
        self._assert_no_lookahead(data_ts_us, source)
```

After the backtest, call `provider.verify_no_lookahead()` to scan the full audit log:

```python
def verify_no_lookahead(self) -> bool:
    """Post-hoc verification. Raises AssertionError on first violation."""
    for i, entry in enumerate(self._audit_log):
        if entry["data_timestamp_us"] > entry["cursor_us"]:
            delta_ms = (entry["data_timestamp_us"] - entry["cursor_us"]) / 1000
            raise AssertionError(
                f"Lookahead at entry {i}: source={entry['source']}, "
                f"lookahead={delta_ms:.1f}ms"
            )
    return True
```

### 5.4 Per-Source Availability Rules

Each data source has specific availability constraints beyond the basic timestamp filter:

| Data Source | Rule | Implementation |
|-------------|------|----------------|
| Polymarket books | Available whenever `timestamp_us <= cursor` | Standard binary search |
| Polymarket trades | Available whenever `timestamp_us <= cursor` | Standard binary search |
| Tick-level NBBO | Available whenever `timestamp_us <= cursor`; no quotes outside 9:30-16:00 ET | Standard binary search (hours enforced by data itself) |
| EOD Greeks | For date D: available only if `cursor >= D 17:15 ET` | Explicit `_et_to_us(prev_date, 17, 15)` check; return empty DataFrame before that |
| Open Interest | For date D: available only if `cursor >= D+1 06:30 ET` | Explicit `_et_to_us(market_date, 6, 30)` check; return empty DataFrame before that |
| Underlying price | Derived from tick NBBO `underlying_price` field | Same as tick-level NBBO |

### 5.5 Monotonic Cursor Enforcement

```python
def advance_time_us(self, t_us: int) -> None:
    if t_us < self._cursor_us:
        raise ValueError(
            f"Time cannot move backward: {t_us} < {self._cursor_us}. "
            f"This would enable lookahead by advancing, reading, then retreating."
        )
    self._cursor_us = t_us
    self._max_cursor_us = max(self._max_cursor_us, t_us)
```

The high-water mark `_max_cursor_us` is tracked separately for diagnostics but not used for enforcement (the cursor itself is sufficient).

---

## 6. Data Quality Pipeline

### 6.1 When Checks Run

Quality checks run at two stages:

1. **ETL time** (after `build_event_stream`): Validates the merged event file before it is consumed by the DataProvider. Issues are logged to `data/quality_reports/{date}_{slug}.json`. Fatal issues (timestamp inversions) block the pipeline.

2. **Backtest initialization** (when `DataProvider.__init__` loads data): Quick checks on loaded data. Issues are logged as warnings to stderr. Non-fatal by default; a `strict=True` mode can be added to fail on any issue.

### 6.2 Check Catalog

#### Check 1: Timestamp Monotonicity

```python
def check_monotonicity(ts_array: np.ndarray, source: str) -> list[str]:
    diffs = np.diff(ts_array)
    inversions = np.where(diffs < 0)[0]
    if len(inversions) > 0:
        return [
            f"[{source}] {len(inversions)} timestamp inversions. "
            f"First at index {inversions[0]}: {ts_array[inversions[0]]} > {ts_array[inversions[0]+1]}"
        ]
    return []
```

**Severity**: FATAL at ETL time (data must be re-sorted), WARNING at query time.

#### Check 2: Gap Detection During Market Hours

```python
def check_gaps(
    ts_array: np.ndarray,
    source: str,
    max_gap_us: int = 5 * 60 * 1_000_000,  # 5 minutes
) -> list[str]:
    diffs = np.diff(ts_array)
    large_gaps = np.where(diffs > max_gap_us)[0]
    issues = []
    for idx in large_gaps:
        gap_sec = diffs[idx] / 1_000_000
        if _is_market_hours_us(ts_array[idx]):
            issues.append(f"[{source}] {gap_sec:.1f}s gap at {_format_ts(ts_array[idx])}")
    return issues
```

**Severity**: WARNING. Gaps may indicate Telonex data drops or genuine illiquidity.

#### Check 3: Cross-Source Consistency

Compare `underlying_price` from tick NBBO against an independent source (e.g., 1-minute bars if available). Flag if the absolute difference exceeds $0.50.

**Severity**: WARNING.

#### Check 4: Book Integrity (Binary Market No-Arbitrage)

```python
def check_book_integrity(yes_book: BookSnapshot, no_book: BookSnapshot) -> list[str]:
    issues = []
    if yes_book.is_valid and no_book.is_valid:
        # YES_bid + NO_ask <= 1.00 (no riskless arb selling both sides)
        if yes_book.best_bid + no_book.best_ask > 1.005:
            issues.append(f"No-arb: YES_bid={yes_book.best_bid:.3f} + NO_ask={no_book.best_ask:.3f} > 1.00")
        # YES_ask + NO_bid <= 1.00 (no riskless arb buying both sides)
        if yes_book.best_ask + no_book.best_bid > 1.005:
            issues.append(f"No-arb: YES_ask={yes_book.best_ask:.3f} + NO_bid={no_book.best_bid:.3f} > 1.00")
    return issues
```

**Severity**: WARNING. Brief violations occur due to matching engine latency. Persistent violations (> 5 seconds) may indicate data corruption.

#### Check 5: Stale Data Detection

Flag if the most recent data point is > 30 minutes old relative to cursor during market hours. Indicates potential data feed issues.

**Severity**: WARNING.

### 6.3 Quality Report Format

```json
{
  "date": "2026-04-01",
  "source": "telonex/events/will-nvidia-nvda-close-above-165/2026-04-01_events.parquet",
  "checks": {
    "monotonicity": {"status": "PASS", "issues": []},
    "gaps": {"status": "WARN", "issues": ["[BOOK_YES] 312.5s gap at 12:15:03 UTC"]},
    "integrity": {"status": "PASS", "issues": []},
    "staleness": {"status": "PASS", "issues": []}
  },
  "total_rows": 28450,
  "timestamp_range_utc": ["2026-04-01T00:00:12", "2026-04-01T23:59:48"],
  "summary": "1 warning, 0 errors"
}
```

### 6.4 Failure Handling

| Severity | ETL Behavior | Backtest Behavior |
|----------|-------------|-------------------|
| FATAL (inversions) | Abort ETL, do not write output | Refuse to load file |
| WARNING | Log to report, continue | Log to stderr, continue |
| INFO | Log to report | Silent |

---

## 7. Integration Contract

### 7.1 What Phase 3 (Engine) Expects from This Layer

The backtester event loop in `bt_engine/engine/loop.py` drives the simulation. The `DataProvider` integrates into this loop via two touchpoints:

**Touchpoint 1: Timeline Construction (DataLoader continues to own this)**

The existing `DataLoader` reads the merged event Parquet files and builds a `DataStore` with a sorted `timeline` of `TimelineEvent` objects. This is unchanged. The event stream files produced by Phase 2 ETL are the input to the DataLoader.

```python
# DataLoader reads Phase 2 output:
#   telonex/events/{slug}/{date}_events.parquet
# and builds:
#   DataStore.timeline: list[TimelineEvent]  # sorted, drives event loop
#   DataStore.snapshots: list[BookSnapshot]  # indexed by payload_index
#   DataStore.trades: list[TradeEvent]       # indexed by payload_index
```

**Touchpoint 2: On-Demand Queries (DataProvider, new)**

Within the event loop, the strategy and fair value layer call the `DataProvider` for data not in the timeline (options, underlying prices, EOD):

```python
# In engine/loop.py, the event loop becomes:

for event in timeline:
    # Advance DataProvider cursor to match engine time
    self.provider.advance_time_us(event.timestamp_us)

    # Phase 1: Process external event (unchanged)
    if event.kind == EventKind.BOOK_SNAPSHOT:
        snap = self.data.get_snapshot(event.payload_index)
        self.data.update_latest_snapshot(event.strike, event.token_side, event.payload_index)
        self._on_book_snapshot(snap)

    elif event.kind == EventKind.TRADE:
        trade = self.data.get_trade(event.payload_index)
        self._on_trade(trade)

    # Phase 4: Fair value (now uses DataProvider for options)
    # Phase 5: Strategy (now uses DataProvider for B-L inputs)
```

### 7.2 Method Call Patterns

The engine and strategy call the DataProvider in these patterns:

| Caller | Method | Frequency | Purpose |
|--------|--------|-----------|---------|
| Engine loop | `advance_time_us(event.timestamp_us)` | Every event (~35K/day) | Synchronize cursor with event loop |
| Strategy | `options_chain(ticker, expiry)` | Every BOOK_SNAPSHOT event (~20K/day) | B-L probability extraction |
| Strategy | `underlying_price(ticker)` | Every BOOK_SNAPSHOT event | B-S fair value input |
| Strategy | `greeks_eod(ticker)` | Once at start, once after 17:15 ET | Risk parameters |
| Strategy | `open_interest(ticker)` | Once after 06:30 ET | Signal for liquidity |
| Strategy | `book(token_id)` | On-demand (already available via DataStore) | Cross-market lookups |
| Strategy | `trades(token_id, since)` | On-demand | Trade flow analysis |
| Engine (audit) | `verify_no_lookahead()` | Once after backtest completes | Correctness verification |

### 7.3 Constructor Wiring

```python
# In runner.py or the backtest entry point:

from bt_engine.data.provider import DataProvider
from bt_engine.data.loader import DataLoader

config = EngineConfig(...)
data_dir = config.data_dir

# Phase 2 output: DataProvider for on-demand queries
provider = DataProvider(
    data_dir=data_dir,
    market_date=config.event.market_date,
    audit=True,  # Enable for development; disable for production runs
)

# Existing Phase 1: DataLoader builds timeline + DataStore
loader = DataLoader(config)
store = loader.load()

# Engine gets both
engine = BacktestEngine(config=config, data=store, strategy=strategy, provider=provider)
```

### 7.4 Compatibility with Existing bt_engine Types

The `DataProvider` returns its own float-based dataclasses (`BookSnapshot`, `Trade`, etc.) while the existing `bt_engine/data/schema.py` uses integer ticks and centishares. The conversion boundary is:

| DataProvider Type | bt_engine Type | Conversion |
|------------------|----------------|------------|
| `BookSnapshot` (float prices) | `schema.BookSnapshot` (int ticks) | `price_ticks = round(price * 100)` |
| `Trade` (float price/size) | `TradeEvent` (int ticks/cs) | `price_ticks = round(price * 100)`, `size_cs = round(size * 100)` |
| `OptionsChain` | No equivalent in bt_engine | New type; used by strategy only |
| `IVQuote` | No equivalent | New type; used by strategy only |
| `underlying_price()` -> float | `UnderlyingPrice.price_cents` | `price_cents = round(price * 100)` |

The conversion happens at the strategy layer boundary, not inside the DataProvider. The DataProvider returns source-native floats; the strategy converts as needed when interacting with the engine's integer types.

---

## 8. Testing Strategy

### 8.1 Unit Tests

#### Test Group 1: Event Stream Construction

| Test | Input | Expected |
|------|-------|----------|
| `test_merge_basic` | 3 book snapshots + 2 trades, non-overlapping timestamps | 5 events in timestamp order |
| `test_merge_simultaneous` | 1 book + 1 trade at identical `timestamp_us` | Book event comes first |
| `test_merge_trades_only_missing` | Book file exists, trades file missing | Events file with BOOK_UPDATE only |
| `test_merge_both_tokens` | YES book+trades, NO book+trades | Single file with interleaved YES/NO events sorted by timestamp |
| `test_merge_preserves_all_columns` | Full-depth book (50 levels) + trade | All bid/ask columns present in BOOK_UPDATE rows; all null in TRADE rows |
| `test_merge_idempotent` | Run twice on same input | Identical output Parquet (byte-level via hash) |

#### Test Group 2: DataProvider Core

| Test | Input | Expected |
|------|-------|----------|
| `test_book_basic` | 5 book snapshots at t=1,2,3,4,5 | `book()` at t=3 returns snapshot 3 |
| `test_book_before_first` | Cursor before first snapshot | `book()` returns None |
| `test_trades_window` | 10 trades at t=1..10 | `trades(since=t3)` at cursor=t7 returns trades 4,5,6,7 |
| `test_options_chain_latest_per_strike` | 3 quotes for strike 165C at t=1,2,3; 2 quotes for 170C at t=1,2 | At t=3: chain has 165C@t3, 170C@t2 |
| `test_underlying_price_cross_expiry` | NVDA quotes from exp1 (latest at t=5) and exp2 (latest at t=7) | `underlying_price("NVDA")` at t=8 returns value from exp2@t7 |
| `test_greeks_eod_before_available` | Cursor at 09:00 ET | `greeks_eod()` returns empty DataFrame |
| `test_greeks_eod_after_available` | Cursor at 18:00 ET | `greeks_eod()` returns previous day's Greeks |
| `test_oi_before_available` | Cursor at 05:00 ET | `open_interest()` returns empty DataFrame |

#### Test Group 3: No-Lookahead

| Test | Input | Expected |
|------|-------|----------|
| `test_cursor_backward_raises` | `advance_time_us(100)` then `advance_time_us(50)` | `ValueError` |
| `test_no_future_data_book` | Book snapshots at t=1..5 | At cursor=3, returned snapshot has `ts <= 3` |
| `test_no_future_data_options` | Options quotes at t=1..5 | At cursor=3, all returned quotes have `ts <= 3` |
| `test_audit_log_records_all` | Multiple queries with `audit=True` | `verify_no_lookahead()` returns True; audit_log has one entry per data access |
| `test_audit_catches_violation` | Manually inject a future-timestamped entry into audit log | `verify_no_lookahead()` raises AssertionError |

### 8.2 Integration Tests

#### Known-Answer Test (KAT)

Use the NVDA POC data from March 30, 2026 as a known-answer corpus:

1. Build the event stream from the existing `book_snapshot_25` data (limited depth but validates the pipeline).
2. Instantiate a `DataProvider` pointing at the POC data.
3. At cursor = 10:30:15.123 ET, verify:
   - `book()` returns a snapshot with `timestamp_us <= cursor_us`
   - The best bid/ask match the values in the POC `data_loader.py` output for the same timestamp
   - `midpoint()` matches `(best_bid + best_ask) / 2`

#### Round-Trip Test

1. Write a synthetic event stream Parquet with known data.
2. Load it via `DataProvider`.
3. At every event timestamp, verify that the DataProvider returns exactly that event's data.
4. At every midpoint between events, verify the DataProvider returns the previous event's data.

#### End-to-End No-Lookahead Audit

1. Run a full backtest with `audit=True`.
2. Call `provider.verify_no_lookahead()` after the run.
3. Assert it returns True.
4. Verify the audit log has the expected number of entries (proportional to timeline length x queries per event).

### 8.3 Performance Benchmarks

| Benchmark | Target | Method |
|-----------|--------|--------|
| `np.searchsorted` on 5M int64 array | < 5 microseconds | `timeit` with 10K iterations |
| `options_chain()` cold (first call, loads Parquet) | < 500 ms | Single call, clock wall time |
| `options_chain()` warm (cached, same bucket) | < 10 microseconds | `timeit` with 10K iterations |
| `book()` warm | < 50 microseconds | `timeit` with 10K iterations |
| Full backtest overhead (DataProvider vs raw arrays) | < 10% wall time increase | Compare backtest with/without DataProvider |
| Memory: full day, all tickers | < 1.5 GB resident | `tracemalloc` peak measurement |

---

## 9. Task Breakdown

### Phase 2A: Event Stream ETL

| # | Task | Depends On | Est. Hours | Deliverable |
|---|------|-----------|------------|-------------|
| 2A.1 | Implement `build_event_stream()` function | Phase 1 download scripts | 3h | `bt_engine/data/etl.py` with merge logic |
| 2A.2 | Implement `build_market_events()` for dual-token merge | 2A.1 | 2h | Combined YES+NO event file builder |
| 2A.3 | Write unit tests for event stream construction | 2A.1 | 2h | `tests/test_etl.py` with 6 test cases from Section 8.1 Group 1 |
| 2A.4 | Integrate into download pipeline CLI | 2A.2 | 1h | `scripts/build_events.py` or flag on `download_telonex.py` |
| 2A.5 | Build event streams for NVDA POC data | 2A.4 | 0.5h | Validate on existing March 30 data |

### Phase 2B: DataProvider Core

| # | Task | Depends On | Est. Hours | Deliverable |
|---|------|-----------|------------|-------------|
| 2B.1 | Define return type dataclasses | -- | 1h | `bt_engine/data/provider_types.py` |
| 2B.2 | Implement `DataProvider.__init__` + time cursor | 2B.1 | 1h | `bt_engine/data/provider.py` scaffold |
| 2B.3 | Implement `book()` + `midpoint()` with lazy loading | 2B.2, 2A.1 | 3h | Polymarket book queries working |
| 2B.4 | Implement `trades()` with binary search range | 2B.3 | 2h | Trade window queries working |
| 2B.5 | Implement `options_chain()` with group-by | 2B.2 | 3h | Options chain queries working |
| 2B.6 | Implement `implied_vol()` as chain wrapper | 2B.5 | 0.5h | Single-contract IV lookup |
| 2B.7 | Implement `underlying_price()` from options cache | 2B.5 | 1h | Cross-expiry price aggregation |
| 2B.8 | Implement `greeks_eod()` + `open_interest()` with availability guards | 2B.2 | 2h | EOD data with time-gating |
| 2B.9 | Implement market registry resolution (`_resolve_token`) | 2B.2 | 1h | Token ID to (slug, side) mapping |
| 2B.10 | Write unit tests for DataProvider | 2B.3-2B.9 | 4h | `tests/test_provider.py` with 8 test cases from Section 8.1 Group 2 |

### Phase 2C: No-Lookahead Enforcement

| # | Task | Depends On | Est. Hours | Deliverable |
|---|------|-----------|------------|-------------|
| 2C.1 | Add `_assert_no_lookahead` + `LookaheadError` | 2B.2 | 0.5h | Assertion guard in provider |
| 2C.2 | Add `_record_access` audit logging | 2C.1 | 1h | Audit mode with access log |
| 2C.3 | Add `verify_no_lookahead()` post-hoc checker | 2C.2 | 1h | Verification function |
| 2C.4 | Wire assertions into all query methods | 2C.1, 2B.3-2B.8 | 1h | Every return path calls assert |
| 2C.5 | Write no-lookahead unit tests | 2C.4 | 2h | `tests/test_no_lookahead.py` with 5 test cases from Section 8.1 Group 3 |

### Phase 2D: LRU Cache & Performance

| # | Task | Depends On | Est. Hours | Deliverable |
|---|------|-----------|------------|-------------|
| 2D.1 | Implement `_cached_chain_lookup` with `cursor_bucket` | 2B.5 | 1.5h | LRU cache on `options_chain()` |
| 2D.2 | Run performance benchmarks | 2D.1 | 1h | Benchmark results vs targets in Section 8.3 |
| 2D.3 | Profile memory usage with `tracemalloc` | 2D.1 | 1h | Memory report, verify < 1.5 GB |

### Phase 2E: Data Quality Pipeline

| # | Task | Depends On | Est. Hours | Deliverable |
|---|------|-----------|------------|-------------|
| 2E.1 | Implement 5 quality check functions | 2B.1 | 3h | `bt_engine/data/quality.py` |
| 2E.2 | Implement `run_all_quality_checks()` summary | 2E.1 | 1h | Report generator |
| 2E.3 | Integrate quality checks into ETL pipeline | 2E.2, 2A.4 | 1h | Checks run after event stream build |
| 2E.4 | Integrate quality checks into DataProvider init | 2E.2, 2B.2 | 1h | Checks run on load (optional) |
| 2E.5 | Write quality check tests | 2E.1 | 2h | `tests/test_quality.py` |

### Phase 2F: Integration & Validation

| # | Task | Depends On | Est. Hours | Deliverable |
|---|------|-----------|------------|-------------|
| 2F.1 | Known-answer test with NVDA POC data | 2B.10, 2A.5 | 2h | `tests/test_kat.py` |
| 2F.2 | Round-trip test with synthetic data | 2B.10 | 2h | `tests/test_roundtrip.py` |
| 2F.3 | Wire DataProvider into `BacktestEngine.__init__` | 2B.10, 2C.5 | 2h | Modified `engine/loop.py` + `runner.py` |
| 2F.4 | End-to-end backtest with audit verification | 2F.3 | 2h | Full run + `verify_no_lookahead()` pass |
| 2F.5 | Documentation: update `bt_engine/README` and docstrings | 2F.4 | 1h | Inline docs |

### Dependency Graph

```
Phase 2A (ETL)              Phase 2B (DataProvider)       Phase 2C (No-Lookahead)
  2A.1 ──> 2A.2             2B.1 ──> 2B.2                  2C.1 ──> 2C.2 ──> 2C.3
    │        │                 │                               │
    v        v                 v                               v
  2A.3    2A.4 ──> 2A.5     2B.3 ──> 2B.4                  2C.4 ──> 2C.5
                               │
                               v
                    2B.5 ──> 2B.6
                      │        │
                      v        v
                    2B.7    2B.8 ──> 2B.9 ──> 2B.10

Phase 2D (Performance)      Phase 2E (Quality)           Phase 2F (Integration)
  2D.1 ──> 2D.2 ──> 2D.3   2E.1 ──> 2E.2               2F.1 ──> 2F.2
    ^                          │       │                    │
    |                          v       v                    v
  2B.5                       2E.3   2E.4 ──> 2E.5        2F.3 ──> 2F.4 ──> 2F.5
                               ^       ^
                               |       |
                             2A.4   2B.2
```

### Estimated Total: ~51 hours

| Sub-Phase | Hours | Critical Path? |
|-----------|-------|---------------|
| 2A: Event Stream ETL | 8.5h | Yes (blocks 2B.3, 2F.1) |
| 2B: DataProvider Core | 18.5h | Yes (largest block) |
| 2C: No-Lookahead | 5.5h | Yes (blocks 2F.3) |
| 2D: Performance | 3.5h | No (can run after 2B) |
| 2E: Data Quality | 8h | No (can run in parallel with 2C/2D) |
| 2F: Integration | 9h | Yes (final validation) |

**Critical path**: 2A.1 -> 2A.2 -> 2B.3 -> 2B.5 -> 2B.10 -> 2C.4 -> 2C.5 -> 2F.3 -> 2F.4 (~35h)

**Parallelizable**: 2D (performance) and 2E (quality) can proceed independently once 2B.5 and 2A.4 are complete respectively.

---

## Appendix: File Inventory

Files created or modified by Phase 2:

| File | Action | Description |
|------|--------|-------------|
| `bt_engine/data/etl.py` | New | Event stream merge logic (`build_event_stream`, `build_market_events`) |
| `bt_engine/data/provider_types.py` | New | `BookSnapshot`, `Trade`, `OptionsChain`, `OptionQuote`, `IVQuote` dataclasses |
| `bt_engine/data/provider.py` | New | `DataProvider` class with all query methods |
| `bt_engine/data/quality.py` | New | 5 quality check functions + summary report |
| `scripts/build_events.py` | New | CLI for building event streams from raw data |
| `bt_engine/engine/loop.py` | Modified | Add `provider.advance_time_us()` call in event loop |
| `bt_engine/runner.py` | Modified | Wire `DataProvider` into engine construction |
| `bt_engine/config.py` | Modified | Add `market_date` field to `EngineConfig` |
| `tests/test_etl.py` | New | Event stream construction tests |
| `tests/test_provider.py` | New | DataProvider unit tests |
| `tests/test_no_lookahead.py` | New | No-lookahead enforcement tests |
| `tests/test_quality.py` | New | Data quality check tests |
| `tests/test_kat.py` | New | Known-answer test with NVDA POC data |
| `tests/test_roundtrip.py` | New | Round-trip synthetic data test |
