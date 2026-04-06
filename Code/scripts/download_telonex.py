"""
Telonex (Polymarket) Data Downloader
=====================================
Downloads L2 orderbook snapshots and trades for Polymarket stock/index binary
event markets via the Telonex SDK. Also builds a market registry mapping
Polymarket markets to their underlying financial instruments.

Requires: Telonex Plus subscription ($79/mo) for book_snapshot_full and trades.

Usage:
    # Download market metadata (free, no API key)
    python scripts/download_telonex.py markets

    # Build market registry from metadata
    python scripts/download_telonex.py registry

    # Download L2 book snapshots for all target markets
    python scripts/download_telonex.py book --start 2026-03-02 --end 2026-04-01

    # Download trades for all target markets
    python scripts/download_telonex.py trades --start 2026-03-02 --end 2026-04-01

    # Download both book + trades for all target markets
    python scripts/download_telonex.py all --start 2026-03-02 --end 2026-04-01

    # Download for specific tickers only
    python scripts/download_telonex.py all --start 2026-03-30 --end 2026-03-31 --ticker NVDA,AAPL

    # Download a single date
    python scripts/download_telonex.py book --date 2026-03-30

    # Force re-download (skip cache)
    python scripts/download_telonex.py book --date 2026-03-30 --force
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import polars as pl
import telonex

# ─── Configuration ────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # market-making-rnd/
CONFIG_PATH = PROJECT_ROOT / "config.toml"

TARGET_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "TSLA", "NFLX", "PLTR", "SPX",
]

# Categories of markets we care about for the backtester.
# "close_above" daily markets are the primary target (binary options analog).
# Weekly and monthly close-above markets are secondary targets.
MARKET_CATEGORIES = ["close_above"]


# ─── Config Loading ──────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load config.toml and return parsed dict."""
    if not CONFIG_PATH.exists():
        print(f"ERROR: config.toml not found at {CONFIG_PATH}")
        print("  Create it with at least: [paths] data_dir = \"D:/data\"")
        sys.exit(1)
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def get_data_dir(config: dict) -> Path:
    """Get the data directory from config."""
    data_dir = config.get("paths", {}).get("data_dir")
    if not data_dir:
        print("ERROR: [paths] data_dir not set in config.toml")
        sys.exit(1)
    return Path(data_dir)


def get_api_key(config: dict) -> str:
    """
    Get Telonex API key from config.toml or environment variable.
    config.toml [telonex] api_key takes priority, then TELONEX_API_KEY env var.
    """
    import os

    key = config.get("telonex", {}).get("api_key", "")
    if key:
        return key

    key = os.environ.get("TELONEX_API_KEY", "")
    if key:
        return key

    print("ERROR: Telonex API key not configured.")
    print("  Set it in config.toml under [telonex] api_key = \"your-key\"")
    print("  Or set the TELONEX_API_KEY environment variable.")
    sys.exit(1)


def telonex_dir(data_dir: Path) -> Path:
    """Base directory for all Telonex data."""
    return data_dir / "telonex"


def manifest_path(data_dir: Path) -> Path:
    """Path to the download manifest."""
    return telonex_dir(data_dir) / "manifest.json"


# ─── Manifest (Resume Tracking) ─────────────────────────────────────────────

def load_manifest(data_dir: Path) -> dict:
    """Load download manifest, creating empty one if missing."""
    mp = manifest_path(data_dir)
    if mp.exists():
        with open(mp) as f:
            return json.load(f)
    return {"book_snapshot_full": {}, "trades": {}, "updated_at": None}


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


def manifest_key(slug: str, outcome: str) -> str:
    """Generate manifest key for a market/outcome pair."""
    return f"{slug}/{outcome}"


# ─── Market Registry ─────────────────────────────────────────────────────────

# Regex patterns for parsing ticker, strike, and expiry from market slugs.
# These patterns are organized by ticker type and market category.

# Stock tickers: daily close-above pattern
# e.g. "nvda-close-above-165-on-march-26-2026"
DAILY_CLOSE_ABOVE_RE = re.compile(
    r"^(?P<pfx>[a-z]+)-close-above-(?P<strike>\d+)-on-(?P<month>[a-z]+)-(?P<day>\d+)-(?P<year>\d{4})$"
)

