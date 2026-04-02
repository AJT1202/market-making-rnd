"""
ThetaData Options Chain Downloader
===================================
Downloads historical options data for backtesting the B-L probability pipeline.

Requires: ThetaTerminal running locally (java -jar ThetaTerminalV3.jar)
Data saved as Parquet files in data/thetadata/

Usage:
    # Download EOD Greeks for all tickers on a single date
    python scripts/download_options.py eod --date 2026-04-01

    # Download EOD Greeks for a date range
    python scripts/download_options.py eod --start 2026-03-24 --end 2026-04-01

    # Download intraday IV for a specific ticker/date
    python scripts/download_options.py intraday-iv --ticker NVDA --date 2026-04-01 --interval 5m

    # Download intraday IV for all tickers on a date
    python scripts/download_options.py intraday-iv --date 2026-04-01 --interval 5m

    # Download full intraday quotes (NBBO) for specific ticker/expiry
    python scripts/download_options.py intraday-quotes --ticker NVDA --date 2026-04-01 --expiry 2026-04-17 --interval 1m
"""

import argparse
import asyncio
import io
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

# --- Configuration ---

BASE_URL = "http://127.0.0.1:25503/v3"
CONCURRENCY = 4  # STANDARD tier limit

TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "TSLA", "NFLX", "PLTR",
]
INDEX_TICKERS = ["SPX", "SPXW", "NDX"]
ALL_TICKERS = TICKERS + INDEX_TICKERS

DATA_DIR = Path(__file__).parent.parent / "data" / "thetadata"

# --- HTTP Client ---

async def fetch(client: httpx.AsyncClient, endpoint: str, params: dict, semaphore: asyncio.Semaphore | None = None) -> str | None:
    """Fetch from ThetaData with concurrency control and error handling."""
    sem = semaphore or asyncio.Semaphore(CONCURRENCY)
    async with sem:
        try:
            r = await client.get(f"{BASE_URL}/{endpoint}", params=params, timeout=120.0)
            if r.status_code == 472:  # NO_DATA
                return None
            if r.status_code == 429:  # Queue full - retry
                await asyncio.sleep(2)
                r = await client.get(f"{BASE_URL}/{endpoint}", params=params, timeout=120.0)
            r.raise_for_status()
            text = r.text.strip()
            return text if text else None
        except httpx.ReadTimeout:
            print(f"  TIMEOUT: {endpoint} {params.get('symbol', '')} - retrying...")
            await asyncio.sleep(3)
            try:
                r = await client.get(f"{BASE_URL}/{endpoint}", params=params, timeout=180.0)
                r.raise_for_status()
                return r.text.strip() or None
            except Exception as e:
                print(f"  FAILED: {endpoint} {params.get('symbol', '')} - {e}")
                return None
        except Exception as e:
            print(f"  ERROR: {endpoint} {params.get('symbol', '')} - {e}")
            return None


def ndjson_to_df(text: str) -> pl.DataFrame:
    """Parse ndjson text into a Polars DataFrame."""
    if not text:
        return pl.DataFrame()
    try:
        return pl.read_ndjson(io.StringIO(text))
    except Exception:
        # Fallback: parse line by line
        records = []
        for line in text.strip().split("\n"):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return pl.DataFrame(records) if records else pl.DataFrame()


# --- EOD Greeks Download ---

async def download_eod_greeks(dt: str, tickers: list[str] | None = None) -> pl.DataFrame:
    """Download full EOD Greeks chain for all tickers on a given date."""
    tickers = tickers or ALL_TICKERS
    dt_fmt = dt.replace("-", "")

    print(f"\n📡 Downloading EOD Greeks for {dt} ({len(tickers)} tickers)...")

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = []
        task_labels = []
        for sym in tickers:
            params = {
                "symbol": sym,
                "expiration": "*",
                "strike": "*",
                "right": "both",
                "start_date": dt_fmt,
                "end_date": dt_fmt,
                "format": "ndjson",
            }
            tasks.append(fetch(client, "option/history/greeks/eod", params, sem))
            task_labels.append(sym)

        results = await asyncio.gather(*tasks)

    frames = []
    for sym, result in zip(task_labels, results):
        if result:
            df = ndjson_to_df(result)
            if not df.is_empty():
                n_strikes = df["strike"].n_unique()
                n_exps = df["expiration"].n_unique()
                print(f"  ✓ {sym}: {len(df)} contracts ({n_strikes} strikes, {n_exps} expiries)")
                frames.append(df)
            else:
                print(f"  - {sym}: empty response")
        else:
            print(f"  - {sym}: no data")

    if not frames:
        print("No data retrieved.")
        return pl.DataFrame()

    combined = pl.concat(frames, how="diagonal_relaxed")
    print(f"\nTotal: {len(combined)} contracts across {len(frames)} tickers")
    return combined


