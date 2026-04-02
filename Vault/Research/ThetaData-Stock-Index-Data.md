---
title: ThetaData Stock & Index Price Data
created: 2026-03-31
updated: 2026-03-31
tags:
  - thetadata
  - stocks
  - indices
  - ohlcv
  - market-data
  - backtesting
  - spx
  - ndx
---

# ThetaData Stock & Index Price Data

Reference for underlying stock and index price data from ThetaData. This is critical for backtesting — we need the underlying price at every point to compute real-time fair values in the [[Breeden-Litzenberger]] pipeline and to reconcile with option Greeks (which include `underlying_price` in their response).

## Stock Data Overview

### Subscription Tiers — Stock Data

| Tier | Granularity | History Start | Concurrent Requests | Delay |
|------|-------------|---------------|---------------------|-------|
| FREE | EOD | 2023-06-01 | 1 (30 req/min) | 1 day |
| VALUE | 1 Minute | 2021-01-01 | 1 | 15-minute delay |
| STANDARD | 1 Minute | 2016-01-01 | 2 | Real-time |
| PRO | Tick Level | 2012-06-01 | 4 | Real-time |

### Data Source

- **UTP-C (Nasdaq):** Full historical data back to 2012-06-01
- **CTA-A/B (NYSE):** History begins 2020-01-01 only
- **Real-time:** Nasdaq Basic (BBO within 1% of NBBO 99.22% of the time)

> **Important:** Some symbols are tape-specific. `SPY` is NOT on the UTP tape, so no historical stock data or Greeks data exists before 2020-01-01 for SPY. Most of our target tickers (AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, NFLX, PLTR) are on Nasdaq/UTP and have full history.

### Ticker Coverage for Our Universe

| Ticker | Exchange | Tape | History Start (Standard) | Notes |
|--------|----------|------|--------------------------|-------|
| AAPL | NASDAQ | UTP-C | 2016-01-01 | Full coverage |
| MSFT | NASDAQ | UTP-C | 2016-01-01 | Full coverage |
| GOOGL | NASDAQ | UTP-C | 2016-01-01 | Full coverage |
| AMZN | NASDAQ | UTP-C | 2016-01-01 | Full coverage |
| META | NASDAQ | UTP-C | 2016-01-01 | Was FB before 2022-06 |
| NVDA | NASDAQ | UTP-C | 2016-01-01 | Full coverage |
| TSLA | NASDAQ | UTP-C | 2016-01-01 | Full coverage |
| NFLX | NASDAQ | UTP-C | 2016-01-01 | Full coverage |
| PLTR | NYSE | CTA | 2020-09-30 | IPO Sep 2020; CTA tape — verify availability |
| SPX | Index | CGIF | 2022-01-01 (Standard) | Index, not stock — use `/v3/index/` endpoints |
| NDX | Index | Nasdaq Indices | **NOT AVAILABLE** | Unsupported by ThetaData |

---

## Stock OHLCV Bars — 1-Minute Historical

### Endpoint

```
GET http://127.0.0.1:25503/v3/stock/history/ohlc
```

### Example: 1-Minute Bars for AAPL

```python
import httpx
import polars as pl
import io

BASE = "http://127.0.0.1:25503/v3"

r = httpx.get(f"{BASE}/stock/history/ohlc", params={
    "symbol": "AAPL",
    "date": "20260330",
    "interval": "1m",
    "start_time": "09:30:00",
    "end_time": "16:00:00",
    "format": "ndjson"
}, timeout=60)
r.raise_for_status()
df = pl.read_ndjson(io.StringIO(r.text))
```

### Multi-Day Request (max 1 month)

```python
r = httpx.get(f"{BASE}/stock/history/ohlc", params={
    "symbol": "AAPL",
    "start_date": "20260301",
    "end_date": "20260330",
    "interval": "1m",
    "format": "ndjson"
}, timeout=120)
```

### Parameters

