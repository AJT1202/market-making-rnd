"""
Data Validation for Backtester v1.0
====================================
Checks integrity, completeness, and quality of downloaded data from
ThetaData (options/stocks) and Telonex (Polymarket L2 books/trades).

Reads data_dir from config.toml at project root. Works gracefully when
data is partially downloaded — validates what exists and reports what's missing.

Usage:
    # Full validation across all sources
    python scripts/validate_data.py all --start 2026-03-02 --end 2026-04-01

    # Schema validation only
    python scripts/validate_data.py schema --source thetadata
    python scripts/validate_data.py schema --source telonex

    # Completeness check for a date range
    python scripts/validate_data.py completeness --start 2026-03-02 --end 2026-04-01

    # Quality report (generates JSON)
    python scripts/validate_data.py report --start 2026-03-02 --end 2026-04-01

    # Save detailed JSON report
    python scripts/validate_data.py all --start 2026-03-02 --end 2026-04-01 --save-report
"""

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

try:
    import tomli
except ImportError:
    # Python 3.11+ has tomllib in the stdlib
    import tomllib as tomli  # type: ignore[import-not-found]


# --- Configuration ---

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.toml"

TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "TSLA", "NFLX", "PLTR",
]
INDEX_TICKERS = ["SPX", "SPXW"]
ALL_TICKERS = TICKERS + INDEX_TICKERS

# Stock tickers that have OHLC data (no SPXW — it uses SPX index OHLC)
OHLC_TICKERS = TICKERS + ["SPX"]

# Market hours in Eastern Time (minutes from midnight)
MARKET_OPEN_MINS = 9 * 60 + 30    # 09:30 ET
MARKET_CLOSE_MINS = 16 * 60 + 15  # 16:15 ET (includes 15 min post-close)

# US market holidays for 2026 (extend as needed)
US_HOLIDAYS_2026 = {
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Jr. Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}


# --- Severity levels ---

CRITICAL = "CRITICAL"
WARNING = "WARNING"
INFO = "INFO"


# --- Helpers ---

