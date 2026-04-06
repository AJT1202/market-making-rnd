"""
ThetaData Options & Stock Data Downloader
==========================================
Downloads historical options and stock data for backtesting the B-L probability
pipeline and Polymarket market making strategies.

Requires: ThetaTerminal running locally (java -jar ThetaTerminalV3.jar)
Config:   config.toml at project root (data_dir, base_url, concurrency)

Usage:
    # Download EOD Greeks for all tickers on a date range
    python scripts/download_options.py eod --start 2026-03-02 --end 2026-04-01

    # Download open interest
    python scripts/download_options.py eod --date 2026-04-01 --skip-oi

    # Download tick-level NBBO quotes (smart-filtered by EOD data)
    python scripts/download_options.py tick-quotes --date 2026-03-30
    python scripts/download_options.py tick-quotes --start 2026-03-02 --end 2026-04-01 --ticker NVDA

    # Download trade-quote (every trade paired with NBBO at execution)
    python scripts/download_options.py trade-quote --date 2026-03-30

    # Download 1-minute stock/index OHLC bars
    python scripts/download_options.py stock-ohlc --start 2026-03-02 --end 2026-04-01

    # Show what's been downloaded (manifest)
    python scripts/download_options.py status
"""

import argparse
import asyncio
import io
import json
import os
import signal
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import polars as pl

try:
    import tomli
except ImportError:
    import tomllib as tomli  # type: ignore[import-not-found]


# ─── Configuration ────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # market-making-rnd/
CONFIG_PATH = PROJECT_ROOT / "config.toml"

EQUITY_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "TSLA", "NFLX", "PLTR",
]
# SPX + SPXW for options; NDX excluded (no index price data)
INDEX_OPTION_TICKERS = ["SPX", "SPXW"]
ALL_OPTION_TICKERS = EQUITY_TICKERS + INDEX_OPTION_TICKERS

# Tickers with underlying price series (stocks + SPX index)
# SPXW uses SPX index price; NDX excluded
OHLC_STOCK_TICKERS = list(EQUITY_TICKERS)  # copy
OHLC_INDEX_TICKERS = ["SPX"]