# Weekly close-above pattern (various tickers including PLTR, NFLX)
# e.g. "pltr-above-173-on-january-9-2026" or "nflx-above-140-on-april-30-2026"
WEEKLY_CLOSE_ABOVE_RE = re.compile(
    r"^(?P<pfx>[a-z]+)-above-(?P<strike>\d+)-on-(?P<month>[a-z]+)-(?P<day>\d+)-(?P<year>\d{4})$"
)

# SPX monthly short-month pattern
# e.g. "spx-above-6960-jan-2026"
SPX_MONTHLY_SHORT_RE = re.compile(
    r"^spx-above-(?P<strike>\d+)-(?P<month>[a-z]{3})-(?P<year>\d{4})$"
)

# SPX long-form close-over pattern
# e.g. "will-sp-500-spx-close-over-7000-on-the-final-trading-day-of-february-2026"
SPX_LONG_CLOSE_OVER_RE = re.compile(
    r"^will-sp-500-spx-close-over-(?P<strike>\d+)-on-the-final-trading-day-of-(?P<month>[a-z]+)-(?P<year>\d{4})$"
)

# SPX close-above with dec/jan suffix pattern
# e.g. "spx-close-above-8000-dec-2026-362-989-862"
SPX_CLOSE_ABOVE_RE = re.compile(
    r"^spx-close-above-(?P<strike>\d+)-(?P<month>[a-z]{3})-(?P<year>\d{4})"
)

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

# Short month name map (for SPX slugs like spx-above-6960-jan-2026)
SHORT_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Slug prefix -> ticker mapping
SLUG_PREFIX_TO_TICKER = {
    "nvda": "NVDA", "aapl": "AAPL", "msft": "MSFT", "googl": "GOOGL",
    "amzn": "AMZN", "meta": "META", "tsla": "TSLA", "nflx": "NFLX",
    "pltr": "PLTR", "spx": "SPX",
    # Long-form SPX prefix used by some markets
    "will-sp-500-spx": "SPX",
}

# Question-based regex for parsing strike and expiry when slug parsing fails.
# Handles patterns like:
#   "Will NVIDIA (NVDA) close above $165 on March 26?"
#   "Will Apple (AAPL) close above $275 on March 4?"
#   "Will Palantir (PLTR) finish week of March 9 above $157?"
#   "Will S&P 500 (SPX) close over $7,000 on the final trading day of February 2026?"
#   "Will Netflix (NFLX) close above $160 end of March?"
QUESTION_CLOSE_ABOVE_RE = re.compile(
    r"close (?:above|over) \$?(?P<strike>[\d,]+)"
)
QUESTION_FINISH_ABOVE_RE = re.compile(
    r"above \$?(?P<strike>[\d,]+)"
)

# Date patterns in questions
QUESTION_DATE_RE = re.compile(
    r"on (?P<month>[A-Z][a-z]+) (?P<day>\d+)"
)
QUESTION_END_OF_RE = re.compile(
    r"(?:end of|final (?:trading )?day of) (?P<month>[A-Z][a-z]+)"
)


def parse_strike_from_question(question: str) -> Optional[float]:
    """Extract strike price from market question text."""
    m = QUESTION_CLOSE_ABOVE_RE.search(question)
    if not m:
        m = QUESTION_FINISH_ABOVE_RE.search(question)
    if m:
        strike_str = m.group("strike").replace(",", "")
        try:
            return float(strike_str)
        except ValueError:
            return None
    return None


def _last_day_of_month(year: int, month: int) -> date:
    """Return the last calendar day of the given month."""
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day)


