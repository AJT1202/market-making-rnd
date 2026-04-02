---
title: ThetaData Options API Reference
created: 2026-03-31
updated: 2026-03-31
tags:
  - thetadata
  - options
  - api
  - greeks
  - implied-volatility
  - breeden-litzenberger
  - market-data
---

# ThetaData Options API Reference

Complete reference for retrieving historical and real-time options chain data from ThetaData's REST API (v3). This feeds directly into the [[Breeden-Litzenberger]] probability extraction pipeline.

## Architecture Overview

ThetaData uses a **local terminal architecture**. The Theta Terminal is a Java JAR that runs on your machine, exposing a REST API at `http://127.0.0.1:25503/v3`. All requests go through this local server, which connects to ThetaData's MDDS (Market Data Distribution System) via a proprietary protocol achieving up to 30x bandwidth reduction.

- **Base URL:** `http://127.0.0.1:25503/v3`
- **Requires:** Java 21+, Theta Terminal running, `creds.txt` with credentials
- **Data Source:** OPRA (Options Price Reporting Authority) — the national consolidated feed for all US equity and index options
- **Response Formats:** `csv` (default), `json`, `ndjson`, `html`

## Subscription Tiers — Options Data

| Tier | Price | Granularity | History Start | Concurrent Requests | Delay |
|------|-------|-------------|---------------|---------------------|-------|
| FREE | $0 | EOD | 2023-06-01 | 1 (30 req/min) | 1 day |
| VALUE | ~$30/mo | 1 Minute | 2020-01-01 | 2 | Real-time |
| **STANDARD** | **~$80/mo** | **Tick Level** | **2016-01-01** | **4** | **Real-time** |
| PRO | ~$200/mo | Tick Level | 2012-06-01 | 8 | Real-time |

### Endpoint Access by Tier

| Endpoint | FREE | VALUE | STANDARD | PRO |
|----------|------|-------|----------|-----|
| EOD | Y | Y | Y | Y |
| Quote (NBBO) | Y | Y | Y | Y |
| Open Interest | Y | Y | Y | Y |
| OHLC | Y | Y | Y | Y |
| Trade | - | Y | Y | Y |
| Trade Quote | - | Y | Y | Y |
| Implied Volatility | - | Y | Y | Y |
| Greeks 1st Order (delta, theta, vega, rho) | - | Y | Y | Y |
| Greeks 2nd Order (gamma, vanna, charm, vomma, veta, vera) | - | - | Y | Y |
| Greeks 3rd Order (speed, zomma, color, ultima) | - | - | Y | Y |
| Trade Greeks (all orders) | - | - | Y | Y |
| All Greeks (combined) Snapshot | - | - | - | Y |
| All Greeks (combined) History | - | - | - | Y |

> **For Breeden-Litzenberger:** The VALUE tier ($30/mo) provides IV and first-order Greeks. STANDARD ($80/mo) adds second-order Greeks and tick-level granularity back to 2016. For our pipeline, **STANDARD is recommended** — we get bid/ask IV, delta, gamma, vega, theta at any intraday interval, plus tick-level quote data.

---

## Step 1: Discover Available Contracts

### List Expirations

```python
# GET /v3/option/list/expirations
import httpx

BASE = "http://127.0.0.1:25503/v3"

r = httpx.get(f"{BASE}/option/list/expirations", params={
    "symbol": "AAPL",
    "format": "ndjson"
}, timeout=60)
r.raise_for_status()
# Returns: [{"symbol": "AAPL", "expiration": "2026-04-03"}, ...]
```

### List Strikes for an Expiration

```python
# GET /v3/option/list/strikes
r = httpx.get(f"{BASE}/option/list/strikes", params={
    "symbol": "AAPL",
    "expiration": "20260417",
    "format": "ndjson"
}, timeout=60)
# Returns: [{"symbol": "AAPL", "strike": 150.0}, {"symbol": "AAPL", "strike": 155.0}, ...]
```

### List All Contracts Traded/Quoted on a Date