def load_config() -> dict:
    """Load config.toml from project root."""
    if not CONFIG_PATH.exists():
        print(f"ERROR: config.toml not found at {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "rb") as f:
        return tomli.load(f)


def get_data_dir(config: dict) -> Path:
    data_dir = config.get("paths", {}).get("data_dir")
    if not data_dir:
        print("ERROR: [paths] data_dir not set in config.toml")
        sys.exit(1)
    return Path(data_dir)


def get_base_url(config: dict) -> str:
    return config.get("thetadata", {}).get("base_url", "http://127.0.0.1:25503/v3")


def get_concurrency(config: dict) -> int:
    return config.get("thetadata", {}).get("concurrency", 4)


def theta_dir(data_dir: Path) -> Path:
    return data_dir / "thetadata"


# ─── Manifest ──────────────────────────────────────────────────────���──────────

def manifest_path(data_dir: Path) -> Path:
    return theta_dir(data_dir) / "manifest.json"


def load_manifest(data_dir: Path) -> dict:
    mp = manifest_path(data_dir)
    if mp.exists():
        with open(mp) as f:
            return json.load(f)
    return {"downloads": [], "updated_at": None}


def save_manifest(data_dir: Path, manifest: dict):
    """Save download manifest to disk (atomic write to prevent corruption)."""
    mp = manifest_path(data_dir)
    mp.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Write to temp file first, then atomic rename — prevents corruption
    # if the process is killed mid-write.
    fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=mp.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(manifest, f, indent=2, default=str)
        os.replace(tmp, mp)  # atomic on both Windows and Unix
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _build_manifest_index(manifest: dict) -> set[str]:
    """Build a set index for O(1) manifest lookups."""
    return {
        f"{e['command']}|{e['ticker']}|{e['date']}|{e.get('expiry', '')}"
        for e in manifest.get("downloads", [])
        if e.get("status") == "complete"
    }


def manifest_has(manifest: dict, command: str, ticker: str, dt: str,
                 expiry: str | None = None,
                 _index: set | None = None) -> bool:
    """Check if a download is already recorded as complete in the manifest."""
    if _index is not None:
        return f"{command}|{ticker}|{dt}|{expiry or ''}" in _index
    # fallback linear scan
    for entry in manifest.get("downloads", []):
        if (entry.get("command") == command
                and entry.get("ticker") == ticker
                and entry.get("date") == dt
                and entry.get("status") == "complete"):
            if expiry is None or entry.get("expiry") == expiry:
                return True
    return False


def manifest_add(manifest: dict, command: str, ticker: str, dt: str,
                 rows: int, file_path: str, expiry: str | None = None,
                 _index: set | None = None):
    """Record a completed download in the manifest (deduplicates)."""
    # Remove existing entry for same key (dedup)
    manifest["downloads"] = [
        e for e in manifest["downloads"]
        if not (e.get("command") == command and e.get("ticker") == ticker
                and e.get("date") == dt and e.get("expiry") == expiry)
    ]
    manifest["downloads"].append({
        "command": command,
        "ticker": ticker,
        "date": dt,
        "expiry": expiry,
        "status": "complete",
        "rows": rows,
        "file_path": file_path,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    })
    if _index is not None:
        _index.add(f"{command}|{ticker}|{dt}|{expiry or ''}")


# ─── HTTP Client ───────────────────────────────────────────────────────────��──

async def fetch(client: httpx.AsyncClient, base_url: str, endpoint: str,
                params: dict, semaphore: asyncio.Semaphore) -> bytes | None:
    """Fetch from ThetaData with concurrency control and retry."""
    async with semaphore:
        url = f"{base_url}/{endpoint}"
        for attempt in range(3):
            try:
                r = await client.get(url, params=params, timeout=180.0)
                if r.status_code == 472:  # NO_DATA
                    return None
                if r.status_code == 570:  # LARGE_REQUEST
                    print(f"    LARGE_REQUEST (570): {endpoint} "
                          f"{params.get('symbol', '')} — split into smaller ranges")
                    return None
                if r.status_code in (471, 473, 476):  # Non-retriable
                    label = {471: "PERMISSION", 473: "INVALID_PARAMS",
                             476: "WRONG_IP"}[r.status_code]
                    print(f"    {label} ({r.status_code}): {endpoint} "
                          f"{params.get('symbol', '')}")
                    return None
                if r.status_code == 429:  # OS_LIMIT
                    wait = 2 * (attempt + 1)
                    print(f"    OS_LIMIT (429), waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                if r.status_code == 478:  # Invalid session
                    print(f"    Session invalid (478) — restart ThetaData terminal")
                    return None
                if r.status_code == 474:  # Disconnected
                    print(f"    Disconnected (474), retrying in 5s...")
                    await asyncio.sleep(5)
                    continue
                r.raise_for_status()
                content = r.content
                return content if content.strip() else None
            except httpx.ReadTimeout:
                if attempt < 2:
                    print(f"    Timeout (attempt {attempt+1}/3), retrying...")
                    await asyncio.sleep(3 * (attempt + 1))
                else:
                    print(f"    TIMEOUT: {endpoint} {params.get('symbol', '')}")
                    return None
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2)
                else:
                    print(f"    ERROR: {endpoint} {params.get('symbol', '')} - {e}")
                    return None
    return None


def ndjson_to_df(data: bytes) -> pl.DataFrame:
    """Parse ndjson bytes into a Polars DataFrame."""
    if not data:
        return pl.DataFrame()
    try:
        return pl.read_ndjson(io.BytesIO(data))
    except Exception:
        skipped = 0
        records = []
        for line in data.strip().split(b"\n"):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    skipped += 1
                    continue
        if skipped:
            print(f"    WARNING: skipped {skipped} malformed ndjson lines")
        return pl.DataFrame(records) if records else pl.DataFrame()


def save_parquet(df: pl.DataFrame, path: Path, label: str,
                 compression: str = "zstd"):
    """Save DataFrame as Parquet."""
    if df.is_empty():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path, compression=compression)
    size_mb = path.stat().st_size / (1024 * 1024)
    print(f"    Saved {label}: {path.name} ({len(df)} rows, {size_mb:.1f} MB)")


# ─── Date Helpers ─────────────────────────────────────────────────────────────

def parse_date_args(args) -> tuple[date, date]:
    """Parse --date or --start/--end into (start, end) dates."""
    if hasattr(args, 'date') and args.date:
        d = datetime.strptime(args.date, "%Y-%m-%d").date()
        return d, d
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end or args.start, "%Y-%m-%d").date()
    return start, end


NYSE_HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26),
    date(2026, 12, 25),
}


def trading_days(start: date, end: date):
    """Yield trading days (skip weekends and NYSE holidays) in [start, end]."""
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in NYSE_HOLIDAYS_2026:
            yield current
        current += timedelta(days=1)