def parse_expiry_from_slug(slug: str) -> Optional[date]:
    """Parse expiry date from a market slug."""
    # Try daily close-above pattern: nvda-close-above-165-on-march-26-2026
    m = DAILY_CLOSE_ABOVE_RE.match(slug)
    if m:
        month = MONTH_MAP.get(m.group("month"))
        if month:
            try:
                return date(int(m.group("year")), month, int(m.group("day")))
            except ValueError:
                return None

    # Try weekly/monthly above pattern: pltr-above-173-on-january-9-2026
    m = WEEKLY_CLOSE_ABOVE_RE.match(slug)
    if m:
        month = MONTH_MAP.get(m.group("month"))
        if month:
            try:
                return date(int(m.group("year")), month, int(m.group("day")))
            except ValueError:
                return None

    # SPX monthly short-month pattern: spx-above-6960-jan-2026
    m = SPX_MONTHLY_SHORT_RE.match(slug)
    if m:
        month = SHORT_MONTH_MAP.get(m.group("month"))
        if month:
            try:
                return _last_day_of_month(int(m.group("year")), month)
            except ValueError:
                return None

    # SPX long-form close-over: will-sp-500-spx-close-over-7000-on-the-final-trading-day-of-february-2026
    m = SPX_LONG_CLOSE_OVER_RE.match(slug)
    if m:
        month = MONTH_MAP.get(m.group("month"))
        if month:
            try:
                return _last_day_of_month(int(m.group("year")), month)
            except ValueError:
                return None

    # SPX close-above with month suffix: spx-close-above-8000-dec-2026-...
    m = SPX_CLOSE_ABOVE_RE.match(slug)
    if m:
        month = SHORT_MONTH_MAP.get(m.group("month"))
        if month:
            try:
                return _last_day_of_month(int(m.group("year")), month)
            except ValueError:
                return None

    return None


def parse_expiry_from_end_date_us(end_date_us: int) -> Optional[date]:
    """Parse expiry date from Polymarket's end_date_us (microseconds since epoch)."""
    if not end_date_us or end_date_us == 0:
        return None
    try:
        ts = end_date_us / 1_000_000  # microseconds -> seconds
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    except (ValueError, OSError, OverflowError):
        return None


def parse_strike_from_slug(slug: str) -> Optional[float]:
    """Extract strike price from a market slug."""
    m = DAILY_CLOSE_ABOVE_RE.match(slug)
    if m:
        return float(m.group("strike"))

    m = WEEKLY_CLOSE_ABOVE_RE.match(slug)
    if m:
        return float(m.group("strike"))

    # SPX monthly short-month: spx-above-6960-jan-2026
    m = SPX_MONTHLY_SHORT_RE.match(slug)
    if m:
        return float(m.group("strike"))

    # SPX long-form close-over: will-sp-500-spx-close-over-7000-on-...
    m = SPX_LONG_CLOSE_OVER_RE.match(slug)
    if m:
        return float(m.group("strike"))

    # SPX close-above with suffix: spx-close-above-8000-dec-2026-...
    m = SPX_CLOSE_ABOVE_RE.match(slug)
    if m:
        return float(m.group("strike"))

    return None


def parse_ticker_from_slug(slug: str) -> Optional[str]:
    """Extract ticker from a market slug prefix.

    Checks longer prefixes first to avoid false positives
    (e.g. 'will-sp-500-spx-' should match before 'spx-' would fail on 'will-...').
    """
    # Sort by prefix length descending so longer prefixes match first
    for pfx, ticker in sorted(SLUG_PREFIX_TO_TICKER.items(), key=lambda x: -len(x[0])):
        if slug.startswith(pfx + "-"):
            return ticker
    return None


def is_stock_binary_market(question: str, slug: str) -> bool:
    """
    Determine if a market is a stock/index binary event market we care about.
    Matches "close above", "close over", "finish above" patterns for target tickers.
    """
    if not question or not slug:
        return False

    # Check if slug starts with a known ticker prefix
    ticker = parse_ticker_from_slug(slug)
    if not ticker:
        return False

    # Must have a non-zero strike price parseable from question or slug
    strike = parse_strike_from_slug(slug) or parse_strike_from_question(question)
    if strike is None or strike <= 0:
        return False

    # Must be a close-above or above type (not range, up-or-down, etc.)
    q_lower = question.lower()
    if any(kw in q_lower for kw in ["close above", "close over", "above"]):
        # Exclude range markets ("close at $X-$Y")
        if "close at" in q_lower and "-" in q_lower:
            return False
        return True

    return False