| Parameter | Required | Type | Default | Description |
|-----------|----------|------|---------|-------------|
| `symbol` | Yes | string | - | Stock symbol |
| `date` | No | string | - | Specific date (overrides start/end_date) |
| `start_date` | No | string | - | Range start (inclusive) |
| `end_date` | No | string | - | Range end (inclusive) |
| `interval` | Yes | string | `1s` | `tick`, `10ms`, `100ms`, `500ms`, `1s`, `5s`, `10s`, `15s`, `30s`, `1m`, `5m`, `10m`, `15m`, `30m`, `1h` |
| `start_time` | No | string | `09:30:00` | Start time (HH:MM:SS.SSS) |
| `end_time` | No | string | `16:00:00` | End time (HH:MM:SS.SSS) |
| `venue` | No | string | `nqb` | `nqb` (Nasdaq Basic) or `utp_cta` (merged SIPs) |
| `format` | No | string | `csv` | `csv`, `json`, `ndjson`, `html` |

### Response Schema

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | datetime | Bar open time (`YYYY-MM-DDTHH:mm:ss.SSS`) |
| `open` | number | Opening trade price |
| `high` | number | Highest traded price |
| `low` | number | Lowest traded price |
| `close` | number | Closing traded price |
| `volume` | integer | Shares traded |
| `count` | integer | Number of trades |
| `vwap` | number | Volume-weighted average price |

> **Bar construction rule:** A trade is included when `bar_timestamp <= trade_time < bar_timestamp + interval`.

---

## Stock End-of-Day (EOD)

```python
# GET /v3/stock/history/eod
r = httpx.get(f"{BASE}/stock/history/eod", params={
    "symbol": "AAPL",
    "start_date": "20260101",
    "end_date": "20260330",
    "format": "ndjson"
}, timeout=60)
```

Response fields: `created`, `last_trade`, `open`, `high`, `low`, `close`, `volume`, `count`, `bid`, `ask`, `bid_size`, `ask_size` + exchange/condition codes.

Theta Data generates the EOD report at **17:15 ET** daily.

---

## Stock At-Time Quote (Point-in-Time Price)

Get the exact NBBO at a specific millisecond — critical for syncing underlying price with option snapshots.

```python
# GET /v3/stock/at_time/quote
r = httpx.get(f"{BASE}/stock/at_time/quote", params={
    "symbol": "AAPL",
    "start_date": "20260330",
    "end_date": "20260330",
    "time_of_day": "10:30:00.000",
    "format": "ndjson"
}, timeout=60)
```

Response: `timestamp`, `bid`, `ask`, `bid_size`, `ask_size` + exchange/condition codes.

---

## Stock Intraday Quotes (NBBO History)

```python
# GET /v3/stock/history/quote
# 1-minute NBBO snapshots
r = httpx.get(f"{BASE}/stock/history/quote", params={
    "symbol": "AAPL",
    "date": "20260330",
    "interval": "1m",
    "format": "ndjson"
}, timeout=60)
```

Response: `timestamp`, `bid`, `ask`, `bid_size`, `ask_size` + exchange/condition codes.

---

## Index Data

### SPX (S&P 500 Index)

SPX price data comes from the **CBOE Global Indices Feed (CGIF)**, which reports every ~1 second for popular indices.

#### Subscription Tiers — Index Data

| Tier | Granularity | History Start | Delay |
|------|-------------|---------------|-------|
| FREE | **NO ACCESS** | - | - |
| VALUE | 15-minute bars | 2023-01-01 | 15-minute delay |
| STANDARD | Exchange-reported (1s for SPX) | 2022-01-01 | Real-time |
| PRO | Exchange-reported (1s for SPX) | 2017-01-01 | Real-time |

> **Index data requires a paid subscription.** FREE tier has NO index access.

#### Supported Index Symbols

Real-time updates via CGIF: `SPX`, `VIX`, and related CBOE indices.

Historical coverage for `RUT`, `DJX`: between first access date and 2024-07-01 only.

**NDX and all Nasdaq Indices Feed symbols are NOT supported.** ThetaData plans synthetic index data matching official prices with 99% accuracy, but this is not yet available.

### Index OHLC Bars — 1-Minute

```python
# GET /v3/index/history/ohlc
r = httpx.get(f"{BASE}/index/history/ohlc", params={
    "symbol": "SPX",
    "start_date": "20260330",
    "end_date": "20260330",
    "interval": "1m",
    "start_time": "09:30:00",
    "end_time": "16:00:00",
    "format": "ndjson"
}, timeout=60)
```

Response schema: `timestamp`, `open`, `high`, `low`, `close`, `volume`, `count`, `vwap`

### Index Price History (Raw Ticks)