def fmt_compact(d: date) -> str:
    return d.strftime("%Y%m%d")


def fmt_iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


# ─── Smart Filtering ───────────────────────────────���─────────────────────────

_greeks_cache: dict[str, pl.DataFrame | None] = {}


def _read_greeks(path: Path) -> pl.DataFrame | None:
    key = str(path)
    if key not in _greeks_cache:
        if not path.exists():
            _greeks_cache[key] = None
        else:
            try:
                _greeks_cache[key] = pl.read_parquet(path)
            except Exception:
                _greeks_cache[key] = None
    return _greeks_cache[key]


def get_relevant_expiries(eod_dir: Path, ticker: str, dt: date,
                          max_dte: int = 30,
                          strike_pct: float = 0.20) -> list[str]:
    """
    Determine which expiration dates to download tick data for, using
    EOD Greeks data for the smart filtering algorithm.

    Returns list of expiry dates as YYYYMMDD strings.
    """
    dt_compact = fmt_compact(dt)
    greeks_path = eod_dir / dt_compact / "greeks.parquet"

    df = _read_greeks(greeks_path)
    if df is None:
        return []

    # Filter to this ticker
    if "symbol" in df.columns:
        df = df.filter(pl.col("symbol") == ticker)
    if df.is_empty():
        return []

    # Get underlying price from any row
    if "underlying_price" not in df.columns:
        return []
    spot = df["underlying_price"].drop_nulls().first()
    if spot is None or spot <= 0:
        return []

    # Filter strikes within strike_pct of spot
    strike_lo = spot * (1 - strike_pct)
    strike_hi = spot * (1 + strike_pct)
    df = df.filter(
        (pl.col("strike") >= strike_lo) & (pl.col("strike") <= strike_hi)
    )

    # Filter expiries within max_dte
    if "expiration" not in df.columns:
        return []

    expiries = df["expiration"].unique().to_list()
    relevant = []
    for exp_str in expiries:
        try:
            if isinstance(exp_str, str):
                exp_date = datetime.strptime(
                    exp_str.replace("-", ""), "%Y%m%d"
                ).date()
            elif isinstance(exp_str, date):
                exp_date = exp_str
            else:
                continue
            dte = (exp_date - dt).days
            if 0 <= dte <= max_dte:
                relevant.append(exp_date.strftime("%Y%m%d"))
        except (ValueError, TypeError):
            continue

    return sorted(set(relevant))


# ─── EOD Greeks & OI ──────────────────────────────────────────────────��──────

async def download_eod_greeks(client: httpx.AsyncClient, base_url: str,
                              dt: str, tickers: list[str],
                              sem: asyncio.Semaphore) -> pl.DataFrame:
    """Download full EOD Greeks chain for all tickers on a given date."""
    dt_fmt = dt.replace("-", "")
    tasks = []
    task_labels = []
    for sym in tickers:
        params = {
            "symbol": sym, "expiration": "*", "strike": "*",
            "right": "both", "start_date": dt_fmt, "end_date": dt_fmt,
            "format": "ndjson",
        }
        tasks.append(fetch(client, base_url, "option/history/greeks/eod",
                           params, sem))
        task_labels.append(sym)
    results = await asyncio.gather(*tasks)

    frames = []
    for sym, result in zip(task_labels, results):
        if result:
            df = ndjson_to_df(result)
            if not df.is_empty():
                n_strikes = df["strike"].n_unique()
                n_exps = df["expiration"].n_unique()
                print(f"    {sym}: {len(df)} contracts "
                      f"({n_strikes} strikes, {n_exps} expiries)")
                frames.append(df)
            else:
                print(f"    {sym}: empty response")
        else:
            print(f"    {sym}: no data")

    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")


async def download_eod_oi(client: httpx.AsyncClient, base_url: str,
                          dt: str, tickers: list[str],
                          sem: asyncio.Semaphore) -> pl.DataFrame:
    """Download open interest for all tickers on a given date."""
    dt_fmt = dt.replace("-", "")
    tasks = []
    task_labels = []
    for sym in tickers:
        params = {
            "symbol": sym, "expiration": "*", "date": dt_fmt,
            "format": "ndjson",
        }
        tasks.append(fetch(client, base_url,
                           "option/history/open_interest", params, sem))
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
    return pl.concat(frames, how="diagonal_relaxed")


# ─── Tick-Level NBBO Quotes ─────────────────────────────────���────────────────