def build_registry(markets_path: Path) -> pl.DataFrame:
    """
    Build the market registry from the Telonex markets Parquet file.

    Filters to stock/index binary event markets, parses ticker/strike/expiry,
    and produces a structured registry for download orchestration.
    """
    print(f"Loading markets from {markets_path} ...")
    df = pl.read_parquet(markets_path)
    df = df.filter(pl.col("exchange") == "polymarket")
    print(f"  {len(df):,} Polymarket markets loaded")

    # Filter to markets with book snapshot data availability
    df = df.filter(
        pl.col("book_snapshot_full_from").is_not_null()
        & (pl.col("book_snapshot_full_from") != "")
    )
    print(f"  {len(df):,} have book_snapshot_full data")

    records = []
    skipped = 0

    for row in df.iter_rows(named=True):
        slug = row["slug"]
        question = row["question"] or ""

        if not is_stock_binary_market(question, slug):
            skipped += 1
            continue

        ticker = parse_ticker_from_slug(slug)
        if ticker not in TARGET_TICKERS:
            skipped += 1
            continue

        # Parse strike: try slug first, fall back to question
        strike = parse_strike_from_slug(slug) or parse_strike_from_question(question)
        if strike is None:
            skipped += 1
            continue

        # Parse expiry: try slug first, fall back to end_date_us
        expiry = parse_expiry_from_slug(slug) or parse_expiry_from_end_date_us(row["end_date_us"])
        if expiry is None:
            skipped += 1
            continue

        records.append({
            "slug": slug,
            "ticker": ticker,
            "strike": strike,
            "expiry": expiry,
            "market_id": row["market_id"],
            "yes_token_id": row["asset_id_0"],
            "no_token_id": row["asset_id_1"],
            "description": question,
            "event_slug": row["event_slug"],
            "status": row["status"],
            "result_id": row["result_id"],
            "book_from": row["book_snapshot_full_from"],
            "book_to": row["book_snapshot_full_to"],
            "trades_from": row["trades_from"],
            "trades_to": row["trades_to"],
        })

    if not records:
        print("  WARNING: No stock/index binary markets found!")
        return pl.DataFrame()

    registry = pl.DataFrame(records)

    # Convert expiry to date type
    registry = registry.with_columns(
        pl.col("expiry").cast(pl.Date),
    )

    # Sort by ticker, expiry, strike
    registry = registry.sort(["ticker", "expiry", "strike"])

    print(f"\n  Registry built: {len(registry):,} markets, {skipped:,} skipped")
    print(f"  Tickers: {sorted(registry['ticker'].unique().to_list())}")

    # Summary per ticker
    for ticker in sorted(registry["ticker"].unique().to_list()):
        t_df = registry.filter(pl.col("ticker") == ticker)
        print(f"    {ticker}: {len(t_df)} markets, "
              f"strikes {t_df['strike'].min()}-{t_df['strike'].max()}, "
              f"expiries {t_df['expiry'].min()} to {t_df['expiry'].max()}")

    return registry


# ─── Download Helpers ────────────────────────────────────────────────────────