```python
# GET /v3/option/list/contracts/{request_type}
r = httpx.get(f"{BASE}/option/list/contracts/quote", params={
    "symbol": "AAPL",
    "date": "20260330",
    "format": "ndjson"
}, timeout=60)
# Returns: [{"symbol":"AAPL","expiration":"2026-04-03","strike":200.0,"right":"call"}, ...]
```

| Parameter | Required | Type | Description |
|-----------|----------|------|-------------|
| `request_type` | Yes (path) | string | `trade` or `quote` |
| `symbol` | No | string/array | Filter by underlying (comma-separated for multiple: `AAPL,SPY,AMD`) |
| `date` | Yes | string | Date in `YYYYMMDD` format |
| `max_dte` | No | integer | Filter by maximum days to expiration |

Response fields: `symbol`, `expiration`, `strike`, `right`

### List Available Dates for a Contract

```python
# GET /v3/option/list/dates/{request_type}
r = httpx.get(f"{BASE}/option/list/dates/quote", params={
    "symbol": "AAPL",
    "expiration": "20260417",
    "format": "ndjson"
}, timeout=60)
# Returns: [{"date": "2026-03-20"}, {"date": "2026-03-23"}, ...]
```

---

## Step 2: Retrieve Full Options Chains

### Historical EOD Options Chain (Best for Daily Backtesting)

This is the **most efficient endpoint for daily options chain snapshots**. Theta Data generates a consolidated EOD report at 17:15 ET daily.

```python
# GET /v3/option/history/eod
# Full chain for AAPL on a single date — all strikes, all expirations
r = httpx.get(f"{BASE}/option/history/eod", params={
    "symbol": "AAPL",
    "expiration": "*",        # ALL expirations
    "strike": "*",            # ALL strikes (default)
    "right": "both",          # calls and puts (default)
    "start_date": "20260330",
    "end_date": "20260330",
    "format": "ndjson"
}, timeout=120)
```

**Response fields:**

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | string | Contract identifier |
| `expiration` | date | `YYYY-MM-DD` |
| `strike` | number | Strike price in dollars |
| `right` | string | `call` or `put` |
| `created` | datetime | Report generation time (17:15 ET) |
| `last_trade` | datetime | Final trade timestamp |
| `open` | number | Opening trade price |
| `high` | number | Highest traded price |
| `low` | number | Lowest traded price |
| `close` | number | Closing trade price |
| `volume` | integer | Contracts traded |
| `count` | integer | Number of trades |
| `bid_size` | integer | Last NBBO bid size |
| `bid` | number | Last NBBO bid price |
| `ask_size` | integer | Last NBBO ask size |
| `ask` | number | Last NBBO ask price |
| `bid_exchange` | integer | Exchange code |
| `ask_exchange` | integer | Exchange code |
| `bid_condition` | integer | Condition code |
| `ask_condition` | integer | Condition code |

> **Note:** EOD does NOT include Greeks or IV. You must query the Greeks EOD endpoint separately.

### Historical EOD Greeks (Full Chain with IV + Greeks)

This is the **primary endpoint for Breeden-Litzenberger** — gives closing bid/ask, all Greeks, and IV for every contract.

```python
# GET /v3/option/history/greeks/eod
# Full chain with Greeks for AAPL on a single date
r = httpx.get(f"{BASE}/option/history/greeks/eod", params={
    "symbol": "AAPL",
    "expiration": "*",        # ALL expirations
    "strike": "*",            # ALL strikes
    "right": "both",          # calls and puts
    "start_date": "20260330",
    "end_date": "20260330",
    "format": "ndjson"
}, timeout=120)
```