def estimate_strike_range(spot: float, pct: float = 0.20,
                          ticker: str = "") -> int:
    """Estimate strike_range param (# strikes above/below spot) for server-side filtering."""
    dollar_range = spot * pct
    if ticker in ("SPX", "SPXW"):
        interval = 25
    elif spot > 500:
        interval = 10
    elif spot > 100:
        interval = 5
    elif spot > 50:
        interval = 2.5
    else:
        interval = 1
    return max(10, int(dollar_range / interval) + 5)


async def download_tick_iv_for_expiry(
    client: httpx.AsyncClient, base_url: str,
    ticker: str, expiry: str, dt: str,
    sem: asyncio.Semaphore,
    spot: float | None = None,
    strike_pct: float = 0.20,
) -> pl.DataFrame:
    """
    Download tick-level IV + NBBO for a single (ticker, expiry, date).

    Uses /option/history/greeks/implied_volatility which returns:
    bid, ask, bid_implied_vol, ask_implied_vol, implied_vol,
    underlying_price, midpoint, iv_error — all fields the B-L pipeline needs.
    """
    dt_fmt = dt.replace("-", "")
    params = {
        "symbol": ticker,
        "expiration": expiry,
        "right": "both",
        "date": dt_fmt,
        "interval": "1m",  # 1m matches tick-level B-L accuracy (tested)
        "start_time": "09:30:00",
        "end_time": "16:15:00",  # 15 min after close — captures settlement prints
        "format": "ndjson",
    }
    # Server-side strike filtering to prevent OOM
    if spot and spot > 0:
        params["strike_range"] = estimate_strike_range(spot, strike_pct, ticker)
    else:
        params["strike"] = "*"

    result = await fetch(client, base_url,
                         "option/history/greeks/implied_volatility",
                         params, sem)
    if not result:
        return pl.DataFrame()
    return ndjson_to_df(result)


def process_tick_quotes(df: pl.DataFrame, spot: float | None,
                        strike_pct: float = 0.20) -> pl.DataFrame:
    """Post-process tick IV data: filter strikes, sort, add timestamp_us.

    Expected columns from IV endpoint: symbol, expiration, strike, right,
    timestamp, bid, ask, bid_implied_vol, ask_implied_vol, implied_vol,
    underlying_price, midpoint, iv_error
    """
    if df.is_empty():
        return df

    # Filter strikes within range if we have spot price
    if spot and "strike" in df.columns:
        strike_lo = spot * (1 - strike_pct)
        strike_hi = spot * (1 + strike_pct)
        df = df.filter(
            (pl.col("strike") >= strike_lo) & (pl.col("strike") <= strike_hi)
        )

    # Convert timestamp to int64 microseconds UTC
    if "timestamp" in df.columns:
        df = df.with_columns(
            pl.col("timestamp")
            .str.to_datetime("%Y-%m-%dT%H:%M:%S%.f")
            .dt.epoch("us")
            .alias("timestamp_us")
        )
        df = df.sort("timestamp_us")

    return df


def get_spot_from_eod(eod_dir: Path, ticker: str, dt: date) -> float | None:
    """Get spot price from EOD Greeks for a ticker on a date."""
    greeks_path = eod_dir / fmt_compact(dt) / "greeks.parquet"
    df = _read_greeks(greeks_path)
    if df is None:
        return None
    try:
        if "symbol" in df.columns:
            df = df.filter(pl.col("symbol") == ticker)
        if "underlying_price" in df.columns:
            return df["underlying_price"].drop_nulls().first()
    except Exception:
        pass
    return None


# ─── Trade-Quote ──────────────────────────────────────────────────────────────

async def download_trade_quote_for_expiry(
    client: httpx.AsyncClient, base_url: str,
    ticker: str, expiry: str, dt: str,
    sem: asyncio.Semaphore,
    spot: float | None = None,
    strike_pct: float = 0.20,
) -> pl.DataFrame:
    """Download trade-quote data for a single (ticker, expiry, date)."""
    dt_fmt = dt.replace("-", "")
    params = {
        "symbol": ticker,
        "expiration": expiry,
        "right": "both",
        "date": dt_fmt,
        "start_time": "09:30:00",
        "end_time": "16:15:00",  # 15 min after close — captures settlement prints
        "format": "ndjson",
    }
    if spot and spot > 0:
        params["strike_range"] = estimate_strike_range(spot, strike_pct, ticker)
    else:
        params["strike"] = "*"

    result = await fetch(client, base_url, "option/history/trade_quote",
                         params, sem)
    if not result:
        return pl.DataFrame()
    return ndjson_to_df(result)