For finer-grained data — returns the index price at each interval. Exchanges typically report SPX every second.

```python
# GET /v3/index/history/price
r = httpx.get(f"{BASE}/index/history/price", params={
    "symbol": "SPX",
    "date": "20260330",
    "interval": "1s",
    "format": "ndjson"
}, timeout=60)
```

Response: `timestamp`, `price`

> **Note:** Price updates are omitted if the price hasn't changed from the previous report. Missing ticks = no price change.

### Index End-of-Day (EOD)

```python
# GET /v3/index/history/eod
r = httpx.get(f"{BASE}/index/history/eod", params={
    "symbol": "SPX",
    "start_date": "20260101",
    "end_date": "20260330",
    "format": "ndjson"
}, timeout=60)
```

Response: `created`, `last_trade`, `open`, `high`, `low`, `close`, `volume`, `count`, `bid`, `ask` + exchange/condition codes.

---

## NDX Workaround

Since NDX index price data is unavailable, consider these alternatives:

1. **Use QQQ as proxy:** QQQ tracks NDX closely. Pull QQQ stock data via `/v3/stock/history/ohlc`
2. **Supply `under_price` to Greeks endpoints:** When querying NDX option Greeks snapshots, manually specify the underlying price
3. **External data source:** Pull NDX levels from another vendor (e.g., Yahoo Finance, Polygon.io) and join with ThetaData options data

```python
# NDX options are available — just the index level is not
# Query NDX option chain normally:
r = httpx.get(f"{BASE}/option/history/greeks/eod", params={
    "symbol": "NDX",
    "expiration": "*",
    "start_date": "20260330",
    "end_date": "20260330",
    "format": "ndjson"
}, timeout=120)

# Greeks include underlying_price from last available source
# Or use QQQ-derived NDX estimate for independent verification
```

---

## Data Resolution & Granularity Summary

### Available Intervals (All Endpoints)

| Interval | Code | Notes |
|----------|------|-------|
| Every tick | `tick` | PRO only for stocks; STANDARD+ for options |
| 10 milliseconds | `10ms` | Extremely granular |
| 100 milliseconds | `100ms` | |
| 500 milliseconds | `500ms` | |
| 1 second | `1s` | Default; SPX reports ~every 1s |
| 5 seconds | `5s` | |
| 10 seconds | `10s` | |
| 15 seconds | `15s` | |
| 30 seconds | `30s` | |
| 1 minute | `1m` | **Recommended for backtesting** |
| 5 minutes | `5m` | Good balance of granularity and data volume |
| 10 minutes | `10m` | |
| 15 minutes | `15m` | |
| 30 minutes | `30m` | |
| 1 hour | `1h` | |
| End of day | EOD endpoint | Separate endpoint, not interval param |

### Greeks/IV Recalculation Frequency

Greeks and IV are **computed per tick** — every quote update triggers a recalculation. When you request with `interval=1m`, you get the Greeks at the last tick before each minute boundary.

This means at 1-minute resolution, you get ~390 Greeks snapshots per trading day per contract. For a full chain with ~500 contracts, that's ~195,000 rows per ticker per day.

---

## Bulk Data Access Strategy

### Approach for Our 11-Ticker Universe

There is no dedicated "bulk download" endpoint. Instead, use the async concurrent request pattern to maximize throughput:

1. **EOD daily chains** are the most efficient for daily backtesting
2. **Multi-day requests** cap at 1 month — batch in monthly chunks
3. **Use `ndjson` format** for direct Polars ingestion
4. **Respect concurrent limits** via `asyncio.Semaphore`

### Estimated Data Volume (Daily EOD Chain Pull)

| Component | Per Ticker | 11 Tickers |
|-----------|-----------|------------|
| EOD Greeks (all strikes, all expiries) | ~2,000-10,000 rows | ~20,000-100,000 rows |
| Open Interest | ~2,000-10,000 rows | ~20,000-100,000 rows |
| Underlying EOD | 1 row | 11 rows |

For 6 months of daily data: ~60M-120M total rows across all tickers.

### Full Pipeline: Daily Underlying + Options Chain

