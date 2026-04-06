"""
Download NVDA IV data at multiple granularities for B-L probability accuracy comparison.

Downloads IV data for NVDA options expiring 2026-04-01, on trading date 2026-03-30,
at three intervals: tick, 1s, 1m.

Output: D:/data/thetadata/granularity_test/
"""

import io
import sys
import time
from pathlib import Path

import httpx
import polars as pl

BASE_URL = "http://127.0.0.1:25503/v3"
OUT_DIR = Path("D:/data/thetadata/granularity_test")
OUT_DIR.mkdir(parents=True, exist_ok=True)

INTERVALS = ["tick", "1s", "1m"]

COMMON_PARAMS = {
    "symbol": "NVDA",
    "expiration": "20260401",
    "right": "both",
    "date": "20260330",
    "start_time": "09:30:00",
    "end_time": "16:15:00",
    "strike_range": "15",
    "format": "ndjson",
}


def download_interval(interval: str) -> pl.DataFrame | None:
    """Download IV data for a single interval."""
    params = {**COMMON_PARAMS, "interval": interval}
    print(f"\n{'='*60}")
    print(f"Downloading interval={interval}...")
    print(f"  URL: {BASE_URL}/option/history/greeks/implied_volatility")
    print(f"  Params: {params}")

    t0 = time.perf_counter()
    try:
        r = httpx.get(
            f"{BASE_URL}/option/history/greeks/implied_volatility",
            params=params,
            timeout=600.0,
        )
    except httpx.ConnectError:
        print("  ERROR: Cannot connect to ThetaData terminal at 127.0.0.1:25503")
        print("  Make sure ThetaTerminal is running.")
        return None
    except httpx.ReadTimeout:
        print("  ERROR: Request timed out after 600s")
        return None

    elapsed = time.perf_counter() - t0

    if r.status_code != 200:
        print(f"  ERROR: HTTP {r.status_code}")
        print(f"  Response: {r.text[:500]}")
        return None

    # Check for empty response
    if not r.content or r.content.strip() == b"":
        print(f"  ERROR: Empty response body")
        return None

    print(f"  Response received in {elapsed:.1f}s, {len(r.content):,} bytes")

    # Parse NDJSON
    try:
        df = pl.read_ndjson(io.BytesIO(r.content))
    except Exception as e:
        print(f"  ERROR parsing NDJSON: {e}")
        # Show first few bytes for debugging
        print(f"  First 500 bytes: {r.content[:500]}")
        return None

    # Save to parquet
    out_path = OUT_DIR / f"nvda_20260401_20260330_{interval}.parquet"
    df.write_parquet(out_path, compression="zstd")
    file_size = out_path.stat().st_size

    print(f"  Rows: {len(df):,}")
    print(f"  Columns: {df.columns}")
    print(f"  Saved: {out_path} ({file_size:,} bytes)")

    return df