def trading_days(start: date, end: date) -> list[date]:
    """Generate list of trading days (weekdays) in range [start, end] inclusive."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # Mon-Fri
            days.append(current)
        current += timedelta(days=1)
    return days


def filter_registry_for_dates(
    registry: pl.DataFrame,
    start: date,
    end: date,
    tickers: list[str] | None = None,
) -> pl.DataFrame:
    """
    Filter registry to markets that overlap with the requested date range.

    A market is relevant if its data availability window overlaps with [start, end].
    Also filters to requested tickers if specified.
    """
    filtered = registry

    # Filter by ticker
    if tickers:
        filtered = filtered.filter(pl.col("ticker").is_in(tickers))

    # Filter by date overlap: market's data window must overlap with [start, end]
    # book_from <= end AND book_to >= start (string comparison works for YYYY-MM-DD)
    start_str = start.isoformat()
    end_str = end.isoformat()

    filtered = filtered.filter(
        (pl.col("book_from") <= end_str)
        & (pl.col("book_to") >= start_str)
    )

    return filtered


def compute_download_dates(
    market_book_from: str,
    market_book_to: str,
    req_start: date,
    req_end: date,
) -> tuple[str, str]:
    """
    Compute the effective download date range for a market.

    Intersects the market's data availability with the requested range.
    Returns (from_date, to_date) as YYYY-MM-DD strings for telonex.download().
    Note: telonex.download() uses exclusive to_date, so we add 1 day.
    """
    # Parse availability dates
    avail_start = date.fromisoformat(market_book_from)
    avail_end = date.fromisoformat(market_book_to)

    # Intersect with requested range
    eff_start = max(avail_start, req_start)
    eff_end = min(avail_end, req_end)

    if eff_start > eff_end:
        return "", ""

    # to_date is exclusive in Telonex SDK
    return eff_start.isoformat(), (eff_end + timedelta(days=1)).isoformat()


def custom_filename(exchange: str, channel: str, dt: datetime, identifier: str) -> str:
    """
    Custom filename generator for Telonex downloads.

    Produces: {YYYY-MM-DD}_{channel}_{identifier}.parquet
    This is simpler and flatter than the SDK default.
    """
    date_str = dt.strftime("%Y-%m-%d")
    return f"{date_str}_{channel}_{identifier}.parquet"


def download_channel_for_market(
    api_key: str,
    channel: str,
    slug: str,
    outcome: str,
    from_date: str,
    to_date: str,
    download_dir: Path,
    force: bool = False,
    concurrency: int = 5,
) -> list[str]:
    """
    Download a single channel for a single market/outcome.

    Returns list of downloaded file paths.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    try:
        files = telonex.download(
            api_key=api_key,
            exchange="polymarket",
            channel=channel,
            slug=slug,
            outcome=outcome,
            from_date=from_date,
            to_date=to_date,
            download_dir=str(download_dir),
            concurrency=concurrency,
            verbose=False,
            force_download=force,
        )
        return files
    except telonex.AuthenticationError:
        print("  ERROR: Invalid Telonex API key. Check config.toml [telonex] api_key")
        sys.exit(1)
    except telonex.EntitlementError as e:
        print(f"  ERROR: Telonex entitlement error: {e}")
        print("  You may need a Telonex Plus subscription for this channel.")
        sys.exit(1)
    except telonex.NotFoundError:
        # No data for this market/date range -- expected for some markets
        return []
    except telonex.RateLimitError as e:
        print(f"  RATE LIMITED: {e}. Waiting 30s...")
        time.sleep(30)
        # Retry once
        try:
            return telonex.download(
                api_key=api_key,
                exchange="polymarket",
                channel=channel,
                slug=slug,
                outcome=outcome,
                from_date=from_date,
                to_date=to_date,
                download_dir=str(download_dir),
                concurrency=concurrency,
                verbose=False,
                force_download=force,
            )
        except Exception:
            return []
    except telonex.DownloadError as e:
        print(f"  DOWNLOAD ERROR for {slug}/{outcome}: {e}")
        return []
    except Exception as e:
        print(f"  UNEXPECTED ERROR for {slug}/{outcome}: {e}")
        return []