**Response fields (superset of EOD + Greeks):**

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | string | Contract identifier |
| `expiration` | date | Expiration date |
| `strike` | number | Strike price ($) |
| `right` | string | `call` / `put` |
| `timestamp` | datetime | Report timestamp |
| `open`, `high`, `low`, `close` | number | OHLC prices |
| `volume`, `count` | integer | Trade stats |
| `bid`, `ask` | number | Final NBBO |
| `bid_size`, `ask_size` | integer | NBBO sizes |
| `delta` | number | Delta |
| `gamma` | number | Gamma |
| `theta` | number | Theta |
| `vega` | number | Vega |
| `rho` | number | Rho |
| `lambda` | number | Lambda (leverage ratio) |
| `epsilon` | number | Epsilon |
| `vanna` | number | Vanna (dDelta/dVol) |
| `charm` | number | Charm (dDelta/dTime) |
| `vomma` | number | Vomma (dVega/dVol) |
| `veta` | number | Veta |
| `vera` | number | Vera |
| `speed` | number | Speed (3rd order) |
| `zomma` | number | Zomma (3rd order) |
| `color` | number | Color (3rd order) |
| `ultima` | number | Ultima (3rd order) |
| `d1`, `d2` | number | Black-Scholes d1, d2 |
| `dual_delta`, `dual_gamma` | number | Dual Greeks |
| `implied_vol` | number | Implied volatility |
| `iv_error` | number | IV fitting error ratio |
| `underlying_price` | number | Underlying midpoint |
| `underlying_timestamp` | datetime | Underlying price timestamp |

**Tier access:** First-order Greeks (delta, theta, vega, rho) require VALUE+. Second-order (gamma, vanna, charm, vomma, veta, vera) require STANDARD+. Third-order and All-Greeks require PRO for history, STANDARD for EOD.

### Customizable Greeks Parameters

All Greeks endpoints accept these tuning parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `annual_dividend` | ignored | Annualized expected dividend amount |
| `rate_type` | `sofr` | Interest rate source: `sofr`, `treasury_m1` through `treasury_y30` |
| `rate_value` | auto | Override interest rate (percent) |
| `version` | `latest` | `latest` = real TTE (down to 1hr for 0DTE); `1` = legacy (fixed 0.15 DTE) |
| `underlyer_use_nbbo` | `false` | Use NBBO midpoint instead of last trade for underlying |

---

## Step 3: Intraday Options Data

### Historical Intraday Quotes (NBBO)

Get the NBBO at any interval for any contract or full chain.

```python
# GET /v3/option/history/quote
# 1-minute NBBO snapshots for ALL AAPL options on a given date
r = httpx.get(f"{BASE}/option/history/quote", params={
    "symbol": "AAPL",
    "expiration": "*",       # all expirations
    "strike": "*",           # all strikes
    "right": "both",
    "date": "20260330",
    "interval": "1m",        # 1-minute snapshots
    "start_time": "09:30:00",
    "end_time": "16:00:00",
    "format": "ndjson"
}, timeout=120)
```

**Available intervals:** `tick`, `10ms`, `100ms`, `500ms`, `1s`, `5s`, `10s`, `15s`, `30s`, `1m`, `5m`, `10m`, `15m`, `30m`, `1h`

> **Multi-day limit:** 1 month max per request; must specify expiration for multi-day.

Response fields: `symbol`, `expiration`, `strike`, `right`, `timestamp`, `bid_size`, `bid_exchange`, `bid`, `bid_condition`, `ask_size`, `ask_exchange`, `ask`, `ask_condition`

### At-Time Quote (Point-in-Time Snapshot)

Get the exact NBBO at a specific millisecond — useful for reconstructing exact market state.

```python
# GET /v3/option/at_time/quote
# What was the full AAPL options chain at exactly 10:30:00 ET?
r = httpx.get(f"{BASE}/option/at_time/quote", params={
    "symbol": "AAPL",
    "expiration": "*",
    "strike": "*",
    "right": "both",
    "start_date": "20260330",
    "end_date": "20260330",
    "time_of_day": "10:30:00.000",
    "format": "ndjson"
}, timeout=120)
```

### Historical Intraday IV

```python
# GET /v3/option/history/greeks/implied_volatility
# Bid IV, mid IV, ask IV at 5-minute intervals
r = httpx.get(f"{BASE}/option/history/greeks/implied_volatility", params={
    "symbol": "AAPL",
    "expiration": "20260417",
    "strike": "*",
    "right": "both",
    "date": "20260330",
    "interval": "5m",
    "format": "ndjson"
}, timeout=120)
```

**Response fields (key for Breeden-Litzenberger):**