async def download_eod_oi(dt: str, tickers: list[str] | None = None) -> pl.DataFrame:
    """Download open interest for all tickers on a given date."""
    tickers = tickers or ALL_TICKERS
    dt_fmt = dt.replace("-", "")

    print(f"📡 Downloading Open Interest for {dt}...")

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = []
        task_labels = []
        for sym in tickers:
            params = {
                "symbol": sym,
                "expiration": "*",
                "date": dt_fmt,
                "format": "ndjson",
            }
            tasks.append(fetch(client, "option/history/open_interest", params, sem))
            task_labels.append(sym)

        results = await asyncio.gather(*tasks)

    frames = []
    for sym, result in zip(task_labels, results):
        if result:
            df = ndjson_to_df(result)
            if not df.is_empty():
                frames.append(df)

    if not frames:
        return pl.DataFrame()

    combined = pl.concat(frames, how="diagonal_relaxed")
    print(f"  OI: {len(combined)} records")
    return combined


# --- Intraday IV Download ---

async def download_intraday_iv(
    dt: str,
    tickers: list[str] | None = None,
    interval: str = "5m",
    max_dte: int = 30,
) -> pl.DataFrame:
    """Download intraday IV for all tickers on a given date.

    Fetches IV for all strikes/expiries within max_dte days to expiry.
    """
    tickers = tickers or ALL_TICKERS
    dt_fmt = dt.replace("-", "")

    print(f"\n📡 Downloading intraday IV ({interval}) for {dt} ({len(tickers)} tickers, max_dte={max_dte})...")

    # First discover which expiries are relevant for each ticker
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=120.0) as client:
        # Get expirations for each ticker
        exp_tasks = []
        for sym in tickers:
            params = {"symbol": sym, "format": "ndjson"}
            exp_tasks.append(fetch(client, "option/list/expirations", params, sem))

        exp_results = await asyncio.gather(*exp_tasks)

    # Parse expirations and filter by max_dte
    target_date = datetime.strptime(dt, "%Y-%m-%d").date()
    ticker_expiries: dict[str, list[str]] = {}
    for sym, result in zip(tickers, exp_results):
        if not result:
            continue
        df = ndjson_to_df(result)
        if df.is_empty():
            continue
        expiries = df["expiration"].to_list()
        # Filter to expiries within max_dte
        relevant = []
        for exp_str in expiries:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                dte = (exp_date - target_date).days
                if 0 <= dte <= max_dte:
                    relevant.append(exp_str.replace("-", ""))
            except ValueError:
                continue
        if relevant:
            ticker_expiries[sym] = relevant
            print(f"  {sym}: {len(relevant)} expiries within {max_dte} DTE")

    # Now fetch IV for each ticker/expiry pair
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = []
        task_labels = []
        for sym, expiries in ticker_expiries.items():
            for exp in expiries:
                params = {
                    "symbol": sym,
                    "expiration": exp,
                    "strike": "*",
                    "right": "both",
                    "date": dt_fmt,
                    "interval": interval,
                    "start_time": "09:30:00",
                    "end_time": "16:00:00",
                    "format": "ndjson",
                }
                tasks.append(fetch(client, "option/history/greeks/implied_volatility", params, sem))
                task_labels.append(f"{sym}:{exp}")

        print(f"  Fetching {len(tasks)} ticker/expiry combinations...")
        results = await asyncio.gather(*tasks)

    frames = []
    for label, result in zip(task_labels, results):
        if result:
            df = ndjson_to_df(result)
            if not df.is_empty():
                frames.append(df)

    if not frames:
        print("No intraday IV data retrieved.")
        return pl.DataFrame()

    combined = pl.concat(frames, how="diagonal_relaxed")
    print(f"Total: {len(combined)} IV snapshots across {len(frames)} ticker/expiry combinations")
    return combined


# --- Intraday Quotes Download ---

async def download_intraday_quotes(
    dt: str,
    ticker: str,
    expiry: str,
    interval: str = "1m",
) -> pl.DataFrame:
    """Download intraday NBBO quotes for a specific ticker/expiry."""
    dt_fmt = dt.replace("-", "")
    exp_fmt = expiry.replace("-", "")

    print(f"\n📡 Downloading intraday quotes ({interval}) for {ticker} exp={expiry} on {dt}...")

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=120.0) as client:
        params = {
            "symbol": ticker,
            "expiration": exp_fmt,
            "strike": "*",
            "right": "both",
            "date": dt_fmt,
            "interval": interval,
            "start_time": "09:30:00",
            "end_time": "16:00:00",
            "format": "ndjson",
        }
        result = await fetch(client, "option/history/quote", params, sem)

    if not result:
        print("No data retrieved.")
        return pl.DataFrame()

    df = ndjson_to_df(result)
    print(f"Total: {len(df)} quote snapshots")
    return df