def process_trade_quote(df: pl.DataFrame, spot: float | None,
                        strike_pct: float = 0.20) -> pl.DataFrame:
    """Post-process trade-quote data."""
    if df.is_empty():
        return df

    if spot and "strike" in df.columns:
        strike_lo = spot * (1 - strike_pct)
        strike_hi = spot * (1 + strike_pct)
        df = df.filter(
            (pl.col("strike") >= strike_lo) & (pl.col("strike") <= strike_hi)
        )

    # Convert timestamps to int64 microseconds UTC
    for ts_col, us_col in [("trade_timestamp", "trade_timestamp_us"),
                           ("quote_timestamp", "quote_timestamp_us")]:
        if ts_col in df.columns:
            df = df.with_columns(
                pl.col(ts_col)
                .str.to_datetime("%Y-%m-%dT%H:%M:%S%.f")
                .dt.epoch("us")
                .alias(us_col)
            )

    if "trade_timestamp_us" in df.columns:
        df = df.sort("trade_timestamp_us")

    return df


# ─── Stock / Index OHLC ──────────────────────────────────────────────────────

async def download_stock_ohlc(client: httpx.AsyncClient, base_url: str,
                              ticker: str, start_dt: str, end_dt: str,
                              sem: asyncio.Semaphore,
                              is_index: bool = False) -> pl.DataFrame:
    """Download 1-minute OHLC bars for a stock or index."""
    endpoint = "index/history/ohlc" if is_index else "stock/history/ohlc"
    start_fmt = start_dt.replace("-", "")
    end_fmt = end_dt.replace("-", "")

    params = {
        "symbol": ticker,
        "start_date": start_fmt,
        "end_date": end_fmt,
        "interval": "1m",
        "start_time": "09:30:00",
        "end_time": "16:15:00",  # 15 min after close — captures settlement prints
        "venue": "utp_cta",
        "format": "ndjson",
    }
    result = await fetch(client, base_url, endpoint, params, sem)

    if not result:
        return pl.DataFrame()

    df = ndjson_to_df(result)
    if df.is_empty():
        return df

    # Add ticker column and timestamp_us
    df = df.with_columns(pl.lit(ticker).alias("symbol"))
    if "timestamp" in df.columns:
        df = df.with_columns(
            pl.col("timestamp")
            .str.to_datetime("%Y-%m-%dT%H:%M:%S%.f")
            .dt.epoch("us")
            .alias("timestamp_us")
        )
        df = df.sort("timestamp_us")

    return df


# ─── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_eod(args):
    """Download EOD Greeks + OI for date range."""
    config = load_config()
    data_dir = get_data_dir(config)
    base_url = get_base_url(config)
    concurrency = get_concurrency(config)
    start, end = parse_date_args(args)
    tickers = args.ticker.split(",") if args.ticker else ALL_OPTION_TICKERS
    skip_oi = args.skip_oi

    async def _run():
        async with httpx.AsyncClient(timeout=300.0) as client:
            manifest = load_manifest(data_dir)
            manifest_idx = _build_manifest_index(manifest)
            eod_dir = theta_dir(data_dir) / "eod"
            sem = asyncio.Semaphore(concurrency)

            for dt in trading_days(start, end):
                dt_str = fmt_iso(dt)
                dt_compact = fmt_compact(dt)

                if manifest_has(manifest, "eod-greeks", "ALL", dt_str,
                                _index=manifest_idx):
                    print(f"  [{dt_str}] EOD Greeks: already downloaded, skipping")
                else:
                    print(f"\n[{dt_str}] Downloading EOD Greeks...")
                    greeks = await download_eod_greeks(
                        client, base_url, dt_str, tickers, sem)
                    if not greeks.is_empty():
                        out = eod_dir / dt_compact / "greeks.parquet"
                        save_parquet(greeks, out, f"Greeks {dt_str}")
                        manifest_add(manifest, "eod-greeks", "ALL", dt_str,
                                     len(greeks), str(out),
                                     _index=manifest_idx)

                if not skip_oi:
                    if manifest_has(manifest, "eod-oi", "ALL", dt_str,
                                    _index=manifest_idx):
                        print(f"  [{dt_str}] OI: already downloaded, skipping")
                    else:
                        print(f"  [{dt_str}] Downloading Open Interest...")
                        oi = await download_eod_oi(
                            client, base_url, dt_str, tickers, sem)
                        if not oi.is_empty():
                            out = eod_dir / dt_compact / "oi.parquet"
                            save_parquet(oi, out, f"OI {dt_str}")
                            manifest_add(manifest, "eod-oi", "ALL", dt_str,
                                         len(oi), str(out),
                                         _index=manifest_idx)

                save_manifest(data_dir, manifest)

            print("\nEOD download complete.")

    asyncio.run(_run())