| Field | Type | Description |
|-------|------|-------------|
| `bid` | number | NBBO bid |
| `bid_implied_vol` | number | IV computed from bid price |
| `midpoint` | number | (bid + ask) / 2 |
| `implied_vol` | number | IV computed from midpoint |
| `ask` | number | NBBO ask |
| `ask_implied_vol` | number | IV computed from ask price |
| `iv_error` | number | Fitting error ratio |
| `underlying_price` | number | Underlying midpoint |

> This endpoint gives bid/ask/mid IV separately, which is critical for estimating IV smile uncertainty in the Breeden-Litzenberger extraction.

### Historical Intraday First-Order Greeks

```python
# GET /v3/option/history/greeks/first_order
r = httpx.get(f"{BASE}/option/history/greeks/first_order", params={
    "symbol": "AAPL",
    "expiration": "20260417",
    "strike": "*",
    "right": "both",
    "date": "20260330",
    "interval": "5m",
    "format": "ndjson"
}, timeout=120)
```

Response includes: `delta`, `theta`, `vega`, `rho`, `epsilon`, `lambda`, `implied_vol`, `iv_error`, `underlying_price`

---

## Step 4: Open Interest

### Historical Open Interest

Open interest is reported once daily by OPRA at ~06:30 ET, representing previous-day EOD figures.

```python
# GET /v3/option/history/open_interest
r = httpx.get(f"{BASE}/option/history/open_interest", params={
    "symbol": "AAPL",
    "expiration": "*",
    "date": "20260330",
    "format": "ndjson"
}, timeout=60)
```

Response: `symbol`, `expiration`, `strike`, `right`, `timestamp`, `open_interest`

### Real-Time Open Interest Snapshot

```python
# GET /v3/option/snapshot/open_interest
r = httpx.get(f"{BASE}/option/snapshot/open_interest", params={
    "symbol": "AAPL",
    "expiration": "*",
    "format": "ndjson"
}, timeout=60)
```

---

## Greeks Calculation Methodology

ThetaData uses the **Black-Scholes model** for all Greeks calculations.

### Key Details

- **IV Method:** Fast bisection method; `iv_error` increases for deep ITM/OTM (expected behavior)
- **Dividends:** Ignored by default; specify `annual_dividend` parameter to include
- **Interest Rate:** SOFR by default (1-day lag); override with `rate_type` or `rate_value`
- **DTE Calculation:** For < 7 DTE, uses quote timestamp for fractional DTE; for >= 7 DTE, uses whole-number DTE. Set `version=1` for legacy (fixed 0.15 DTE on expiration day)
- **Rho and Vega:** Must be divided by 100 to get actual values
- **Model:** Uses European pricing model (EU)
- **Underlying Price:** Uses midpoint at the time of each option tick

---

## SPX and Index Options Specifics

### Index Option Symbology

Index options are split across multiple symbols for different settlement styles:

| Index | AM-Settled (Monthly) | PM-Settled (Weekly) | Quarterly | Other |
|-------|---------------------|---------------------|-----------|-------|
| SPX | `SPX` | `SPXW` | `SPXQ` (pre-2014-07) | `SPXPM` (pre-2018-12) |
| VIX | `VIX` | `VIXW` | - | - |
| RUT | `RUT` | `RUTW` | `RUTQ` | - |
| NDX | `NDX` | - | - | `NDXP` (PM-settled) |
| DJX | `DJIA` | - | - | - |

> **Critical for our pipeline:** When querying SPX options, you must query BOTH `SPX` and `SPXW` to get the complete chain. SPX = AM-settled monthlies; SPXW = PM-settled weeklies.

### Data Availability for Index Options

- **SPX options:** Full OPRA data, Greeks back to 2017-01-01
- **NDX options:** Available through OPRA, but **NDX index price data is NOT available** (Nasdaq Indices Feed unsupported). You can work around this by supplying `under_price` parameter to Greeks endpoints
- **VIX options:** Full OPRA data available
- **RUT/DJX:** Historical coverage between first access date and 2024-07-01

### SPX Extended/Global Trading Hours