# --- Save Helpers ---

def save_parquet(df: pl.DataFrame, path: Path, label: str):
    """Save DataFrame as Parquet with compression."""
    if df.is_empty():
        print(f"  Skipping {label} (empty)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path, compression="zstd")
    size_mb = path.stat().st_size / (1024 * 1024)
    print(f"  💾 {label}: {path.name} ({len(df)} rows, {size_mb:.1f} MB)")


# --- CLI ---

def cmd_eod(args):
    """Download EOD Greeks + OI for date range."""
    start = datetime.strptime(args.start or args.date, "%Y-%m-%d").date()
    end = datetime.strptime(args.end or args.date, "%Y-%m-%d").date()
    tickers = args.ticker.split(",") if args.ticker else None

    current = start
    while current <= end:
        dt_str = current.strftime("%Y-%m-%d")
        dt_compact = current.strftime("%Y%m%d")

        # Skip weekends
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        out_dir = DATA_DIR / "eod" / dt_compact

        # Download Greeks
        greeks = asyncio.run(download_eod_greeks(dt_str, tickers))
        if not greeks.is_empty():
            save_parquet(greeks, out_dir / f"greeks_{dt_compact}.parquet", f"Greeks {dt_str}")

        # Download OI
        if not args.skip_oi:
            oi = asyncio.run(download_eod_oi(dt_str, tickers))
            if not oi.is_empty():
                save_parquet(oi, out_dir / f"oi_{dt_compact}.parquet", f"OI {dt_str}")

        current += timedelta(days=1)


def cmd_intraday_iv(args):
    """Download intraday IV for date."""
    tickers = args.ticker.split(",") if args.ticker else None

    dt_str = args.date
    dt_compact = dt_str.replace("-", "")
    out_dir = DATA_DIR / "intraday_iv" / dt_compact

    df = asyncio.run(download_intraday_iv(
        dt_str,
        tickers=tickers,
        interval=args.interval,
        max_dte=args.max_dte,
    ))
    if not df.is_empty():
        save_parquet(df, out_dir / f"iv_{args.interval}_{dt_compact}.parquet", f"IV {dt_str}")


def cmd_intraday_quotes(args):
    """Download intraday quotes for specific ticker/expiry."""
    dt_str = args.date
    dt_compact = dt_str.replace("-", "")
    out_dir = DATA_DIR / "intraday_quotes" / dt_compact

    df = asyncio.run(download_intraday_quotes(
        dt_str,
        ticker=args.ticker,
        expiry=args.expiry,
        interval=args.interval,
    ))
    if not df.is_empty():
        fname = f"quotes_{args.ticker}_{args.expiry.replace('-','')}_{args.interval}_{dt_compact}.parquet"
        save_parquet(df, out_dir / fname, f"Quotes {args.ticker} {dt_str}")


def main():
    parser = argparse.ArgumentParser(description="ThetaData Options Downloader")
    sub = parser.add_subparsers(dest="command", required=True)

    # EOD
    p_eod = sub.add_parser("eod", help="Download EOD Greeks + OI")
    p_eod.add_argument("--date", help="Single date (YYYY-MM-DD)")
    p_eod.add_argument("--start", help="Start date for range")
    p_eod.add_argument("--end", help="End date for range")
    p_eod.add_argument("--ticker", help="Comma-separated tickers (default: all)")
    p_eod.add_argument("--skip-oi", action="store_true", help="Skip open interest")
    p_eod.set_defaults(func=cmd_eod)

    # Intraday IV
    p_iv = sub.add_parser("intraday-iv", help="Download intraday IV snapshots")
    p_iv.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    p_iv.add_argument("--ticker", help="Comma-separated tickers (default: all)")
    p_iv.add_argument("--interval", default="5m", help="Interval: 1m, 5m, 15m (default: 5m)")
    p_iv.add_argument("--max-dte", type=int, default=30, help="Max days to expiry (default: 30)")
    p_iv.set_defaults(func=cmd_intraday_iv)

    # Intraday Quotes
    p_q = sub.add_parser("intraday-quotes", help="Download intraday NBBO quotes")
    p_q.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")
    p_q.add_argument("--ticker", required=True, help="Single ticker")
    p_q.add_argument("--expiry", required=True, help="Expiration date (YYYY-MM-DD)")
    p_q.add_argument("--interval", default="1m", help="Interval (default: 1m)")
    p_q.set_defaults(func=cmd_intraday_quotes)

    args = parser.parse_args()

    if args.command == "eod" and not args.date and not args.start:
        parser.error("eod requires --date or --start/--end")

    args.func(args)


if __name__ == "__main__":
    main()