def print_summary(results: dict[str, pl.DataFrame]):
    """Print comparison summary of all downloaded intervals."""
    print(f"\n{'='*60}")
    print("SUMMARY: Granularity Comparison")
    print(f"{'='*60}")

    # Row counts and file sizes
    print(f"\n{'Interval':<12} {'Rows':>12} {'File Size':>15} {'Columns':>10}")
    print("-" * 52)
    for interval in INTERVALS:
        if interval not in results:
            print(f"{interval:<12} {'FAILED':>12}")
            continue
        df = results[interval]
        path = OUT_DIR / f"nvda_20260401_20260330_{interval}.parquet"
        size = path.stat().st_size
        print(f"{interval:<12} {len(df):>12,} {size:>12,} KB {len(df.columns):>8}")

    # Column comparison
    if len(results) >= 2:
        cols_list = [set(df.columns) for df in results.values()]
        if all(c == cols_list[0] for c in cols_list):
            print(f"\nColumns identical across all intervals: {sorted(cols_list[0])}")
        else:
            print("\nWARNING: Columns differ between intervals!")
            for interval, df in results.items():
                print(f"  {interval}: {sorted(df.columns)}")

    # Sample timestamps from each
    print(f"\n--- Sample timestamps (first 5 rows) ---")
    for interval, df in results.items():
        if "timestamp" in df.columns:
            ts_col = df["timestamp"].head(5).to_list()
            print(f"  {interval}: {ts_col}")

    # Unique strikes
    for interval, df in results.items():
        if "strike" in df.columns:
            strikes = sorted(df["strike"].unique().to_list())
            print(f"\n  {interval} strikes ({len(strikes)}): {strikes[:5]} ... {strikes[-5:]}")

    # Unique rights
    for interval, df in results.items():
        if "right" in df.columns:
            rights = df["right"].unique().to_list()
            print(f"  {interval} rights: {rights}")

    # Tick data: median interval between quotes
    if "tick" in results:
        df_tick = results["tick"]
        if "timestamp" in df_tick.columns:
            print(f"\n--- Tick data: interval analysis ---")
            # Parse timestamps and compute diffs per contract
            try:
                ts_series = df_tick["timestamp"].cast(pl.Datetime("ms"))
                # Get a single strike/right combination to analyze intervals
                if "strike" in df_tick.columns and "right" in df_tick.columns:
                    sample_strike = df_tick["strike"].mode().first()
                    sample_right = "call"
                    subset = df_tick.filter(
                        (pl.col("strike") == sample_strike) & (pl.col("right") == sample_right)
                    ).sort("timestamp")
                    if len(subset) > 1:
                        ts = subset["timestamp"].cast(pl.Datetime("ms"))
                        diffs = ts.diff().drop_nulls().dt.total_milliseconds()
                        print(f"  Sample contract: strike={sample_strike}, right={sample_right}")
                        print(f"  Quotes for this contract: {len(subset):,}")
                        print(f"  Median interval: {diffs.median():.0f} ms")
                        print(f"  Mean interval:   {diffs.mean():.0f} ms")
                        print(f"  Min interval:    {diffs.min():.0f} ms")
                        print(f"  Max interval:    {diffs.max():.0f} ms")
            except Exception as e:
                print(f"  Could not analyze tick intervals: {e}")

    # 1s data: verify timestamps at 1-second intervals
    if "1s" in results:
        df_1s = results["1s"]
        if "timestamp" in df_1s.columns and "strike" in df_1s.columns:
            print(f"\n--- 1s data: interval verification ---")
            try:
                sample_strike = df_1s["strike"].mode().first()
                subset = df_1s.filter(
                    (pl.col("strike") == sample_strike) & (pl.col("right") == "call")
                ).sort("timestamp")
                if len(subset) > 1:
                    ts = subset["timestamp"].cast(pl.Datetime("ms"))
                    diffs = ts.diff().drop_nulls().dt.total_milliseconds()
                    print(f"  Sample contract: strike={sample_strike}, right=call")
                    print(f"  Rows for this contract: {len(subset):,}")
                    print(f"  Median interval: {diffs.median():.0f} ms (expected: 1000)")
                    print(f"  Unique intervals: {sorted(diffs.unique().to_list())[:10]}")
            except Exception as e:
                print(f"  Could not verify 1s intervals: {e}")

    # 1m data: verify timestamps at 1-minute intervals
    if "1m" in results:
        df_1m = results["1m"]
        if "timestamp" in df_1m.columns and "strike" in df_1m.columns:
            print(f"\n--- 1m data: interval verification ---")
            try:
                sample_strike = df_1m["strike"].mode().first()
                subset = df_1m.filter(
                    (pl.col("strike") == sample_strike) & (pl.col("right") == "call")
                ).sort("timestamp")
                if len(subset) > 1:
                    ts = subset["timestamp"].cast(pl.Datetime("ms"))
                    diffs = ts.diff().drop_nulls().dt.total_milliseconds()
                    print(f"  Sample contract: strike={sample_strike}, right=call")
                    print(f"  Rows for this contract: {len(subset):,}")
                    print(f"  Median interval: {diffs.median():.0f} ms (expected: 60000)")
                    print(f"  Unique intervals: {sorted(diffs.unique().to_list())[:10]}")
            except Exception as e:
                print(f"  Could not verify 1m intervals: {e}")


def main():
    print("NVDA IV Granularity Download")
    print(f"Output directory: {OUT_DIR}")
    print(f"Intervals: {INTERVALS}")
    print(f"Expiration: 2026-04-01, Date: 2026-03-30")
    print(f"Strike range: 15 (approx 30 strikes around ATM)")

    results: dict[str, pl.DataFrame] = {}

    for interval in INTERVALS:
        df = download_interval(interval)
        if df is not None:
            results[interval] = df

    if results:
        print_summary(results)
    else:
        print("\nNo data was downloaded successfully.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Done. {len(results)}/{len(INTERVALS)} intervals downloaded successfully.")
    print(f"Files saved to: {OUT_DIR}")


if __name__ == "__main__":
    main()