def cmd_tick_quotes(args):
    """Download tick-level NBBO quotes with smart filtering."""
    config = load_config()
    data_dir = get_data_dir(config)
    base_url = get_base_url(config)
    concurrency = get_concurrency(config)
    start, end = parse_date_args(args)
    tickers = args.ticker.split(",") if args.ticker else ALL_OPTION_TICKERS
    max_dte = args.max_dte if hasattr(args, 'max_dte') else 30
    strike_pct = args.strike_pct if hasattr(args, 'strike_pct') else 0.20

    async def _run():
        async with httpx.AsyncClient(timeout=300.0) as client:
            manifest = load_manifest(data_dir)
            manifest_idx = _build_manifest_index(manifest)
            eod_dir = theta_dir(data_dir) / "eod"
            out_base = theta_dir(data_dir) / "options_iv"
            sem = asyncio.Semaphore(concurrency)

            total_files = 0
            total_rows = 0
            t0 = time.time()

            try:
                for dt in trading_days(start, end):
                    dt_str = fmt_iso(dt)
                    print(f"\n{'='*60}")
                    print(f"[{dt_str}] Tick-level NBBO quotes")
                    print(f"{'='*60}")

                    for ticker in tickers:
                        expiries = get_relevant_expiries(
                            eod_dir, ticker, dt,
                            max_dte=max_dte, strike_pct=strike_pct)

                        if not expiries:
                            print(f"  {ticker}: no EOD data or no relevant "
                                  f"expiries (run 'eod' command first)")
                            continue

                        print(f"  {ticker}: {len(expiries)} expiries to "
                              f"download")
                        spot = get_spot_from_eod(eod_dir, ticker, dt)

                        for expiry in expiries:
                            if manifest_has(manifest, "tick-quotes", ticker,
                                            dt_str, expiry,
                                            _index=manifest_idx):
                                print(f"    {ticker}_{expiry}: cached, "
                                      f"skipping")
                                continue

                            print(f"    {ticker}_{expiry}: downloading...",
                                  end=" ", flush=True)
                            t1 = time.time()

                            df = await download_tick_iv_for_expiry(
                                client, base_url, ticker, expiry, dt_str,
                                sem, spot=spot, strike_pct=strike_pct)

                            if df.is_empty():
                                print("no data")
                                continue

                            df = process_tick_quotes(df, spot, strike_pct)

                            if df.is_empty():
                                print("empty after filtering")
                                continue

                            out = (out_base / dt_str
                                   / f"{ticker}_{expiry}.parquet")
                            save_parquet(df, out, f"{ticker}_{expiry}")

                            manifest_add(manifest, "tick-quotes", ticker,
                                         dt_str, len(df), str(out), expiry,
                                         _index=manifest_idx)
                            save_manifest(data_dir, manifest)
                            total_files += 1
                            total_rows += len(df)
                            elapsed = time.time() - t1
                            print(f"{len(df)} rows in {elapsed:.1f}s")

            except KeyboardInterrupt:
                print(f"\n\nInterrupted! Saving manifest "
                      f"({total_files} files so far)...")
                save_manifest(data_dir, manifest)
                print("Manifest saved. Resume by re-running the same "
                      "command.")
                return

            elapsed_total = time.time() - t0
            print(f"\nTick quotes complete: {total_files} files, "
                  f"{total_rows:,} total rows in {elapsed_total:.0f}s")

    asyncio.run(_run())