SPX, VIX, and XSP options trade outside regular hours:
- **GTH:** 20:15 ET to 09:25 ET (next day)
- **RTH:** 09:30 ET to 16:15 ET
- **ETH data in ThetaData:** Covers 2015-2018, gap from 2019-Dec 2022, full coverage from Jan 2022+

---

## Efficient Full-Chain Retrieval Pattern

### Daily EOD Chain Pull for Backtesting

```python
import asyncio
import httpx
import polars as pl
import io
from datetime import date, timedelta

BASE = "http://127.0.0.1:25503/v3"
CONCURRENCY = 4  # STANDARD tier
SEMAPHORE = asyncio.Semaphore(CONCURRENCY)

TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META",
           "NVDA", "TSLA", "NFLX", "PLTR",
           "SPX", "SPXW"]  # SPX + SPXW for full SPX chain

async def fetch_eod_greeks(client: httpx.AsyncClient, symbol: str, dt: str) -> pl.DataFrame:
    """Fetch full EOD Greeks chain for one ticker on one date."""
    async with SEMAPHORE:
        params = {
            "symbol": symbol,
            "expiration": "*",
            "strike": "*",
            "right": "both",
            "start_date": dt,
            "end_date": dt,
            "format": "ndjson"
        }
        r = await client.get(f"{BASE}/option/history/greeks/eod", params=params)
        if r.status_code == 472:  # NO_DATA
            return pl.DataFrame()
        r.raise_for_status()
        if not r.text.strip():
            return pl.DataFrame()
        return pl.read_ndjson(io.StringIO(r.text))

async def fetch_open_interest(client: httpx.AsyncClient, symbol: str, dt: str) -> pl.DataFrame:
    """Fetch open interest for full chain on one date."""
    async with SEMAPHORE:
        params = {
            "symbol": symbol,
            "expiration": "*",
            "date": dt,
            "format": "ndjson"
        }
        r = await client.get(f"{BASE}/option/history/open_interest", params=params)
        if r.status_code == 472:
            return pl.DataFrame()
        r.raise_for_status()
        if not r.text.strip():
            return pl.DataFrame()
        return pl.read_ndjson(io.StringIO(r.text))

async def pull_daily_chain(dt: str):
    """Pull full options chain with Greeks + OI for all tickers on one date."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = []
        for sym in TICKERS:
            tasks.append(fetch_eod_greeks(client, sym, dt))
            tasks.append(fetch_open_interest(client, sym, dt))

        results = await asyncio.gather(*tasks, return_exceptions=True)

    greeks_frames = []
    oi_frames = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"Error: {result}")
            continue
        if result.is_empty():
            continue
        if i % 2 == 0:
            greeks_frames.append(result)
        else:
            oi_frames.append(result)

    greeks_df = pl.concat(greeks_frames) if greeks_frames else pl.DataFrame()
    oi_df = pl.concat(oi_frames) if oi_frames else pl.DataFrame()
    return greeks_df, oi_df

# Usage
greeks, oi = asyncio.run(pull_daily_chain("20260330"))
```

### Multi-Day Batch Pull

```python
import polars as pl
from datetime import date, timedelta

async def pull_date_range(start: date, end: date):
    """Pull chains for a date range, day by day."""
    all_greeks = []
    all_oi = []
    current = start
    while current <= end:
        dt_str = current.strftime("%Y%m%d")
        print(f"Pulling {dt_str}...")
        try:
            g, o = await pull_daily_chain(dt_str)
            if not g.is_empty():
                all_greeks.append(g)
            if not o.is_empty():
                all_oi.append(o)
        except Exception as e:
            print(f"  Error on {dt_str}: {e}")
        current += timedelta(days=1)

    return (
        pl.concat(all_greeks) if all_greeks else pl.DataFrame(),
        pl.concat(all_oi) if all_oi else pl.DataFrame()
    )
```

---

## Request Limits & Rate Limiting

ThetaData does **not** impose traditional rate limits. Instead, they limit **concurrent outstanding requests**:

| Tier | Max Concurrent Requests | Queue Size |
|------|------------------------|------------|
| FREE | 1 (30 req/min hard cap) | - |
| VALUE | 2 | 16 (configurable to 128) |
| STANDARD | 4 | 16 (configurable to 128) |
| PRO | 8 | 16 (configurable to 128) |

- Requests exceeding concurrent limit are queued (not rejected)
- If queue exceeds configured size, HTTP 429 is returned
- No daily request quota — unlimited total requests per day

### Request Sizing Best Practices

Keep responses under ~1 million ticks:

| Asset Class | Resolution | Max Date Range per Request |
|-------------|------------|---------------------------|
| Options | Tick-level | 1 day (1 week for illiquid) |
| Options | 100ms | 1 week |
| Options | 1s+ | 1 month |
| Options | EOD | 1 month |
| Any | Highly liquid (AAPL/SPX) tick | 1 day |

> Exceeding these ranges may cause out-of-memory errors on the terminal or server side.

### Error Codes

| HTTP Code | Name | Meaning |
|-----------|------|---------|
| 200 | OK | Success |
| 404 | NO_IMPL | Invalid request or outdated terminal |
| 429 | OS_LIMIT | Queue full; retry |
| 472 | NO_DATA | No data exists for query |
| 473 | INVALID_PARAMS | Bad parameters |
| 474 | DISCONNECTED | Lost connection to MDDS |
| 570 | LARGE_REQUEST | Request too large; reduce date range |

---

## Python SDK & Client

There is **no official Python SDK**. ThetaData's API is a pure REST API accessed via HTTP. The recommended Python approach:

- **Library:** `httpx` (supports both sync and async)
- **Data Processing:** `polars` (recommended by ThetaData) or `pandas`
- **Concurrency:** `asyncio` with `asyncio.Semaphore` to respect concurrent request limits

### Installation

```bash
pip install httpx polars
```

### Streaming vs Non-Streaming Reads

```python
# Non-streaming (loads full response into memory)
r = httpx.get(url, params=params, timeout=60)
data = r.text

# Streaming (memory-efficient for large responses)
with httpx.stream("GET", url, params=params, timeout=60) as r:
    r.raise_for_status()
    for line in r.iter_lines():
        process(line)
```

### Format Recommendation

Use `ndjson` format for programmatic access — it parses cleanly into Polars DataFrames:

```python
import polars as pl
import io

r = httpx.get(url, params={**params, "format": "ndjson"}, timeout=60)
df = pl.read_ndjson(io.StringIO(r.text))
```

---

## Data Fields Summary for Breeden-Litzenberger Pipeline

The following fields are available and relevant to our probability extraction:

| Field | Source Endpoint | Tier Required | Notes |
|-------|----------------|---------------|-------|
| `bid` | quote, eod, greeks | FREE+ | NBBO bid |
| `ask` | quote, eod, greeks | FREE+ | NBBO ask |
| midpoint | computed | - | `(bid + ask) / 2` |
| `volume` | eod, ohlc | FREE+ | Daily volume |
| `open_interest` | open_interest | FREE+ | Previous-day OI |
| `implied_vol` | greeks/iv | VALUE+ | Black-Scholes IV from midpoint |
| `bid_implied_vol` | greeks/iv | VALUE+ | IV from bid price |
| `ask_implied_vol` | greeks/iv | VALUE+ | IV from ask price |
| `delta` | greeks/first_order | VALUE+ | |
| `gamma` | greeks/second_order | STANDARD+ | |
| `vega` | greeks/first_order | VALUE+ | Divide by 100 for actual |
| `theta` | greeks/first_order | VALUE+ | |
| `rho` | greeks/first_order | VALUE+ | Divide by 100 for actual |
| `underlying_price` | greeks endpoints | VALUE+ | Underlying midpoint at option tick |
| `iv_error` | greeks endpoints | VALUE+ | Fitting quality metric |
| `d1`, `d2` | greeks/all | PRO | Black-Scholes model values |

---

## Related Notes

- [[ThetaData-Stock-Index-Data]] — Underlying stock/index price data
- [[Breeden-Litzenberger]] — Risk-neutral probability extraction methodology
- [[Options-Chain-Pipeline]] — Data ingestion pipeline architecture