def download_channel(
    api_key: str,
    channel: str,
    registry: pl.DataFrame,
    start: date,
    end: date,
    data_dir: Path,
    tickers: list[str] | None = None,
    force: bool = False,
    concurrency: int = 5,
) -> dict:
    """
    Download a channel (book_snapshot_full or trades) for all markets in registry.

    Returns download stats dict.
    """
    # Determine the raw data subdirectory
    if channel == "book_snapshot_full":
        raw_subdir = "book_raw"
        avail_from_col = "book_from"
        avail_to_col = "book_to"
    elif channel == "trades":
        raw_subdir = "trades_raw"
        avail_from_col = "trades_from"
        avail_to_col = "trades_to"
    else:
        print(f"ERROR: Unknown channel: {channel}")
        return {}

    # Filter registry
    filtered = filter_registry_for_dates(registry, start, end, tickers)

    if filtered.is_empty():
        print(f"  No markets found for the requested date range and tickers.")
        return {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0}

    print(f"\n{'=' * 70}")
    print(f"Downloading {channel} for {len(filtered)} markets")
    print(f"  Date range: {start} to {end}")
    if tickers:
        print(f"  Tickers: {tickers}")
    print(f"{'=' * 70}")

    # Load manifest
    mf = load_manifest(data_dir)
    if channel not in mf:
        mf[channel] = {}

    stats = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0, "files": 0}
    outcomes = ["Yes", "No"]

    for i, row in enumerate(filtered.iter_rows(named=True)):
        slug = row["slug"]
        ticker = row["ticker"]
        strike = row["strike"]

        # Compute effective date range for this market
        avail_from = row.get(avail_from_col, "")
        avail_to = row.get(avail_to_col, "")
        if not avail_from or not avail_to:
            continue

        from_date, to_date = compute_download_dates(avail_from, avail_to, start, end)
        if not from_date:
            continue

        for outcome in outcomes:
            stats["total"] += 1
            mk = manifest_key(slug, outcome)

            # Check manifest for completion
            if not force and mk in mf[channel]:
                entry = mf[channel][mk]
                if entry.get("status") == "complete":
                    stats["skipped"] += 1
                    continue

            # Download
            dl_dir = telonex_dir(data_dir) / raw_subdir / slug
            print(f"  [{i+1}/{len(filtered)}] {ticker} ${strike:.0f} | {slug} / {outcome} "
                  f"({from_date} to {to_date})")

            t0 = time.time()
            files = download_channel_for_market(
                api_key=api_key,
                channel=channel,
                slug=slug,
                outcome=outcome,
                from_date=from_date,
                to_date=to_date,
                download_dir=dl_dir,
                force=force,
                concurrency=concurrency,
            )
            elapsed = time.time() - t0

            if files:
                stats["downloaded"] += 1
                stats["files"] += len(files)
                mf[channel][mk] = {
                    "status": "complete",
                    "files": len(files),
                    "from_date": from_date,
                    "to_date": to_date,
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "elapsed_s": round(elapsed, 1),
                }
                print(f"    -> {len(files)} files in {elapsed:.1f}s")
            else:
                stats["failed"] += 1
                mf[channel][mk] = {
                    "status": "no_data",
                    "from_date": from_date,
                    "to_date": to_date,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
                print(f"    -> no data")

            # Save manifest periodically (every 10 downloads)
            if (stats["downloaded"] + stats["failed"]) % 10 == 0:
                save_manifest(data_dir, mf)

    # Final manifest save
    save_manifest(data_dir, mf)

    print(f"\n{channel} download complete:")
    print(f"  Total market/outcome pairs: {stats['total']}")
    print(f"  Downloaded: {stats['downloaded']} ({stats['files']} files)")
    print(f"  Skipped (cached): {stats['skipped']}")
    print(f"  No data: {stats['failed']}")

    return stats


# ─── CLI Commands ────────────────────────────────────────────────────────────

def cmd_markets(args):
    """Download Polymarket market metadata from Telonex (free, no API key)."""
    config = load_config()
    data_dir = get_data_dir(config)
    out_dir = telonex_dir(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dest = out_dir / "polymarket_markets.parquet"

    print("Downloading Polymarket market metadata from Telonex ...")
    print(f"  Destination: {dest}")

    # Use a temp dir for the SDK, then move to our location
    tmp_dir = out_dir / "_tmp_markets"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        sdk_path = telonex.download_markets(
            exchange="polymarket",
            download_dir=str(tmp_dir),
            verbose=True,
        )
        sdk_path = Path(sdk_path)

        # Move to final location
        if sdk_path.exists():
            if dest.exists():
                dest.unlink()
            sdk_path.rename(dest)
            print(f"\n  Saved to: {dest}")

            # Print stats
            df = pl.read_parquet(dest)
            print(f"  Total markets: {len(df):,}")
            pm = df.filter(pl.col("exchange") == "polymarket")
            print(f"  Polymarket markets: {len(pm):,}")

            # Count stock/index markets
            for ticker in TARGET_TICKERS:
                pfx = ticker.lower()
                count = pm.filter(pl.col("event_slug").str.contains(pfx)).shape[0]
                if count > 0:
                    print(f"    {ticker}: {count}")
        else:
            print(f"  ERROR: SDK did not produce expected file at {sdk_path}")
    finally:
        # Clean up temp dir
        import shutil
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def cmd_registry(args):
    """Build market registry from downloaded market metadata."""
    config = load_config()
    data_dir = get_data_dir(config)
    markets_path = telonex_dir(data_dir) / "polymarket_markets.parquet"

    if not markets_path.exists():
        print(f"Market metadata not found at {markets_path}")
        print("Run 'markets' command first: python scripts/download_telonex.py markets")
        resp = input("Download now? [y/N] ").strip().lower()
        if resp == "y":
            # Simulate markets command
            cmd_markets(args)
        else:
            sys.exit(1)

    registry = build_registry(markets_path)

    if registry.is_empty():
        print("ERROR: Registry is empty. Check market metadata.")
        sys.exit(1)

    # Save registry
    registry_path = telonex_dir(data_dir) / "market_registry.parquet"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry.write_parquet(registry_path, compression="zstd")
    size_kb = registry_path.stat().st_size / 1024
    print(f"\n  Registry saved to: {registry_path} ({size_kb:.1f} KB)")

    # Also save as CSV for easy inspection
    csv_path = telonex_dir(data_dir) / "market_registry.csv"
    registry.write_csv(csv_path)
    print(f"  CSV copy saved to: {csv_path}")


def load_registry(data_dir: Path) -> pl.DataFrame:
    """Load market registry, prompting to build it if missing."""
    registry_path = telonex_dir(data_dir) / "market_registry.parquet"

    if not registry_path.exists():
        print(f"Market registry not found at {registry_path}")
        print("Building registry first...")

        markets_path = telonex_dir(data_dir) / "polymarket_markets.parquet"
        if not markets_path.exists():
            print(f"Market metadata also missing at {markets_path}")
            print("Run these commands in order:")
            print("  python scripts/download_telonex.py markets")
            print("  python scripts/download_telonex.py registry")
            sys.exit(1)

        registry = build_registry(markets_path)
        if registry.is_empty():
            print("ERROR: Registry is empty. Check market metadata.")
            sys.exit(1)

        registry.write_parquet(registry_path, compression="zstd")
        print(f"  Registry auto-built and saved to: {registry_path}")
        return registry

    return pl.read_parquet(registry_path)


def parse_date_args(args) -> tuple[date, date]:
    """Parse --date or --start/--end from CLI args into (start, end) dates."""
    if args.date:
        d = datetime.strptime(args.date, "%Y-%m-%d").date()
        return d, d
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else start
        return start, end
    print("ERROR: Must specify --date or --start (with optional --end)")
    sys.exit(1)


def parse_tickers(args) -> list[str] | None:
    """Parse --ticker arg into list of ticker strings, or None for all."""
    if not args.ticker:
        return None
    tickers = [t.strip().upper() for t in args.ticker.split(",")]
    for t in tickers:
        if t not in TARGET_TICKERS:
            print(f"WARNING: Unknown ticker '{t}'. Valid tickers: {TARGET_TICKERS}")
    return tickers


def cmd_book(args):
    """Download L2 book snapshots for target markets."""
    config = load_config()
    data_dir = get_data_dir(config)
    api_key = get_api_key(config)

    start, end = parse_date_args(args)
    tickers = parse_tickers(args)

    registry = load_registry(data_dir)
    print(f"Registry loaded: {len(registry)} markets")

    download_channel(
        api_key=api_key,
        channel="book_snapshot_full",
        registry=registry,
        start=start,
        end=end,
        data_dir=data_dir,
        tickers=tickers,
        force=args.force,
        concurrency=args.concurrency,
    )


def cmd_trades(args):
    """Download trade data for target markets."""
    config = load_config()
    data_dir = get_data_dir(config)
    api_key = get_api_key(config)

    start, end = parse_date_args(args)
    tickers = parse_tickers(args)

    registry = load_registry(data_dir)
    print(f"Registry loaded: {len(registry)} markets")

    download_channel(
        api_key=api_key,
        channel="trades",
        registry=registry,
        start=start,
        end=end,
        data_dir=data_dir,
        tickers=tickers,
        force=args.force,
        concurrency=args.concurrency,
    )


def cmd_all(args):
    """Download both book snapshots and trades for target markets."""
    config = load_config()
    data_dir = get_data_dir(config)
    api_key = get_api_key(config)

    start, end = parse_date_args(args)
    tickers = parse_tickers(args)

    registry = load_registry(data_dir)
    print(f"Registry loaded: {len(registry)} markets")

    t0 = time.time()

    # Book snapshots first
    book_stats = download_channel(
        api_key=api_key,
        channel="book_snapshot_full",
        registry=registry,
        start=start,
        end=end,
        data_dir=data_dir,
        tickers=tickers,
        force=args.force,
        concurrency=args.concurrency,
    )

    # Then trades
    trades_stats = download_channel(
        api_key=api_key,
        channel="trades",
        registry=registry,
        start=start,
        end=end,
        data_dir=data_dir,
        tickers=tickers,
        force=args.force,
        concurrency=args.concurrency,
    )

    elapsed = time.time() - t0
    print(f"\n{'=' * 70}")
    print(f"All downloads complete in {elapsed:.0f}s")
    print(f"  Book snapshots: {book_stats.get('downloaded', 0)} downloaded, "
          f"{book_stats.get('files', 0)} files")
    print(f"  Trades: {trades_stats.get('downloaded', 0)} downloaded, "
          f"{trades_stats.get('files', 0)} files")
    print(f"{'=' * 70}")


def cmd_availability(args):
    """Check data availability for a specific market slug."""
    slug = args.slug
    outcome = args.outcome or "Yes"

    print(f"Checking availability for {slug} / {outcome} ...")
    try:
        avail = telonex.get_availability(
            exchange="polymarket",
            slug=slug,
            outcome=outcome,
        )
        print(json.dumps(avail, indent=2, default=str))
    except Exception as e:
        print(f"ERROR: {e}")


def cmd_status(args):
    """Show download progress from the manifest."""
    config = load_config()
    data_dir = get_data_dir(config)

    mf = load_manifest(data_dir)

    print(f"Download manifest: {manifest_path(data_dir)}")
    print(f"Last updated: {mf.get('updated_at', 'never')}")

    for channel in ["book_snapshot_full", "trades"]:
        entries = mf.get(channel, {})
        if not entries:
            print(f"\n  {channel}: no downloads recorded")
            continue

        complete = sum(1 for e in entries.values() if isinstance(e, dict) and e.get("status") == "complete")
        no_data = sum(1 for e in entries.values() if isinstance(e, dict) and e.get("status") == "no_data")
        failed = sum(1 for e in entries.values() if isinstance(e, dict) and e.get("status") == "failed")
        total_files = sum(
            e.get("files", 0)
            for e in entries.values()
            if isinstance(e, dict) and e.get("status") == "complete"
        )

        print(f"\n  {channel}:")
        print(f"    Complete: {complete} ({total_files} files)")
        print(f"    No data:  {no_data}")
        print(f"    Failed:   {failed}")
        print(f"    Total:    {len(entries)}")


# ─── Main ────────────────────────────────────────────────────────────────────

def add_date_args(parser: argparse.ArgumentParser):
    """Add standard date arguments to a subparser."""
    parser.add_argument("--date", help="Single date (YYYY-MM-DD)")
    parser.add_argument("--start", help="Start date for range (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date for range (YYYY-MM-DD)")


def add_download_args(parser: argparse.ArgumentParser):
    """Add standard download arguments to a subparser."""
    add_date_args(parser)
    parser.add_argument("--ticker", help="Comma-separated tickers (default: all target tickers)")
    parser.add_argument("--force", action="store_true", help="Force re-download (ignore cache)")
    parser.add_argument("--concurrency", type=int, default=5, help="Max concurrent downloads (default: 5)")


def main():
    parser = argparse.ArgumentParser(
        description="Telonex (Polymarket) Data Downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  markets       Download Polymarket market metadata (free, no API key)
  registry      Build market registry from metadata
  book          Download L2 book snapshots (book_snapshot_full)
  trades        Download trade data
  all           Download both book snapshots and trades
  availability  Check data availability for a specific market
  status        Show download progress from manifest
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # markets
    p_markets = sub.add_parser("markets", help="Download Polymarket market metadata")
    p_markets.set_defaults(func=cmd_markets)

    # registry
    p_registry = sub.add_parser("registry", help="Build market registry from metadata")
    p_registry.set_defaults(func=cmd_registry)

    # book
    p_book = sub.add_parser("book", help="Download L2 book snapshots")
    add_download_args(p_book)
    p_book.set_defaults(func=cmd_book)

    # trades
    p_trades = sub.add_parser("trades", help="Download trade data")
    add_download_args(p_trades)
    p_trades.set_defaults(func=cmd_trades)

    # all
    p_all = sub.add_parser("all", help="Download both book + trades")
    add_download_args(p_all)
    p_all.set_defaults(func=cmd_all)

    # availability
    p_avail = sub.add_parser("availability", help="Check data availability for a market")
    p_avail.add_argument("slug", help="Market slug")
    p_avail.add_argument("--outcome", default="Yes", help="Outcome: Yes or No (default: Yes)")
    p_avail.set_defaults(func=cmd_availability)

    # status
    p_status = sub.add_parser("status", help="Show download progress")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()

    # Validate date args for download commands
    if args.command in ("book", "trades", "all"):
        if not args.date and not args.start:
            parser.error(f"{args.command} requires --date or --start (with optional --end)")

    args.func(args)


if __name__ == "__main__":
    main()