def cmd_trade_quote(args):
    """Download trade-quote data with smart filtering."""
    config = load_config()
    data_dir = get_data_dir(config)
    base_url = get_base_url(config)
    concurrency = get_concurrency(config)
    start, end = parse_date_args(args)
    tickers = args.ticker.split(",") if args.ticker else ALL_OPTION_TICKERS
    max_dte = args.max_dte if hasattr(args, 'max_dte') else 30
    strike_pct = args.strike_pct if hasattr(args, 'strike_pct') else 0.20

    async def _run():
        async with httpx.AsyncClient(timeout=300.0) as client:
            manifest = load_manifest(data_dir)
            manifest_idx = _build_manifest_index(manifest)
            eod_dir = theta_dir(data_dir) / "eod"
            out_base = theta_dir(data_dir) / "trade_quote"
            sem = asyncio.Semaphore(concurrency)

            total_files = 0
            total_rows = 0
            t0 = time.time()

            try:
                for dt in trading_days(start, end):
                    dt_str = fmt_iso(dt)
                    print(f"\n{'='*60}")
                    print(f"[{dt_str}] Trade-quote data")
                    print(f"{'='*60}")

                    for ticker in tickers:
                        expiries = get_relevant_expiries(
                            eod_dir, ticker, dt,
                            max_dte=max_dte, strike_pct=strike_pct)

                        if not expiries:
                            print(f"  {ticker}: no EOD data or no relevant "
                                  f"expiries")
                            continue

                        print(f"  {ticker}: {len(expiries)} expiries")
                        spot = get_spot_from_eod(eod_dir, ticker, dt)

                        for expiry in expiries:
                            if manifest_has(manifest, "trade-quote", ticker,
                                            dt_str, expiry,
                                            _index=manifest_idx):
                                print(f"    {ticker}_{expiry}: cached, "
                                      f"skipping")
                                continue

                            print(f"    {ticker}_{expiry}: downloading...",
                                  end=" ", flush=True)
                            t1 = time.time()

                            df = await download_trade_quote_for_expiry(
                                client, base_url, ticker, expiry, dt_str,
                                sem, spot=spot, strike_pct=strike_pct)

                            if df.is_empty():
                                print("no data")
                                continue

                            df = process_trade_quote(df, spot, strike_pct)

                            if df.is_empty():
                                print("empty after filtering")
                                continue

                            out = (out_base / dt_str
                                   / f"{ticker}_{expiry}.parquet")
                            save_parquet(df, out, f"{ticker}_{expiry}")

                            manifest_add(manifest, "trade-quote", ticker,
                                         dt_str, len(df), str(out), expiry,
                                         _index=manifest_idx)
                            save_manifest(data_dir, manifest)
                            total_files += 1
                            total_rows += len(df)
                            elapsed = time.time() - t1
                            print(f"{len(df)} rows in {elapsed:.1f}s")

            except KeyboardInterrupt:
                print(f"\n\nInterrupted! Saving manifest "
                      f"({total_files} files so far)...")
                save_manifest(data_dir, manifest)
                print("Manifest saved. Resume by re-running the same "
                      "command.")
                return

            elapsed_total = time.time() - t0
            print(f"\nTrade-quote complete: {total_files} files, "
                  f"{total_rows:,} total rows in {elapsed_total:.0f}s")

    asyncio.run(_run())


def cmd_stock_ohlc(args):
    """Download 1-minute OHLC bars for stocks and indices."""
    config = load_config()
    data_dir = get_data_dir(config)
    base_url = get_base_url(config)
    concurrency = get_concurrency(config)
    start, end = parse_date_args(args)
    tickers_arg = args.ticker.split(",") if args.ticker else None

    stock_tickers = ([t for t in OHLC_STOCK_TICKERS if t in tickers_arg]
                     if tickers_arg else OHLC_STOCK_TICKERS)
    index_tickers = ([t for t in OHLC_INDEX_TICKERS if t in tickers_arg]
                     if tickers_arg else OHLC_INDEX_TICKERS)
    all_tickers = [(t, False) for t in stock_tickers] + \
                  [(t, True) for t in index_tickers]

    async def _run():
        async with httpx.AsyncClient(timeout=300.0) as client:
            manifest = load_manifest(data_dir)
            manifest_idx = _build_manifest_index(manifest)
            out_dir = theta_dir(data_dir) / "stock_ohlc"
            sem = asyncio.Semaphore(concurrency)
            month_delta = timedelta(days=28)

            print(f"Downloading 1m OHLC: {len(all_tickers)} tickers, "
                  f"{fmt_iso(start)} to {fmt_iso(end)}")

            for ticker, is_index in all_tickers:
                label = f"{ticker} ({'index' if is_index else 'stock'})"

                if manifest_has(manifest, "stock-ohlc", ticker,
                                f"{fmt_iso(start)}:{fmt_iso(end)}",
                                _index=manifest_idx):
                    print(f"  {label}: cached, skipping")
                    continue

                print(f"  {label}: downloading...", end=" ", flush=True)
                t1 = time.time()

                frames = []
                chunk_start = start
                while chunk_start <= end:
                    chunk_end = min(chunk_start + month_delta, end)
                    df = await download_stock_ohlc(
                        client, base_url, ticker,
                        fmt_iso(chunk_start), fmt_iso(chunk_end),
                        sem, is_index=is_index)
                    if not df.is_empty():
                        frames.append(df)
                    chunk_start = chunk_end + timedelta(days=1)

                if not frames:
                    print("no data")
                    continue

                combined = pl.concat(frames, how="diagonal_relaxed")
                if "timestamp_us" in combined.columns:
                    combined = combined.unique(subset=["timestamp_us"]).sort(
                        "timestamp_us")

                out = out_dir / f"{ticker}.parquet"
                save_parquet(combined, out, label)

                manifest_add(manifest, "stock-ohlc", ticker,
                             f"{fmt_iso(start)}:{fmt_iso(end)}",
                             len(combined), str(out),
                             _index=manifest_idx)

                elapsed = time.time() - t1
                print(f"{len(combined)} bars in {elapsed:.1f}s")

            save_manifest(data_dir, manifest)
            print("\nStock/index OHLC complete.")

    asyncio.run(_run())


