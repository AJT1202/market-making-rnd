"""
Load and align Telonex orderbook data with NVDA price data.
"""

from pathlib import Path
from dataclasses import dataclass

import numpy as np
import pandas as pd


DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "telonex" / "nvda-poc"

STRIKES = [160, 165, 170, 175, 180]

# Map strike -> parquet filename
BOOK_FILES = {
    strike: DATA_DIR / f"book_snapshot_25_strike{strike}_2026-03-30.parquet"
    for strike in STRIKES
}

NVDA_FILE = DATA_DIR / "nvda_prices_1m.parquet"

# Price and size columns in the orderbook data
PRICE_SIZE_COLS = []
for side in ("bid", "ask"):
    for i in range(25):
        PRICE_SIZE_COLS.append(f"{side}_price_{i}")
        PRICE_SIZE_COLS.append(f"{side}_size_{i}")


@dataclass
class MarketData:
    """Container for all loaded and aligned market data."""
    # Dict of strike -> DataFrame with orderbook snapshots
    books: dict[int, pd.DataFrame]
    # NVDA 1-min price DataFrame (index = UTC timestamp)
    nvda_prices: pd.DataFrame
    # Unified timeline: list of (timestamp_us, strike, row_index) sorted by time
    timeline: pd.DataFrame


def load_orderbook(strike: int) -> pd.DataFrame:
    """Load a single strike's orderbook data, converting strings to floats."""
    path = BOOK_FILES[strike]
    print(f"  Loading {path.name} ...", end=" ")
    df = pd.read_parquet(path)

    # Convert all price/size columns from string to float
    for col in PRICE_SIZE_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Compute BBO columns for convenience using concat to avoid fragmentation
    bbo = pd.DataFrame({
        "best_bid": df["bid_price_0"],
        "best_ask": df["ask_price_0"],
        "best_bid_size": df["bid_size_0"],
        "best_ask_size": df["ask_size_0"],
    }, index=df.index)
    bbo["mid"] = (bbo["best_bid"] + bbo["best_ask"]) / 2.0
    bbo["spread"] = bbo["best_ask"] - bbo["best_bid"]
    df = pd.concat([df, bbo], axis=1)

    # Filter out rows with invalid BBO
    valid_mask = (
        df["best_bid"].notna()
        & df["best_ask"].notna()
        & (df["best_bid"] > 0)
        & (df["best_ask"] > 0)
        & (df["best_ask"] > df["best_bid"])
    )
    n_before = len(df)
    df = df[valid_mask].reset_index(drop=True)
    n_after = len(df)

    # Add strike column
    df["strike"] = strike

    # Convert timestamp_us to datetime for convenience
    df["datetime_utc"] = pd.to_datetime(df["timestamp_us"], unit="us", utc=True)

    print(f"{n_after} rows ({n_before - n_after} filtered)")
    return df


def load_nvda_prices() -> pd.DataFrame:
    """Load NVDA 1-min price data, convert index to UTC."""
    print("  Loading nvda_prices_1m.parquet ...", end=" ")
    df = pd.read_parquet(NVDA_FILE)

    # Convert index from US/Eastern to UTC
    df.index = df.index.tz_convert("UTC")
    df.index.name = "datetime_utc"

    # Keep only Close for forward-fill alignment
    df = df[["Close"]].rename(columns={"Close": "nvda_close"})
    df["timestamp_us"] = (df.index.astype(np.int64) // 1000).astype(np.int64)

    print(f"{len(df)} rows")
    return df


def align_nvda_to_books(
    books: dict[int, pd.DataFrame], nvda_prices: pd.DataFrame
) -> dict[int, pd.DataFrame]:
    """
    For each orderbook snapshot, find the most recent NVDA 1-min close price.
    Adds 'nvda_price' column to each book DataFrame via asof merge.
    """
    print("  Aligning NVDA prices to orderbook snapshots ...")

    nvda_for_merge = nvda_prices[["nvda_close"]].copy()
    nvda_for_merge["timestamp_us"] = (
        nvda_for_merge.index.astype(np.int64) // 1000
    ).astype(np.int64)
    nvda_for_merge = nvda_for_merge.sort_values("timestamp_us")

    aligned = {}
    for strike, df in books.items():
        df = df.sort_values("timestamp_us").reset_index(drop=True)

        # asof merge: for each book timestamp, get the most recent nvda price
        merged = pd.merge_asof(
            df,
            nvda_for_merge[["timestamp_us", "nvda_close"]],
            on="timestamp_us",
            direction="backward",
        )
        merged = merged.rename(columns={"nvda_close": "nvda_price"})

        # Drop rows where we don't have a corresponding NVDA price yet
        n_before = len(merged)
        merged = merged.dropna(subset=["nvda_price"]).reset_index(drop=True)
        n_dropped = n_before - len(merged)
        if n_dropped > 0:
            print(f"    Strike {strike}: dropped {n_dropped} rows without NVDA price")

        aligned[strike] = merged

    return aligned


def build_timeline(books: dict[int, pd.DataFrame]) -> pd.DataFrame:
    """
    Build a unified timeline of all orderbook events across all strikes,
    sorted chronologically. Uses vectorized concat instead of row-by-row loop.
    """
    print("  Building unified timeline ...")
    frames = []
    for strike, df in books.items():
        frame = pd.DataFrame({
            "timestamp_us": df["timestamp_us"].values,
            "strike": strike,
            "row_idx": np.arange(len(df)),
        })
        frames.append(frame)
    timeline = pd.concat(frames, ignore_index=True)
    timeline = timeline.sort_values("timestamp_us").reset_index(drop=True)
    print(f"    {len(timeline)} total events across all strikes")
    return timeline


def load_all_data() -> MarketData:
    """Main entry point: load all data, align, and return MarketData."""
    print("Loading market data ...")

    # Load orderbooks
    books_raw = {}
    for strike in STRIKES:
        books_raw[strike] = load_orderbook(strike)

    # Load NVDA prices
    nvda_prices = load_nvda_prices()

    # Align NVDA prices to book snapshots
    books = align_nvda_to_books(books_raw, nvda_prices)

    # Build unified timeline
    timeline = build_timeline(books)

    print("Data loading complete.\n")
    return MarketData(books=books, nvda_prices=nvda_prices, timeline=timeline)


if __name__ == "__main__":
    data = load_all_data()
    for strike, df in data.books.items():
        print(f"Strike {strike}: {len(df)} snapshots, "
              f"NVDA range [{df['nvda_price'].min():.2f}, {df['nvda_price'].max():.2f}]")