```python
import asyncio
import httpx
import polars as pl
import io
from datetime import date, timedelta

BASE = "http://127.0.0.1:25503/v3"
CONCURRENCY = 4
SEMAPHORE = asyncio.Semaphore(CONCURRENCY)

EQUITY_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META",
                  "NVDA", "TSLA", "NFLX", "PLTR"]
INDEX_TICKERS = ["SPX"]  # NDX unavailable for index data
OPTION_TICKERS = EQUITY_TICKERS + ["SPX", "SPXW", "NDX", "NDXP"]


async def fetch_stock_ohlc(client: httpx.AsyncClient, symbol: str,
                           start: str, end: str) -> pl.DataFrame:
    """Fetch 1-minute OHLC bars for a stock."""
    async with SEMAPHORE:
        params = {
            "symbol": symbol,
            "start_date": start,
            "end_date": end,
            "interval": "1m",
            "format": "ndjson"
        }
        r = await client.get(f"{BASE}/stock/history/ohlc", params=params)
        if r.status_code == 472:
            return pl.DataFrame()
        r.raise_for_status()
        if not r.text.strip():
            return pl.DataFrame()
        df = pl.read_ndjson(io.StringIO(r.text))
        return df.with_columns(pl.lit(symbol).alias("symbol"))


async def fetch_index_ohlc(client: httpx.AsyncClient, symbol: str,
                           start: str, end: str) -> pl.DataFrame:
    """Fetch 1-minute OHLC bars for an index."""
    async with SEMAPHORE:
        params = {
            "symbol": symbol,
            "start_date": start,
            "end_date": end,
            "interval": "1m",
            "format": "ndjson"
        }
        r = await client.get(f"{BASE}/index/history/ohlc", params=params)
        if r.status_code == 472:
            return pl.DataFrame()
        r.raise_for_status()
        if not r.text.strip():
            return pl.DataFrame()
        df = pl.read_ndjson(io.StringIO(r.text))
        return df.with_columns(pl.lit(symbol).alias("symbol"))


async def fetch_option_eod_greeks(client: httpx.AsyncClient, symbol: str,
                                   dt: str) -> pl.DataFrame:
    """Fetch full EOD Greeks chain for one ticker on one date."""
    async with SEMAPHORE:
        params = {
            "symbol": symbol,
            "expiration": "*",
            "start_date": dt,
            "end_date": dt,
            "format": "ndjson"
        }
        r = await client.get(f"{BASE}/option/history/greeks/eod", params=params)
        if r.status_code == 472:
            return pl.DataFrame()
        r.raise_for_status()
        if not r.text.strip():
            return pl.DataFrame()
        return pl.read_ndjson(io.StringIO(r.text))


async def daily_pipeline(dt: str):
    """
    Full daily data pull:
    1. 1-min OHLC for all equities and SPX
    2. EOD options chain with Greeks for all tickers
    """
    async with httpx.AsyncClient(timeout=120.0) as client:
        # Underlying data
        stock_tasks = [fetch_stock_ohlc(client, sym, dt, dt)
                       for sym in EQUITY_TICKERS]
        index_tasks = [fetch_index_ohlc(client, sym, dt, dt)
                       for sym in INDEX_TICKERS]

        # Options chains
        option_tasks = [fetch_option_eod_greeks(client, sym, dt)
                        for sym in OPTION_TICKERS]

        all_results = await asyncio.gather(
            *stock_tasks, *index_tasks, *option_tasks,
            return_exceptions=True
        )

    # Separate results
    n_stocks = len(EQUITY_TICKERS)
    n_indices = len(INDEX_TICKERS)
    n_options = len(OPTION_TICKERS)

    stock_results = [r for r in all_results[:n_stocks]
                     if isinstance(r, pl.DataFrame) and not r.is_empty()]
    index_results = [r for r in all_results[n_stocks:n_stocks+n_indices]
                     if isinstance(r, pl.DataFrame) and not r.is_empty()]
    option_results = [r for r in all_results[n_stocks+n_indices:]
                      if isinstance(r, pl.DataFrame) and not r.is_empty()]

    return {
        "stocks": pl.concat(stock_results) if stock_results else pl.DataFrame(),
        "indices": pl.concat(index_results) if index_results else pl.DataFrame(),
        "options": pl.concat(option_results) if option_results else pl.DataFrame(),
    }


if __name__ == "__main__":
    data = asyncio.run(daily_pipeline("20260330"))
    print(f"Stocks: {data['stocks'].shape}")
    print(f"Indices: {data['indices'].shape}")
    print(f"Options: {data['options'].shape}")
```