def cmd_status(args):
    """Show download manifest status."""
    config = load_config()
    data_dir = get_data_dir(config)
    manifest = load_manifest(data_dir)

    print(f"Manifest: {manifest_path(data_dir)}")
    print(f"Last updated: {manifest.get('updated_at', 'never')}")

    downloads = manifest.get("downloads", [])
    if not downloads:
        print("  No downloads recorded.")
        return

    # Group by command
    by_command: dict[str, list] = {}
    for entry in downloads:
        cmd = entry.get("command", "unknown")
        by_command.setdefault(cmd, []).append(entry)

    for cmd, entries in sorted(by_command.items()):
        total_rows = sum(e.get("rows", 0) for e in entries)
        dates = sorted(set(e.get("date", "") for e in entries))
        date_range = f"{dates[0]} to {dates[-1]}" if dates else "none"
        print(f"\n  {cmd}: {len(entries)} files, {total_rows:,} total rows")
        print(f"    Dates: {date_range}")


# ─── CLI Parser ─────────────────────────────────────────────────────────────��─

def add_common_args(parser):
    """Add common date/ticker arguments to a subparser."""
    parser.add_argument("--date", help="Single date (YYYY-MM-DD)")
    parser.add_argument("--start", help="Start date for range")
    parser.add_argument("--end", help="End date for range")
    parser.add_argument("--ticker", help="Comma-separated tickers (default: all)")


def main():
    parser = argparse.ArgumentParser(
        description="ThetaData Options & Stock Downloader")
    sub = parser.add_subparsers(dest="command", required=True)

    # EOD
    p_eod = sub.add_parser("eod", help="Download EOD Greeks + OI")
    add_common_args(p_eod)
    p_eod.add_argument("--skip-oi", action="store_true",
                        help="Skip open interest")
    p_eod.set_defaults(func=cmd_eod)

    # Tick quotes
    p_tick = sub.add_parser("tick-quotes",
                            help="Download tick-level NBBO quotes")
    add_common_args(p_tick)
    p_tick.add_argument("--max-dte", type=int, default=30,
                        help="Max days to expiry (default: 30)")
    p_tick.add_argument("--strike-pct", type=float, default=0.20,
                        help="Strike range as %% of spot (default: 0.20)")
    p_tick.set_defaults(func=cmd_tick_quotes)

    # Trade-quote
    p_tq = sub.add_parser("trade-quote",
                          help="Download trade-quote (trade + NBBO at exec)")
    add_common_args(p_tq)
    p_tq.add_argument("--max-dte", type=int, default=30,
                      help="Max days to expiry (default: 30)")
    p_tq.add_argument("--strike-pct", type=float, default=0.20,
                      help="Strike range as %% of spot (default: 0.20)")
    p_tq.set_defaults(func=cmd_trade_quote)

    # Stock/Index OHLC
    p_ohlc = sub.add_parser("stock-ohlc",
                            help="Download 1-minute stock/index OHLC bars")
    add_common_args(p_ohlc)
    p_ohlc.set_defaults(func=cmd_stock_ohlc)

    # Status
    p_status = sub.add_parser("status", help="Show download manifest status")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()

    if args.command in ("eod", "tick-quotes", "trade-quote", "stock-ohlc"):
        if not args.date and not args.start:
            parser.error(f"{args.command} requires --date or --start/--end")

    args.func(args)


if __name__ == "__main__":
    main()
