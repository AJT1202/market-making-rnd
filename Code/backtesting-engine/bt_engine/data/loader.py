"""DataLoader: loads Telonex parquet files into a DataStore.

Uses vectorized numpy operations. BookSnapshot objects are lightweight
views into pre-allocated arrays, not materialized frozen dataclasses.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from bt_engine.config import EngineConfig
from bt_engine.types import EventKind, FillMode, Side, TokenSide
from bt_engine.data.schema import (
    BookSnapshot,
    TimelineEvent,
    TradeEvent,
    UnderlyingPrice,
)
from bt_engine.data.store import DataStore


class DataLoader:
    """Loads Telonex data and builds a DataStore."""

    def __init__(self, config: EngineConfig):
        self.config = config
        self._sequence = 0

    def load(self) -> DataStore:
        store = DataStore(
            strikes=self.config.event.strikes,
            fill_mode=self.config.fill.mode,
        )

        for market in self.config.event.markets:
            for ts_str in market.token_side_available:
                token_side = TokenSide(ts_str)
                self._load_book_snapshots(store, market.strike, token_side)

        if self.config.fill.mode == FillMode.TRADE_DRIVEN:
            for market in self.config.event.markets:
                for ts_str in market.token_side_available:
                    token_side = TokenSide(ts_str)
                    self._load_trades(store, market.strike, token_side)

        if self.config.underlying_price_file is not None:
            self._load_underlying_prices(store)

        store.timeline.sort()
        return store

    def _load_book_snapshots(
        self, store: DataStore, strike: int, token_side: TokenSide
    ) -> None:
        path = self._find_book_file(strike, token_side)
        if path is None:
            print(f"  WARNING: No book file for strike={strike} token={token_side.value}")
            return

        df = pd.read_parquet(path)
        n_rows = len(df)
        max_level = self._detect_depth_levels(df)

        # Convert all price/size columns to float64 at once
        for side in ("bid", "ask"):
            for i in range(max_level):
                for suffix in ("price", "size"):
                    col = f"{side}_{suffix}_{i}"
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

        # Extract timestamps
        ts_arr = df["timestamp_us"].values.astype(np.int64)
        local_col = "local_timestamp_us" if "local_timestamp_us" in df.columns else "timestamp_us"
        local_ts_arr = df[local_col].values.astype(np.int64)

        # Build 2D arrays: (n_rows, max_level) for bid/ask prices/sizes in ticks/cs
        bid_price_2d = np.zeros((n_rows, max_level), dtype=np.int64)
        bid_size_2d = np.zeros((n_rows, max_level), dtype=np.int64)
        ask_price_2d = np.zeros((n_rows, max_level), dtype=np.int64)
        ask_size_2d = np.zeros((n_rows, max_level), dtype=np.int64)

        for i in range(max_level):
            bp = f"bid_price_{i}"
            bs = f"bid_size_{i}"
            ap = f"ask_price_{i}"
            asf = f"ask_size_{i}"

            if bp in df.columns:
                bid_price_2d[:, i] = np.round(df[bp].values * 100).astype(np.int64)
            if bs in df.columns:
                bid_size_2d[:, i] = np.round(df[bs].values * 100).astype(np.int64)
            if ap in df.columns:
                ask_price_2d[:, i] = np.round(df[ap].values * 100).astype(np.int64)
            if asf in df.columns:
                ask_size_2d[:, i] = np.round(df[asf].values * 100).astype(np.int64)

        # Zero out negative values
        bid_price_2d = np.maximum(bid_price_2d, 0)
        bid_size_2d = np.maximum(bid_size_2d, 0)
        ask_price_2d = np.maximum(ask_price_2d, 0)
        ask_size_2d = np.maximum(ask_size_2d, 0)

        # Build snapshots as lightweight views into the arrays
        base_idx = len(store.snapshots)
        base_seq = self._sequence

        snapshots = []
        events = []

        for row_i in range(n_rows):
            snap = BookSnapshot(
                timestamp_us=int(ts_arr[row_i]),
                local_timestamp_us=int(local_ts_arr[row_i]),
                strike=strike,
                token_side=token_side,
                bid_prices=bid_price_2d[row_i],
                bid_sizes=bid_size_2d[row_i],
                ask_prices=ask_price_2d[row_i],
                ask_sizes=ask_size_2d[row_i],
                max_levels=max_level,
            )
            snapshots.append(snap)

            events.append(TimelineEvent(
                timestamp_us=int(ts_arr[row_i]),
                kind=EventKind.BOOK_SNAPSHOT,
                strike=strike,
                token_side=token_side,
                payload_index=base_idx + row_i,
                sequence=base_seq + row_i,
            ))

        store.snapshots.extend(snapshots)
        store.timeline.extend(events)
        self._sequence = base_seq + n_rows

        print(f"  Loaded {n_rows:,} snapshots for strike={strike} token={token_side.value}")

    def _load_trades(
        self, store: DataStore, strike: int, token_side: TokenSide
    ) -> None:
        path = self._find_trades_file(strike, token_side)
        if path is None:
            return

        df = pd.read_parquet(path)
        n_rows = len(df)

        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df["size"] = pd.to_numeric(df["size"], errors="coerce")

        ts_arr = df["timestamp_us"].values.astype(np.int64)
        price_arr = np.round(df["price"].values * 100).astype(np.int64)
        size_arr = np.round(df["size"].values * 100).astype(np.int64)

        side_col = "side" if "side" in df.columns else "taker_side"
        if side_col in df.columns:
            side_arr = df[side_col].values
        else:
            side_arr = np.array(["BUY"] * n_rows)

        base_idx = len(store.trades)
        base_seq = self._sequence

        trades = []
        events = []

        for row_i in range(n_rows):
            taker_side = Side.BUY if str(side_arr[row_i]).upper() == "BUY" else Side.SELL
            trade = TradeEvent(
                timestamp_us=int(ts_arr[row_i]),
                strike=strike,
                token_side=token_side,
                price_ticks=int(price_arr[row_i]),
                size_cs=int(size_arr[row_i]),
                taker_side=taker_side,
            )
            trades.append(trade)
            events.append(TimelineEvent(
                timestamp_us=int(ts_arr[row_i]),
                kind=EventKind.TRADE,
                strike=strike,
                token_side=token_side,
                payload_index=base_idx + row_i,
                sequence=base_seq + row_i,
            ))

        store.trades.extend(trades)
        store.timeline.extend(events)
        self._sequence = base_seq + n_rows
        print(f"  Loaded {n_rows:,} trades for strike={strike} token={token_side.value}")

    def _load_underlying_prices(self, store: DataStore) -> None:
        path = self.config.underlying_price_file
        if path is None or not path.exists():
            return

        df = pd.read_parquet(path)

        if hasattr(df.index, 'tz') and df.index.tz is not None:
            df.index = df.index.tz_convert("UTC")

        close_col = None
        for candidate in ["Close", "close", "nvda_close"]:
            if candidate in df.columns:
                close_col = candidate
                break
        if close_col is None:
            close_col = df.select_dtypes(include=[np.number]).columns[0]

        ts_arr = (df.index.astype(np.int64) // 1000).values
        bar_duration = self.config.underlying_bar_duration_us
        if bar_duration > 0:
            ts_arr = ts_arr + bar_duration
        price_arr = np.round(df[close_col].values * 100).astype(np.int64)

        base_idx = len(store.underlying_prices)
        base_seq = self._sequence

        for i in range(len(df)):
            store.underlying_prices.append(
                UnderlyingPrice(timestamp_us=int(ts_arr[i]), price_cents=int(price_arr[i]))
            )
            store.timeline.append(TimelineEvent(
                timestamp_us=int(ts_arr[i]),
                kind=EventKind.UNDERLYING_PRICE,
                strike=0,
                token_side=TokenSide.YES,
                payload_index=base_idx + i,
                sequence=base_seq + i,
            ))

        self._sequence = base_seq + len(df)
        print(f"  Loaded {len(df):,} underlying prices")

    def _find_book_file(self, strike: int, token_side: TokenSide) -> Path | None:
        data_dir = self.config.data_dir
        candidates = list(data_dir.rglob(f"*strike{strike}*.parquet"))
        candidates += list(data_dir.rglob(f"*book_snapshot*{strike}*.parquet"))
        if token_side == TokenSide.NO:
            candidates = [p for p in candidates if "_no_" in p.name.lower() or "_no." in p.name.lower()]
        seen = set()
        unique = [c for c in candidates if c not in seen and not seen.add(c)]
        return unique[0] if unique else None

    def _find_trades_file(self, strike: int, token_side: TokenSide) -> Path | None:
        data_dir = self.config.data_dir
        ts_lower = token_side.value.lower()
        candidates = list(data_dir.rglob(f"*trades*{strike}*{ts_lower}*.parquet"))
        candidates += list(data_dir.rglob(f"*trades*{ts_lower}*{strike}*.parquet"))
        return candidates[0] if candidates else None

    @staticmethod
    def _detect_depth_levels(df: pd.DataFrame) -> int:
        max_level = 0
        for col in df.columns:
            if col.startswith("bid_price_"):
                try:
                    level = int(col.split("_")[-1])
                    max_level = max(max_level, level + 1)
                except ValueError:
                    pass
        return max_level