---

## Request Sizing Guidelines for Bulk Pulls

| Data Type | Resolution | Max Date Range per Request |
|-----------|------------|---------------------------|
| Stock OHLC | 1-minute | 1 month |
| Stock OHLC | Tick | 1 day |
| Index Price | 1-second | 1 month |
| Index OHLC | 1-minute | 1 month |
| Option EOD/Greeks | EOD | 1 month (request day-by-day for `*` expiration) |
| Option Quotes | 1-minute | 1 month (requires specific expiration for multi-day) |
| Option Quotes | Tick | 1 day (1 week for illiquid) |

### Throughput Optimization Tips

1. **Use `asyncio.Semaphore`** set to your tier's concurrent limit (4 for STANDARD)
2. **Request queue:** Terminal queues up to 16 requests (configurable to 128 in config)
3. **Use `ndjson` format** for fastest parsing with Polars
4. **Streaming reads** for large responses: `httpx.stream("GET", ...)` with `iter_lines()`
5. **Break large date ranges** into daily or weekly chunks
6. **For full chains:** `expiration=*` with `strike=*` returns everything in one request
7. **Filter by DTE:** Use `max_dte` to limit expirations (e.g., `max_dte=90` for <= 90 DTE)
8. **Filter by moneyness:** Use `strike_range` to limit strikes around spot (e.g., `strike_range=20`)

---

## Concurrent Request Patterns

### Using asyncio.Semaphore (Recommended)

```python
import asyncio
import httpx

CONCURRENCY_LIMIT = 4  # STANDARD tier
semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

async def fetch(client: httpx.AsyncClient, url: str, params: dict):
    async with semaphore:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r

async def main():
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = [
            fetch(client, f"{BASE}/stock/history/ohlc",
                  {"symbol": sym, "date": "20260330",
                   "interval": "1m", "format": "ndjson"})
            for sym in ["AAPL", "MSFT", "GOOGL", "AMZN",
                        "META", "NVDA", "TSLA", "NFLX", "PLTR"]
        ]
        results = await asyncio.gather(*tasks)
    return results

results = asyncio.run(main())
```

---

## OPRA Data Details

All options data (equity and index) comes from **OPRA (Options Price Reporting Authority)**, administered by CBOE:

- ThetaData receives every NBBO quote and trade from OPRA in real-time (~3ms latency)
- Most OPRA vendors filter quotes due to volume (millions of quotes in the first second of market open) — ThetaData retains all of them
- OPRA does NOT produce national EOD reports — ThetaData generates consolidated EOD at 17:15 ET
- Open interest is reported by OPRA at ~06:30 ET daily (previous-day figures)

### OPRA vs SIP for Equities

- Options: OPRA (single consolidated feed)
- Equities: CTA (NYSE) + UTP (Nasdaq) — two separate SIPs
- ThetaData gets real-time OPRA; gets 15-min delayed CTA/UTP (real-time Nasdaq Basic only)

---

## Key Limitations & Caveats

| Issue | Impact | Workaround |
|-------|--------|------------|
| NDX index data unavailable | Cannot get NDX price from ThetaData | Use QQQ as proxy or external data source |
| PLTR on CTA tape | Stock history may only go back to 2020 | Verify with `/v3/stock/list/dates/quote?symbol=PLTR` |
| SPY on CTA tape | No stock history before 2020-01-01 | Use SPX index data or external SPY data |
| Multi-day requests capped at 1 month | Must loop for longer periods | Batch in monthly chunks |
| No official Python SDK | Must use raw HTTP | `httpx` + `polars` is the recommended stack |
| Greeks EOD requires day-by-day for `*` exp | Can't do multi-month in one request | Loop over dates, use async concurrency |
| Index data requires paid tier (VALUE+) | FREE tier has zero index access | Budget for at least VALUE subscription |
| Free tier: 30 req/min, 1-day delayed | Insufficient for serious backtesting | Minimum VALUE ($30) or STANDARD ($80) |

---

## Related Notes

- [[ThetaData-Options-API]] — Options chain data, Greeks endpoints, IV surfaces
- [[Breeden-Litzenberger]] — Risk-neutral probability extraction from options prices
- [[Options-Chain-Pipeline]] — Data pipeline architecture