def load_config() -> dict:
    """Load config.toml and return parsed dict."""
    if not CONFIG_PATH.exists():
        print(f"ERROR: config.toml not found at {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "rb") as f:
        return tomli.load(f)


def get_data_dir() -> Path:
    """Get data directory from config.toml."""
    cfg = load_config()
    data_dir = Path(cfg["paths"]["data_dir"])
    if not data_dir.exists():
        print(f"WARNING: data_dir does not exist: {data_dir}")
    return data_dir


def trading_days(start: date, end: date) -> list[date]:
    """Return list of expected trading days (skip weekends and US holidays)."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in US_HOLIDAYS_2026:
            days.append(current)
        current += timedelta(days=1)
    return days


def format_size(nbytes: int) -> str:
    """Format byte count as human-readable string."""
    if nbytes >= 1024 ** 3:
        return f"{nbytes / (1024 ** 3):.2f} GB"
    if nbytes >= 1024 ** 2:
        return f"{nbytes / (1024 ** 2):.1f} MB"
    if nbytes >= 1024:
        return f"{nbytes / 1024:.0f} KB"
    return f"{nbytes} B"


def safe_read_parquet(path: Path) -> pl.DataFrame | None:
    """Read a Parquet file, returning None on failure."""
    try:
        return pl.read_parquet(path)
    except Exception as e:
        return None


# --- Schema definitions ---

# Each schema: dict of column_name -> (expected_polars_dtype_family, nullable)
# dtype_family is a prefix check, e.g. "Int" matches Int32, Int64, etc.

TICK_QUOTES_SCHEMA = {
    "timestamp_us":      ("Int", False),
    "symbol":            ("String", False),
    "strike":            ("Int", False),
    "right":             ("String", False),
    "expiration":        ("String", False),
    "bid":               ("Float", True),
    "bid_size":          ("Int", True),
    "ask":               ("Float", True),
    "ask_size":          ("Int", True),
}

TRADE_QUOTE_SCHEMA = {
    "timestamp":         ("Int", False),
    "symbol":            ("String", False),
    "strike":            ("Int", False),
    "right":             ("String", False),
    "expiration":        ("String", False),
    "price":             ("Float", True),
    "size":              ("Int", True),
    "bid":               ("Float", True),
    "ask":               ("Float", True),
}

EOD_GREEKS_SCHEMA = {
    "symbol":            ("String", False),
    "expiration":        ("String", False),
    "strike":            ("Int", False),
    "right":             ("String", False),
    "implied_vol":       ("Float", True),
    "delta":             ("Float", True),
    "gamma":             ("Float", True),
    "theta":             ("Float", True),
    "underlying_price":  ("Float", True),
}

EOD_OI_SCHEMA = {
    "symbol":            ("String", False),
    "expiration":        ("String", False),
    "strike":            ("Int", False),
    "right":             ("String", False),
    "open_interest":     ("Int", True),
}

STOCK_OHLC_SCHEMA = {
    "open":              ("Float", True),
    "high":              ("Float", True),
    "low":               ("Float", True),
    "close":             ("Float", True),
    "volume":            ("Int", True),
}

TELONEX_BOOK_SCHEMA = {
    # Telonex book_snapshot_full has timestamp_us plus depth columns.
    # We check the core required columns; depth columns are variable.
    "timestamp_us":      ("Int", False),
}

TELONEX_TRADES_SCHEMA = {
    "timestamp_us":      ("Int", False),
}

MARKET_REGISTRY_SCHEMA = {
    "market_slug":       ("String", False),
    "ticker":            ("String", False),
    "strike":            ("Float", True),
    "expiry":            ("Date", True),
}


# --- Validation result collector ---

class ValidationResult:
    """Collects validation findings across all checks."""

    def __init__(self):
        self.findings: list[dict] = []
        self.file_stats: list[dict] = []

    def add(self, severity: str, check: str, message: str, **kwargs):
        finding = {"severity": severity, "check": check, "message": message}
        finding.update(kwargs)
        self.findings.append(finding)

    def add_stat(self, **kwargs):
        self.file_stats.append(kwargs)

    @property
    def critical_failures(self) -> list[dict]:
        return [f for f in self.findings if f["severity"] == CRITICAL]

    @property
    def warnings(self) -> list[dict]:
        return [f for f in self.findings if f["severity"] == WARNING]

    @property
    def infos(self) -> list[dict]:
        return [f for f in self.findings if f["severity"] == INFO]

    @property
    def overall_status(self) -> str:
        if self.critical_failures:
            return "FAIL"
        if self.warnings:
            return "WARN"
        return "PASS"

    def print_summary(self):
        total_files = len(self.file_stats)
        total_bytes = sum(s.get("size_bytes", 0) for s in self.file_stats)
        total_rows = sum(s.get("rows", 0) for s in self.file_stats)

        status = self.overall_status
        status_color = {"PASS": "\033[92m", "WARN": "\033[93m", "FAIL": "\033[91m"}
        reset = "\033[0m"

        print("\n" + "=" * 70)
        print(f"  VALIDATION RESULT: {status_color.get(status, '')}{status}{reset}")
        print("=" * 70)

        print(f"\n  Files scanned:      {total_files}")
        print(f"  Total rows:         {total_rows:,}")
        print(f"  Total size:         {format_size(total_bytes)}")
        print(f"  Critical failures:  {len(self.critical_failures)}")
        print(f"  Warnings:           {len(self.warnings)}")
        print(f"  Info items:         {len(self.infos)}")

        if self.critical_failures:
            print(f"\n--- CRITICAL ({len(self.critical_failures)}) ---")
            for f in self.critical_failures:
                file_info = f" [{f['file']}]" if "file" in f else ""
                print(f"  [CRITICAL] {f['check']}: {f['message']}{file_info}")

        if self.warnings:
            print(f"\n--- WARNINGS ({len(self.warnings)}) ---")
            for f in self.warnings[:20]:  # Cap display at 20
                file_info = f" [{f['file']}]" if "file" in f else ""
                print(f"  [WARN] {f['check']}: {f['message']}{file_info}")
            if len(self.warnings) > 20:
                print(f"  ... and {len(self.warnings) - 20} more warnings")

        if self.infos:
            print(f"\n--- INFO ({len(self.infos)}) ---")
            for f in self.infos[:15]:
                print(f"  [INFO] {f['check']}: {f['message']}")
            if len(self.infos) > 15:
                print(f"  ... and {len(self.infos) - 15} more info items")

        print()

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict for report output."""
        total_bytes = sum(s.get("size_bytes", 0) for s in self.file_stats)
        return {
            "run_at": datetime.utcnow().isoformat() + "Z",
            "status": self.overall_status,
            "critical_failures": self.critical_failures,
            "warnings": self.warnings,
            "info": self.infos,
            "summary": {
                "total_files": len(self.file_stats),
                "total_rows": sum(s.get("rows", 0) for s in self.file_stats),
                "total_size_gb": round(total_bytes / (1024 ** 3), 2),
            },
            "file_stats": self.file_stats,
        }


# --- Schema validation ---

def check_dtype_family(col_dtype: pl.DataType, expected_family: str) -> bool:
    """Check if a Polars dtype matches the expected family prefix."""
    dtype_str = str(col_dtype)
    if expected_family == "Int":
        return "Int" in dtype_str or "UInt" in dtype_str
    if expected_family == "Float":
        return "Float" in dtype_str
    if expected_family == "String":
        return "String" in dtype_str or "Utf8" in dtype_str or "Categorical" in dtype_str
    if expected_family == "Date":
        return "Date" in dtype_str
    return False


def validate_schema(
    df: pl.DataFrame,
    expected: dict,
    file_path: Path,
    result: ValidationResult,
    label: str,
):
    """Validate DataFrame schema against expected column definitions."""
    actual_cols = set(df.columns)

    for col_name, (dtype_family, nullable) in expected.items():
        # Check column exists
        if col_name not in actual_cols:
            # For schemas with flexible columns (e.g. timestamp vs timestamp_us),
            # skip missing optional columns silently handled by caller.
            result.add(
                CRITICAL, "schema_missing_column",
                f"Missing required column '{col_name}' in {label}",
                file=str(file_path),
            )
            continue

        # Check dtype
        col_dtype = df.schema[col_name]
        if not check_dtype_family(col_dtype, dtype_family):
            result.add(
                WARNING, "schema_dtype_mismatch",
                f"Column '{col_name}' expected {dtype_family} but got {col_dtype} in {label}",
                file=str(file_path),
            )

        # Check nulls in non-nullable columns
        if not nullable:
            null_count = df[col_name].null_count()
            if null_count > 0:
                result.add(
                    CRITICAL, "schema_unexpected_nulls",
                    f"Column '{col_name}' has {null_count} nulls (non-nullable) in {label}",
                    file=str(file_path),
                )


# --- Timestamp validation ---

def validate_timestamps(
    df: pl.DataFrame,
    ts_col: str,
    file_path: Path,
    file_date: date | None,
    result: ValidationResult,
    label: str,
    check_market_hours: bool = True,
):
    """Validate timestamp column: monotonicity, market hours, gaps."""
    if ts_col not in df.columns:
        return

    ts = df[ts_col]

    # --- Monotonicity ---
    diffs = ts.diff().drop_nulls()
    negative_count = (diffs < 0).sum()
    if negative_count > 0:
        result.add(
            CRITICAL, "timestamp_not_monotonic",
            f"{negative_count} out-of-order timestamps in {label}",
            file=str(file_path),
        )

    # --- Future timestamps ---
    if file_date is not None:
        # End of file_date in UTC microseconds (approximate: midnight next day)
        # Allow up to 21:00 UTC (17:00 ET) for post-market activity
        eod_us = int(datetime(file_date.year, file_date.month, file_date.day, 21, 0, 0).timestamp() * 1_000_000)
        max_ts = ts.max()
        if max_ts is not None and max_ts > eod_us:
            result.add(
                WARNING, "timestamp_future",
                f"Max timestamp {max_ts} exceeds expected EOD for {file_date} in {label}",
                file=str(file_path),
            )

    # --- Market hours check ---
    if check_market_hours and file_date is not None and len(df) > 0:
        min_ts = ts.min()
        max_ts = ts.max()
        if min_ts is not None and max_ts is not None:
            # Convert to approximate Eastern Time hours for a rough bounds check.
            # UTC to ET offset is -4 (EDT) or -5 (EST). Use -4 as a conservative bound.
            min_dt = datetime.utcfromtimestamp(min_ts / 1_000_000)
            max_dt = datetime.utcfromtimestamp(max_ts / 1_000_000)
            # Shift to ET (approx — DST-aware would need pytz/zoneinfo)
            min_et_hour = min_dt.hour - 4
            max_et_hour = max_dt.hour - 4
            # Very loose check: data should be roughly between 9 and 17 ET
            if min_et_hour < 4 or max_et_hour > 21:
                result.add(
                    WARNING, "timestamp_outside_hours",
                    f"Timestamps span {min_dt.strftime('%H:%M')}-{max_dt.strftime('%H:%M')} UTC "
                    f"(~{min_et_hour}:{min_dt.minute:02d}-{max_et_hour}:{max_dt.minute:02d} ET) in {label}",
                    file=str(file_path),
                )

    # --- Gap detection ---
    if len(df) > 10:
        diffs_pos = diffs.filter(diffs > 0)
        if len(diffs_pos) > 0:
            median_gap = diffs_pos.median()
            if median_gap is not None and median_gap > 0:
                threshold = median_gap * 10
                large_gaps = (diffs_pos > threshold).sum()
                if large_gaps > 0:
                    max_gap_us = diffs_pos.max()
                    max_gap_sec = max_gap_us / 1_000_000 if max_gap_us else 0
                    result.add(
                        WARNING, "timestamp_gap",
                        f"{large_gaps} gaps > 10x median interval "
                        f"(median={median_gap/1_000_000:.2f}s, max gap={max_gap_sec:.1f}s) in {label}",
                        file=str(file_path),
                    )


# --- ThetaData validation ---

def find_thetadata_files(data_dir: Path) -> dict:
    """Discover all ThetaData files organized by type."""
    td_dir = data_dir / "thetadata"
    files = {
        "tick_quotes": [],
        "trade_quote": [],
        "eod_greeks": [],
        "eod_oi": [],
        "stock_ohlc": [],
    }

    # tick_quotes/{YYYY-MM-DD}/{TICKER}_{EXPIRY}.parquet
    tq_dir = td_dir / "tick_quotes"
    if tq_dir.exists():
        for date_dir in sorted(tq_dir.iterdir()):
            if date_dir.is_dir():
                for pq in sorted(date_dir.glob("*.parquet")):
                    files["tick_quotes"].append(pq)

    # trade_quote/{YYYY-MM-DD}/{TICKER}_{EXPIRY}.parquet
    trq_dir = td_dir / "trade_quote"
    if trq_dir.exists():
        for date_dir in sorted(trq_dir.iterdir()):
            if date_dir.is_dir():
                for pq in sorted(date_dir.glob("*.parquet")):
                    files["trade_quote"].append(pq)

    # eod/{YYYYMMDD}/greeks*.parquet and oi*.parquet
    eod_dir = td_dir / "eod"
    if eod_dir.exists():
        for date_dir in sorted(eod_dir.iterdir()):
            if date_dir.is_dir():
                for pq in sorted(date_dir.glob("greeks*.parquet")):
                    files["eod_greeks"].append(pq)
                for pq in sorted(date_dir.glob("oi*.parquet")):
                    files["eod_oi"].append(pq)

    # stock_ohlc/{TICKER}*.parquet
    ohlc_dir = td_dir / "stock_ohlc"
    if ohlc_dir.exists():
        for pq in sorted(ohlc_dir.glob("*.parquet")):
            files["stock_ohlc"].append(pq)

    return files


def validate_thetadata_schema(data_dir: Path, result: ValidationResult):
    """Validate schemas of all ThetaData files."""
    files = find_thetadata_files(data_dir)

    print("\n--- ThetaData Schema Validation ---")

    # Tick quotes
    for pq in files["tick_quotes"]:
        df = safe_read_parquet(pq)
        if df is None:
            result.add(CRITICAL, "file_unreadable", f"Cannot read Parquet file", file=str(pq))
            continue
        if df.is_empty():
            result.add(CRITICAL, "file_empty", f"File has 0 rows", file=str(pq))
            continue
        label = f"tick_quotes/{pq.parent.name}/{pq.name}"
        validate_schema(df, TICK_QUOTES_SCHEMA, pq, result, label)
        result.add_stat(
            type="tick_quotes", file=str(pq), rows=len(df),
            size_bytes=pq.stat().st_size,
        )
    count = len(files["tick_quotes"])
    print(f"  tick_quotes:  {count} files" + (" (none found)" if count == 0 else ""))

    # Trade-quote
    for pq in files["trade_quote"]:
        df = safe_read_parquet(pq)
        if df is None:
            result.add(CRITICAL, "file_unreadable", f"Cannot read Parquet file", file=str(pq))
            continue
        if df.is_empty():
            result.add(CRITICAL, "file_empty", f"File has 0 rows", file=str(pq))
            continue
        label = f"trade_quote/{pq.parent.name}/{pq.name}"
        validate_schema(df, TRADE_QUOTE_SCHEMA, pq, result, label)
        result.add_stat(
            type="trade_quote", file=str(pq), rows=len(df),
            size_bytes=pq.stat().st_size,
        )
    count = len(files["trade_quote"])
    print(f"  trade_quote:  {count} files" + (" (none found)" if count == 0 else ""))

    # EOD Greeks
    for pq in files["eod_greeks"]:
        df = safe_read_parquet(pq)
        if df is None:
            result.add(CRITICAL, "file_unreadable", f"Cannot read Parquet file", file=str(pq))
            continue
        if df.is_empty():
            result.add(CRITICAL, "file_empty", f"File has 0 rows", file=str(pq))
            continue
        label = f"eod/{pq.parent.name}/{pq.name}"
        validate_schema(df, EOD_GREEKS_SCHEMA, pq, result, label)
        result.add_stat(
            type="eod_greeks", file=str(pq), rows=len(df),
            size_bytes=pq.stat().st_size,
        )
    count = len(files["eod_greeks"])
    print(f"  eod_greeks:   {count} files" + (" (none found)" if count == 0 else ""))

    # EOD OI
    for pq in files["eod_oi"]:
        df = safe_read_parquet(pq)
        if df is None:
            result.add(CRITICAL, "file_unreadable", f"Cannot read Parquet file", file=str(pq))
            continue
        if df.is_empty():
            result.add(CRITICAL, "file_empty", f"File has 0 rows", file=str(pq))
            continue
        label = f"eod/{pq.parent.name}/{pq.name}"
        validate_schema(df, EOD_OI_SCHEMA, pq, result, label)
        result.add_stat(
            type="eod_oi", file=str(pq), rows=len(df),
            size_bytes=pq.stat().st_size,
        )
    count = len(files["eod_oi"])
    print(f"  eod_oi:       {count} files" + (" (none found)" if count == 0 else ""))

    # Stock OHLC
    for pq in files["stock_ohlc"]:
        df = safe_read_parquet(pq)
        if df is None:
            result.add(CRITICAL, "file_unreadable", f"Cannot read Parquet file", file=str(pq))
            continue
        if df.is_empty():
            result.add(CRITICAL, "file_empty", f"File has 0 rows", file=str(pq))
            continue
        label = f"stock_ohlc/{pq.name}"
        validate_schema(df, STOCK_OHLC_SCHEMA, pq, result, label)
        result.add_stat(
            type="stock_ohlc", file=str(pq), rows=len(df),
            size_bytes=pq.stat().st_size,
        )
    count = len(files["stock_ohlc"])
    print(f"  stock_ohlc:   {count} files" + (" (none found)" if count == 0 else ""))


def validate_thetadata_timestamps(data_dir: Path, result: ValidationResult):
    """Validate timestamps in ThetaData tick-level files."""
    files = find_thetadata_files(data_dir)

    print("\n--- ThetaData Timestamp Validation ---")

    checked = 0

    # Tick quotes — use timestamp_us
    for pq in files["tick_quotes"]:
        df = safe_read_parquet(pq)
        if df is None or df.is_empty():
            continue
        # Parse date from parent directory name (YYYY-MM-DD)
        file_date = None
        try:
            file_date = datetime.strptime(pq.parent.name, "%Y-%m-%d").date()
        except ValueError:
            pass
        ts_col = "timestamp_us" if "timestamp_us" in df.columns else "timestamp"
        label = f"tick_quotes/{pq.parent.name}/{pq.name}"
        validate_timestamps(df, ts_col, pq, file_date, result, label)
        checked += 1

    # Trade-quote — use timestamp
    for pq in files["trade_quote"]:
        df = safe_read_parquet(pq)
        if df is None or df.is_empty():
            continue
        file_date = None
        try:
            file_date = datetime.strptime(pq.parent.name, "%Y-%m-%d").date()
        except ValueError:
            pass
        ts_col = "timestamp" if "timestamp" in df.columns else "timestamp_us"
        label = f"trade_quote/{pq.parent.name}/{pq.name}"
        validate_timestamps(df, ts_col, pq, file_date, result, label)
        checked += 1

    print(f"  Checked timestamps in {checked} files")


# --- Telonex validation ---

def find_telonex_files(data_dir: Path) -> dict:
    """Discover all Telonex files organized by type."""
    tel_dir = data_dir / "telonex"
    files = {
        "book_raw": [],
        "trades_raw": [],
        "market_registry": None,
    }

    # book_raw/{slug}/{date}_book_{outcome}.parquet
    book_dir = tel_dir / "book_raw"
    if book_dir.exists():
        for slug_dir in sorted(book_dir.iterdir()):
            if slug_dir.is_dir():
                for pq in sorted(slug_dir.glob("*.parquet")):
                    files["book_raw"].append(pq)

    # trades_raw/{slug}/{date}_trades_{outcome}.parquet
    trades_dir = tel_dir / "trades_raw"
    if trades_dir.exists():
        for slug_dir in sorted(trades_dir.iterdir()):
            if slug_dir.is_dir():
                for pq in sorted(slug_dir.glob("*.parquet")):
                    files["trades_raw"].append(pq)

    # market_registry.parquet — check both locations
    for registry_path in [
        tel_dir / "market_registry.parquet",
        data_dir / "aligned" / "market_registry.parquet",
    ]:
        if registry_path.exists():
            files["market_registry"] = registry_path
            break

    return files


def validate_telonex_schema(data_dir: Path, result: ValidationResult):
    """Validate schemas of all Telonex files."""
    files = find_telonex_files(data_dir)

    print("\n--- Telonex Schema Validation ---")

    # Book raw
    for pq in files["book_raw"]:
        df = safe_read_parquet(pq)
        if df is None:
            result.add(CRITICAL, "file_unreadable", f"Cannot read Parquet file", file=str(pq))
            continue
        if df.is_empty():
            result.add(CRITICAL, "file_empty", f"File has 0 rows", file=str(pq))
            continue
        label = f"book_raw/{pq.parent.name}/{pq.name}"
        validate_schema(df, TELONEX_BOOK_SCHEMA, pq, result, label)
        # Check that at least some bid/ask columns exist
        has_bid = any(c.startswith("bid_price") or c.startswith("bids[") for c in df.columns)
        has_ask = any(c.startswith("ask_price") or c.startswith("asks[") for c in df.columns)
        if not has_bid and not has_ask:
            # May have a different column naming — just warn
            result.add(
                WARNING, "schema_no_depth_columns",
                f"No bid/ask depth columns found in {label}",
                file=str(pq),
            )
        result.add_stat(
            type="book_raw", file=str(pq), rows=len(df),
            size_bytes=pq.stat().st_size,
        )
    count = len(files["book_raw"])
    print(f"  book_raw:          {count} files" + (" (none found)" if count == 0 else ""))

    # Trades raw
    for pq in files["trades_raw"]:
        df = safe_read_parquet(pq)
        if df is None:
            result.add(CRITICAL, "file_unreadable", f"Cannot read Parquet file", file=str(pq))
            continue
        if df.is_empty():
            result.add(CRITICAL, "file_empty", f"File has 0 rows", file=str(pq))
            continue
        label = f"trades_raw/{pq.parent.name}/{pq.name}"
        validate_schema(df, TELONEX_TRADES_SCHEMA, pq, result, label)
        result.add_stat(
            type="trades_raw", file=str(pq), rows=len(df),
            size_bytes=pq.stat().st_size,
        )
    count = len(files["trades_raw"])
    print(f"  trades_raw:        {count} files" + (" (none found)" if count == 0 else ""))

    # Market registry
    if files["market_registry"]:
        pq = files["market_registry"]
        df = safe_read_parquet(pq)
        if df is None:
            result.add(CRITICAL, "file_unreadable", f"Cannot read market registry", file=str(pq))
        elif df.is_empty():
            result.add(CRITICAL, "file_empty", f"Market registry has 0 rows", file=str(pq))
        else:
            label = "market_registry.parquet"
            validate_schema(df, MARKET_REGISTRY_SCHEMA, pq, result, label)
            result.add_stat(
                type="market_registry", file=str(pq), rows=len(df),
                size_bytes=pq.stat().st_size,
            )
            print(f"  market_registry:   {len(df)} markets")
    else:
        print("  market_registry:   not found")
        result.add(WARNING, "missing_registry", "market_registry.parquet not found")


def validate_telonex_timestamps(data_dir: Path, result: ValidationResult):
    """Validate timestamps in Telonex files."""
    files = find_telonex_files(data_dir)

    print("\n--- Telonex Timestamp Validation ---")

    checked = 0
    for pq in files["book_raw"] + files["trades_raw"]:
        df = safe_read_parquet(pq)
        if df is None or df.is_empty():
            continue
        ts_col = "timestamp_us" if "timestamp_us" in df.columns else None
        if ts_col is None:
            # Try common alternatives
            for candidate in ["timestamp", "time_us", "ts"]:
                if candidate in df.columns:
                    ts_col = candidate
                    break
        if ts_col is None:
            continue

        # Parse date from filename (YYYY-MM-DD_book_*.parquet or YYYY-MM-DD_trades_*.parquet)
        file_date = None
        try:
            date_part = pq.name.split("_")[0]
            file_date = datetime.strptime(date_part, "%Y-%m-%d").date()
        except (ValueError, IndexError):
            pass

        file_type = "book_raw" if "book" in pq.parent.parent.name else "trades_raw"
        label = f"{file_type}/{pq.parent.name}/{pq.name}"
        validate_timestamps(df, ts_col, pq, file_date, result, label, check_market_hours=False)
        checked += 1

    print(f"  Checked timestamps in {checked} files")


# --- Completeness checks ---

def validate_completeness(
    data_dir: Path,
    start: date,
    end: date,
    result: ValidationResult,
):
    """Check that all expected data exists for every trading day in range."""
    expected_days = trading_days(start, end)
    td_dir = data_dir / "thetadata"
    tel_dir = data_dir / "telonex"

    print(f"\n--- Completeness Check ({start} to {end}, {len(expected_days)} trading days) ---")

    # --- ThetaData EOD ---
    eod_dir = td_dir / "eod"
    eod_present = 0
    eod_missing_days = []
    for d in expected_days:
        # Check both YYYYMMDD and YYYY-MM-DD directory names
        d_compact = d.strftime("%Y%m%d")
        d_iso = d.strftime("%Y-%m-%d")
        greeks_found = False
        for fmt in [d_compact, d_iso]:
            dir_path = eod_dir / fmt
            if dir_path.exists() and any(dir_path.glob("greeks*.parquet")):
                greeks_found = True
                break
        if greeks_found:
            eod_present += 1
        else:
            eod_missing_days.append(d)

    print(f"  EOD Greeks:       {eod_present}/{len(expected_days)} days")
    if eod_missing_days:
        missing_str = ", ".join(d.isoformat() for d in eod_missing_days[:10])
        suffix = f" (and {len(eod_missing_days) - 10} more)" if len(eod_missing_days) > 10 else ""
        result.add(
            CRITICAL, "completeness_eod_missing",
            f"Missing EOD Greeks for {len(eod_missing_days)} days: {missing_str}{suffix}",
        )

    # --- ThetaData tick quotes ---
    tq_dir = td_dir / "tick_quotes"
    tq_days_present = set()
    tq_ticker_day: dict[str, set] = {t: set() for t in ALL_TICKERS}
    if tq_dir.exists():
        for date_dir in tq_dir.iterdir():
            if date_dir.is_dir():
                try:
                    d = datetime.strptime(date_dir.name, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if start <= d <= end:
                    tq_days_present.add(d)
                    for pq in date_dir.glob("*.parquet"):
                        # Parse ticker from filename: {TICKER}_{EXPIRY}.parquet
                        parts = pq.stem.split("_")
                        if parts:
                            ticker = parts[0]
                            if ticker in tq_ticker_day:
                                tq_ticker_day[ticker].add(d)

    tq_missing = [d for d in expected_days if d not in tq_days_present]
    print(f"  Tick quotes:      {len(tq_days_present)}/{len(expected_days)} days with data")
    if tq_missing:
        missing_str = ", ".join(d.isoformat() for d in tq_missing[:10])
        suffix = f" (and {len(tq_missing) - 10} more)" if len(tq_missing) > 10 else ""
        result.add(
            WARNING, "completeness_tick_quotes_missing",
            f"Missing tick quote days: {missing_str}{suffix}",
        )

    # Per-ticker completeness
    for ticker in ALL_TICKERS:
        days_with_data = len(tq_ticker_day[ticker])
        if days_with_data > 0 and days_with_data < len(expected_days):
            missing_count = len(expected_days) - days_with_data
            result.add(
                INFO, "completeness_ticker_partial",
                f"Tick quotes for {ticker}: {days_with_data}/{len(expected_days)} days "
                f"({missing_count} missing)",
            )

    # --- ThetaData stock OHLC ---
    ohlc_dir = td_dir / "stock_ohlc"
    ohlc_present = []
    ohlc_missing = []
    for ticker in OHLC_TICKERS:
        found = False
        if ohlc_dir.exists():
            for pq in ohlc_dir.glob(f"{ticker}*.parquet"):
                found = True
                break
        if found:
            ohlc_present.append(ticker)
        else:
            ohlc_missing.append(ticker)

    print(f"  Stock OHLC:       {len(ohlc_present)}/{len(OHLC_TICKERS)} tickers")
    if ohlc_missing:
        result.add(
            WARNING, "completeness_ohlc_missing",
            f"Missing stock OHLC for: {', '.join(ohlc_missing)}",
        )

    # --- Telonex ---
    tel_files = find_telonex_files(data_dir)
    book_slugs: set[str] = set()
    trades_slugs: set[str] = set()
    for pq in tel_files["book_raw"]:
        book_slugs.add(pq.parent.name)
    for pq in tel_files["trades_raw"]:
        trades_slugs.add(pq.parent.name)

    print(f"  Telonex books:    {len(tel_files['book_raw'])} files across {len(book_slugs)} markets")
    print(f"  Telonex trades:   {len(tel_files['trades_raw'])} files across {len(trades_slugs)} markets")

    # Check registry coverage
    if tel_files["market_registry"]:
        registry = safe_read_parquet(tel_files["market_registry"])
        if registry is not None and not registry.is_empty():
            registry_slugs = set(registry["market_slug"].to_list())
            missing_books = registry_slugs - book_slugs
            missing_trades = registry_slugs - trades_slugs

            if missing_books:
                sample = list(missing_books)[:5]
                result.add(
                    WARNING, "completeness_telonex_book_missing",
                    f"{len(missing_books)} registry markets missing book data "
                    f"(e.g. {', '.join(sample)})",
                )
            if missing_trades:
                sample = list(missing_trades)[:5]
                result.add(
                    WARNING, "completeness_telonex_trades_missing",
                    f"{len(missing_trades)} registry markets missing trades data "
                    f"(e.g. {', '.join(sample)})",
                )

    # --- Cross-source: do both ThetaData and Telonex exist per day? ---
    telonex_dates: set[date] = set()
    for pq in tel_files["book_raw"] + tel_files["trades_raw"]:
        try:
            date_part = pq.name.split("_")[0]
            d = datetime.strptime(date_part, "%Y-%m-%d").date()
            if start <= d <= end:
                telonex_dates.add(d)
        except (ValueError, IndexError):
            pass

    theta_dates = tq_days_present | set(d for d in expected_days if d not in eod_missing_days)
    both_dates = theta_dates & telonex_dates
    print(f"  Cross-source:     {len(both_dates)} days with both ThetaData + Telonex")

    only_theta = theta_dates - telonex_dates
    only_telonex = telonex_dates - theta_dates
    if only_theta:
        result.add(
            INFO, "completeness_cross_source",
            f"{len(only_theta)} days have ThetaData but no Telonex data",
        )
    if only_telonex:
        result.add(
            INFO, "completeness_cross_source",
            f"{len(only_telonex)} days have Telonex but no ThetaData data",
        )


# --- Cross-source consistency ---

def validate_cross_source(data_dir: Path, result: ValidationResult):
    """Check consistency between EOD Greeks underlying_price and stock OHLC close."""
    td_dir = data_dir / "thetadata"
    eod_dir = td_dir / "eod"
    ohlc_dir = td_dir / "stock_ohlc"

    print("\n--- Cross-Source Consistency ---")

    if not eod_dir.exists() or not ohlc_dir.exists():
        print("  Skipped (EOD or OHLC directory missing)")
        return

    # Load all stock OHLC files into a dict: ticker -> DataFrame
    ohlc_data: dict[str, pl.DataFrame] = {}
    for pq in ohlc_dir.glob("*.parquet"):
        ticker = pq.stem.split("_")[0]
        df = safe_read_parquet(pq)
        if df is not None and not df.is_empty():
            ohlc_data[ticker] = df

    if not ohlc_data:
        print("  Skipped (no OHLC data loaded)")
        return

    checks = 0
    mismatches = 0

    for date_dir in sorted(eod_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        for pq in date_dir.glob("greeks*.parquet"):
            greeks = safe_read_parquet(pq)
            if greeks is None or greeks.is_empty():
                continue
            if "underlying_price" not in greeks.columns or "symbol" not in greeks.columns:
                continue

            # Group by symbol, get mean underlying_price
            try:
                ticker_prices = (
                    greeks
                    .group_by("symbol")
                    .agg(pl.col("underlying_price").mean().alias("greeks_price"))
                )
            except Exception:
                continue

            for row in ticker_prices.iter_rows(named=True):
                ticker = row["symbol"]
                greeks_price = row["greeks_price"]
                if greeks_price is None or greeks_price <= 0:
                    continue

                if ticker not in ohlc_data:
                    continue

                ohlc_df = ohlc_data[ticker]
                if "close" not in ohlc_df.columns:
                    continue

                # Try to match by date — parse date from directory name
                try:
                    dir_name = date_dir.name
                    if len(dir_name) == 8:
                        file_date = datetime.strptime(dir_name, "%Y%m%d").date()
                    else:
                        file_date = datetime.strptime(dir_name, "%Y-%m-%d").date()
                except ValueError:
                    continue

                # Find OHLC close for this date — need a timestamp or date column
                # OHLC may have a 'timestamp' or 'date' column; try to filter
                ohlc_close = None
                if "timestamp" in ohlc_df.columns:
                    # Timestamp may be in ms or us — try to extract date
                    try:
                        ts_col = ohlc_df["timestamp"]
                        # Convert to date: assume ms from epoch
                        ohlc_with_date = ohlc_df.with_columns(
                            (pl.col("timestamp") * 1000)
                            .cast(pl.Datetime("us"))
                            .dt.date()
                            .alias("_date")
                        )
                        day_data = ohlc_with_date.filter(pl.col("_date") == file_date)
                        if not day_data.is_empty():
                            ohlc_close = day_data["close"].last()
                    except Exception:
                        pass

                if ohlc_close is None or ohlc_close <= 0:
                    continue

                checks += 1
                pct_diff = abs(greeks_price - ohlc_close) / ohlc_close
                if pct_diff > 0.005:  # 0.5% tolerance
                    mismatches += 1
                    result.add(
                        WARNING, "cross_source_price_mismatch",
                        f"{ticker} on {file_date}: Greeks underlying={greeks_price:.2f}, "
                        f"OHLC close={ohlc_close:.2f} (diff={pct_diff:.2%})",
                    )

    print(f"  Price comparisons: {checks} checked, {mismatches} mismatches (>0.5%)")


# --- Quality metrics report ---

def generate_quality_report(
    data_dir: Path,
    start: date | None,
    end: date | None,
    result: ValidationResult,
):
    """Generate detailed data quality metrics."""
    td_files = find_thetadata_files(data_dir)
    tel_files = find_telonex_files(data_dir)

    print("\n--- Data Quality Metrics ---")

    # --- ThetaData tick quotes ---
    if td_files["tick_quotes"]:
        total_rows = 0
        total_bytes = 0
        strikes_per_file = []
        expiries_per_date: dict[str, set] = {}

        for pq in td_files["tick_quotes"]:
            size = pq.stat().st_size
            total_bytes += size
            df = safe_read_parquet(pq)
            if df is None or df.is_empty():
                continue
            total_rows += len(df)

            if "strike" in df.columns:
                strikes_per_file.append(df["strike"].n_unique())
            if "expiration" in df.columns:
                date_key = pq.parent.name
                if date_key not in expiries_per_date:
                    expiries_per_date[date_key] = set()
                expiries_per_date[date_key].update(df["expiration"].unique().to_list())

        print(f"\n  Tick Quotes:")
        print(f"    Files:           {len(td_files['tick_quotes'])}")
        print(f"    Total rows:      {total_rows:,}")
        print(f"    Total size:      {format_size(total_bytes)}")
        if strikes_per_file:
            print(f"    Strikes/file:    min={min(strikes_per_file)}, "
                  f"max={max(strikes_per_file)}, "
                  f"mean={sum(strikes_per_file)/len(strikes_per_file):.0f}")
        for dt_key, exps in sorted(expiries_per_date.items())[:5]:
            print(f"    {dt_key}: {len(exps)} unique expiries")

        result.add(
            INFO, "quality_tick_quotes",
            f"{len(td_files['tick_quotes'])} files, {total_rows:,} rows, {format_size(total_bytes)}",
        )

    # --- ThetaData EOD Greeks ---
    if td_files["eod_greeks"]:
        total_rows = 0
        total_bytes = 0
        for pq in td_files["eod_greeks"]:
            total_bytes += pq.stat().st_size
            df = safe_read_parquet(pq)
            if df is not None:
                total_rows += len(df)

        print(f"\n  EOD Greeks:")
        print(f"    Files:           {len(td_files['eod_greeks'])}")
        print(f"    Total rows:      {total_rows:,}")
        print(f"    Total size:      {format_size(total_bytes)}")

        result.add(
            INFO, "quality_eod_greeks",
            f"{len(td_files['eod_greeks'])} files, {total_rows:,} rows, {format_size(total_bytes)}",
        )

    # --- Telonex books ---
    if tel_files["book_raw"]:
        total_rows = 0
        total_bytes = 0
        intervals: list[float] = []

        for pq in tel_files["book_raw"]:
            total_bytes += pq.stat().st_size
            df = safe_read_parquet(pq)
            if df is None or df.is_empty():
                continue
            total_rows += len(df)

            # Compute median snapshot interval
            ts_col = "timestamp_us" if "timestamp_us" in df.columns else None
            if ts_col and len(df) > 1:
                diffs = df[ts_col].diff().drop_nulls()
                diffs_pos = diffs.filter(diffs > 0)
                if len(diffs_pos) > 0:
                    median_us = diffs_pos.median()
                    if median_us is not None:
                        intervals.append(median_us / 1_000_000)  # to seconds

        print(f"\n  Telonex Books:")
        print(f"    Files:           {len(tel_files['book_raw'])}")
        print(f"    Total rows:      {total_rows:,}")
        print(f"    Total size:      {format_size(total_bytes)}")
        if intervals:
            avg_interval = sum(intervals) / len(intervals)
            print(f"    Snapshot freq:   median {avg_interval:.2f}s across {len(intervals)} files")

        result.add(
            INFO, "quality_telonex_books",
            f"{len(tel_files['book_raw'])} files, {total_rows:,} rows, {format_size(total_bytes)}",
        )

    # --- Telonex trades ---
    if tel_files["trades_raw"]:
        total_rows = 0
        total_bytes = 0
        trades_per_file: list[int] = []

        for pq in tel_files["trades_raw"]:
            total_bytes += pq.stat().st_size
            df = safe_read_parquet(pq)
            if df is None or df.is_empty():
                continue
            total_rows += len(df)
            trades_per_file.append(len(df))

        print(f"\n  Telonex Trades:")
        print(f"    Files:           {len(tel_files['trades_raw'])}")
        print(f"    Total rows:      {total_rows:,}")
        print(f"    Total size:      {format_size(total_bytes)}")
        if trades_per_file:
            print(f"    Trades/file:     min={min(trades_per_file)}, "
                  f"max={max(trades_per_file)}, "
                  f"mean={sum(trades_per_file)/len(trades_per_file):.0f}")

        result.add(
            INFO, "quality_telonex_trades",
            f"{len(tel_files['trades_raw'])} files, {total_rows:,} rows, {format_size(total_bytes)}",
        )

    # --- Null percentages (sample up to 5 files per type) ---
    print(f"\n  Null Percentages (sampled):")
    for type_key, file_list in [
        ("tick_quotes", td_files["tick_quotes"][:5]),
        ("eod_greeks", td_files["eod_greeks"][:3]),
        ("book_raw", tel_files["book_raw"][:5]),
    ]:
        if not file_list:
            continue
        for pq in file_list:
            df = safe_read_parquet(pq)
            if df is None or df.is_empty():
                continue
            null_pcts = {}
            for col in df.columns:
                null_count = df[col].null_count()
                if null_count > 0:
                    null_pcts[col] = round(null_count / len(df) * 100, 1)
            if null_pcts:
                cols_str = ", ".join(f"{c}={p}%" for c, p in sorted(null_pcts.items())[:5])
                print(f"    {type_key}/{pq.name}: {cols_str}")


# --- Spot checks ---

def run_spot_checks(data_dir: Path, result: ValidationResult):
    """Run deep spot-check validations on sampled files."""
    td_files = find_thetadata_files(data_dir)

    print("\n--- Spot Checks ---")

    # Tick quotes: bid <= ask
    checked_tq = 0
    for pq in td_files["tick_quotes"][:3]:
        df = safe_read_parquet(pq)
        if df is None or df.is_empty():
            continue
        if "bid" in df.columns and "ask" in df.columns:
            valid = df.filter(
                (pl.col("bid").is_not_null()) & (pl.col("ask").is_not_null())
            )
            if len(valid) > 0:
                violations = valid.filter(pl.col("bid") > pl.col("ask"))
                pct_good = (1 - len(violations) / len(valid)) * 100
                label = f"tick_quotes/{pq.parent.name}/{pq.name}"
                if pct_good < 99.9:
                    result.add(
                        WARNING, "spot_check_bid_ask",
                        f"bid <= ask in {pct_good:.2f}% of rows ({len(violations)} violations) "
                        f"in {label}",
                        file=str(pq),
                    )
                else:
                    print(f"  bid<=ask: {pct_good:.2f}% valid in {pq.name}")
                checked_tq += 1

    # EOD Greeks: strike monotonicity for calls (price should decrease with strike)
    for pq in td_files["eod_greeks"][:2]:
        df = safe_read_parquet(pq)
        if df is None or df.is_empty():
            continue
        if all(c in df.columns for c in ["symbol", "expiration", "right", "strike", "bid"]):
            calls = df.filter(pl.col("right") == "C")
            if not calls.is_empty():
                # For each (symbol, expiration), check strike monotonicity of bid
                groups = calls.group_by(["symbol", "expiration"]).agg(
                    pl.col("strike").sort().alias("strikes"),
                    pl.col("bid").sort_by("strike").alias("bids"),
                )
                violations = 0
                total_groups = len(groups)
                for row in groups.iter_rows(named=True):
                    bids = [b for b in row["bids"] if b is not None and b > 0]
                    if len(bids) < 3:
                        continue
                    # Check that bids are generally non-increasing
                    increases = sum(1 for i in range(1, len(bids)) if bids[i] > bids[i-1] * 1.01)
                    if increases > len(bids) * 0.1:
                        violations += 1

                if violations > 0:
                    result.add(
                        WARNING, "spot_check_strike_monotonicity",
                        f"{violations}/{total_groups} call chains have non-monotonic bids "
                        f"in {pq.name}",
                        file=str(pq),
                    )
                else:
                    print(f"  Strike monotonicity: OK in {pq.name}")

    if checked_tq == 0 and not td_files["tick_quotes"]:
        print("  No tick quote files to spot-check")


# --- Row count bounds ---

def validate_row_counts(data_dir: Path, result: ValidationResult):
    """Check that tick-level files meet minimum row thresholds."""
    td_files = find_thetadata_files(data_dir)

    low_count = 0
    for pq in td_files["tick_quotes"]:
        df = safe_read_parquet(pq)
        if df is None:
            continue
        if 0 < len(df) < 1000:
            low_count += 1
            result.add(
                WARNING, "row_count_bounds",
                f"Only {len(df)} rows (threshold: 1000)",
                file=str(pq),
            )

    if low_count > 0:
        print(f"  {low_count} tick quote files below 1000-row threshold")


# --- CLI commands ---

def cmd_all(args):
    """Run full validation suite."""
    data_dir = get_data_dir()
    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else None
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else None
    result = ValidationResult()

    print(f"Data directory: {data_dir}")
    if start and end:
        print(f"Date range: {start} to {end}")

    # Schema
    validate_thetadata_schema(data_dir, result)
    validate_telonex_schema(data_dir, result)

    # Timestamps
    validate_thetadata_timestamps(data_dir, result)
    validate_telonex_timestamps(data_dir, result)

    # Row counts
    validate_row_counts(data_dir, result)

    # Completeness
    if start and end:
        validate_completeness(data_dir, start, end, result)

    # Cross-source
    validate_cross_source(data_dir, result)

    # Spot checks
    run_spot_checks(data_dir, result)

    # Quality metrics
    generate_quality_report(data_dir, start, end, result)

    result.print_summary()

    if args.save_report:
        _save_report(data_dir, result, start, end)


def cmd_schema(args):
    """Run schema validation only."""
    data_dir = get_data_dir()
    result = ValidationResult()

    print(f"Data directory: {data_dir}")

    if args.source in ("thetadata", "all"):
        validate_thetadata_schema(data_dir, result)
    if args.source in ("telonex", "all"):
        validate_telonex_schema(data_dir, result)

    result.print_summary()

    if args.save_report:
        _save_report(data_dir, result)


def cmd_completeness(args):
    """Run completeness checks only."""
    data_dir = get_data_dir()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    result = ValidationResult()

    print(f"Data directory: {data_dir}")
    print(f"Date range: {start} to {end}")

    validate_completeness(data_dir, start, end, result)
    result.print_summary()

    if args.save_report:
        _save_report(data_dir, result, start, end)


def cmd_report(args):
    """Generate and save a quality report."""
    data_dir = get_data_dir()
    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else None
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else None
    result = ValidationResult()

    print(f"Data directory: {data_dir}")

    # Run everything for a comprehensive report
    validate_thetadata_schema(data_dir, result)
    validate_telonex_schema(data_dir, result)
    validate_thetadata_timestamps(data_dir, result)
    validate_telonex_timestamps(data_dir, result)
    validate_row_counts(data_dir, result)
    if start and end:
        validate_completeness(data_dir, start, end, result)
    validate_cross_source(data_dir, result)
    run_spot_checks(data_dir, result)
    generate_quality_report(data_dir, start, end, result)

    result.print_summary()
    _save_report(data_dir, result, start, end)


def _save_report(
    data_dir: Path,
    result: ValidationResult,
    start: date | None = None,
    end: date | None = None,
):
    """Save validation report as JSON."""
    report = result.to_dict()
    if start and end:
        report["date_range"] = [start.isoformat(), end.isoformat()]

    report_path = data_dir / "validation_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Report saved to: {report_path}")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Data validation for backtester v1.0 (ThetaData + Telonex)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # all
    p_all = sub.add_parser("all", help="Full validation suite")
    p_all.add_argument("--start", help="Start date (YYYY-MM-DD)")
    p_all.add_argument("--end", help="End date (YYYY-MM-DD)")
    p_all.add_argument("--save-report", action="store_true", help="Save JSON report to data_dir")
    p_all.set_defaults(func=cmd_all)

    # schema
    p_schema = sub.add_parser("schema", help="Schema validation only")
    p_schema.add_argument(
        "--source", choices=["thetadata", "telonex", "all"], default="all",
        help="Which data source to validate (default: all)",
    )
    p_schema.add_argument("--save-report", action="store_true", help="Save JSON report to data_dir")
    p_schema.set_defaults(func=cmd_schema)

    # completeness
    p_comp = sub.add_parser("completeness", help="Completeness checks only")
    p_comp.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    p_comp.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    p_comp.add_argument("--save-report", action="store_true", help="Save JSON report to data_dir")
    p_comp.set_defaults(func=cmd_completeness)

    # report
    p_report = sub.add_parser("report", help="Generate full quality report (saves JSON)")
    p_report.add_argument("--start", help="Start date (YYYY-MM-DD)")
    p_report.add_argument("--end", help="End date (YYYY-MM-DD)")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
