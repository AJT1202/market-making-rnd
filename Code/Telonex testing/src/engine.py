"""
Minimal event-driven backtesting engine for Polymarket market making.

Processes orderbook snapshots chronologically, manages positions, cash,
resting orders, and delegates to strategy and fill simulator.
"""

import datetime
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.data_loader import MarketData, STRIKES
from src.fair_value import compute_fair_values, enforce_monotonicity, EXPIRY_US
from src.fill_simulator import (
    Fill,
    L2FillSimulator,
    MidpointFillSimulator,
    Order,
    Side,
)
from src.strategy import MarketMakingStrategy, StrategyParams


# Market resolutions
RESOLUTIONS = {
    160: True,   # YES
    165: True,   # YES
    170: False,  # NO
    175: False,  # NO
    180: False,  # NO
}

# US market hours in UTC: 13:30 - 20:00
MARKET_OPEN_UTC_HOUR = 13
MARKET_OPEN_UTC_MINUTE = 30
MARKET_CLOSE_UTC_HOUR = 20
MARKET_CLOSE_UTC_MINUTE = 0


def is_market_hours(timestamp_us: int) -> bool:
    """Check if timestamp falls within US equity market hours (9:30-16:00 ET)."""
    dt = datetime.datetime.fromtimestamp(
        timestamp_us / 1_000_000, tz=datetime.timezone.utc
    )
    hour, minute = dt.hour, dt.minute
    time_minutes = hour * 60 + minute
    open_minutes = MARKET_OPEN_UTC_HOUR * 60 + MARKET_OPEN_UTC_MINUTE
    close_minutes = MARKET_CLOSE_UTC_HOUR * 60 + MARKET_CLOSE_UTC_MINUTE
    return open_minutes <= time_minutes < close_minutes


@dataclass
class EngineState:
    """Complete engine state at any point in time."""
    positions: dict[int, float] = field(default_factory=lambda: {s: 0.0 for s in STRIKES})
    cash: float = 0.0
    resting_orders: dict[int, list[Order]] = field(
        default_factory=lambda: {s: [] for s in STRIKES}
    )
    fills: list[Fill] = field(default_factory=list)
    position_history: list[dict] = field(default_factory=list)
    pnl_history: list[dict] = field(default_factory=list)
    fair_value_history: list[dict] = field(default_factory=list)

    # Book state tracking: strike -> latest book row (as dict)
    latest_books: dict[int, dict] = field(default_factory=dict)

    # Counters
    snapshots_processed: int = 0
    orders_placed: int = 0
    orders_cancelled: int = 0


class BacktestEngine:
    """
    Event-driven backtesting engine.

    Processes orderbook snapshots in chronological order:
    1. Update book state for the snapshot's strike
    2. Check fills against new book
    3. Compute fair values
    4. Generate new orders via strategy
    5. Record state
    """

    def __init__(
        self,
        data: MarketData,
        strategy: MarketMakingStrategy,
        fill_simulator: L2FillSimulator | MidpointFillSimulator,
        sigma: float = 0.50,
    ):
        self.data = data
        self.strategy = strategy
        self.fill_sim = fill_simulator
        self.sigma = sigma
        self.state = EngineState()

    def run(self) -> EngineState:
        """
        Run the full backtest on the timeline.

        Returns the final EngineState with all history.
        """
        print(f"Running backtest with {self.fill_sim.name} fill simulator ...")

        timeline = self.data.timeline
        total_events = len(timeline)

        # Filter to market hours only
        market_hours_mask = timeline["timestamp_us"].apply(is_market_hours)
        market_timeline = timeline[market_hours_mask]
        n_market = len(market_timeline)
        print(f"  Total events: {total_events}, during market hours: {n_market}")

        if n_market == 0:
            print("  WARNING: No events during market hours!")
            return self.state

        # Progress tracking
        progress_interval = max(1, n_market // 20)
        last_progress = 0

        for i, (_, event) in enumerate(market_timeline.iterrows()):
            timestamp_us = int(event["timestamp_us"])
            strike = int(event["strike"])
            row_idx = int(event["row_idx"])

            # Get the book row for this event
            book_df = self.data.books[strike]
            if row_idx >= len(book_df):
                continue
            book_row = book_df.iloc[row_idx].to_dict()

            # 1. Update latest book state
            self.state.latest_books[strike] = book_row

            # 2. Check fills for resting orders on this strike
            resting = self.state.resting_orders[strike]
            if resting:
                new_fills = self.fill_sim.check_fills(resting, book_row, strike)
                for fill in new_fills:
                    self._process_fill(fill)
                # Remove filled orders
                filled_ids = {f.order_id for f in new_fills}
                self.state.resting_orders[strike] = [
                    o for o in self.state.resting_orders[strike]
                    if o.order_id not in filled_ids
                ]

            # 3. Compute fair values using latest NVDA price
            nvda_price = book_row.get("nvda_price")
            if nvda_price is None or np.isnan(nvda_price):
                continue

            fair_values = compute_fair_values(
                nvda_price, timestamp_us, STRIKES, self.sigma
            )
            fair_values = enforce_monotonicity(fair_values)

            # 4. Generate new orders (cancel-and-replace for this strike)
            # Cancel all resting orders for this strike
            cancelled = len(self.state.resting_orders[strike])
            self.state.orders_cancelled += cancelled
            self.state.resting_orders[strike] = []

            # Generate new orders for this strike only
            book_rows = {strike: book_row}
            orders = self.strategy.generate_orders(
                strike,
                fair_values[strike],
                book_row,
                self.state.positions[strike],
                timestamp_us,
            )

            for order in orders:
                self.state.resting_orders[order.strike].append(order)
                self.state.orders_placed += 1

            # 5. Record state periodically (every ~1000 events to save memory)
            self.state.snapshots_processed += 1
            if self.state.snapshots_processed % 500 == 0:
                self._record_state(timestamp_us, fair_values)

            # Progress output
            if i - last_progress >= progress_interval:
                pct = (i + 1) / n_market * 100
                dt = datetime.datetime.fromtimestamp(
                    timestamp_us / 1_000_000, tz=datetime.timezone.utc
                )
                n_fills = len(self.state.fills)
                print(
                    f"  [{pct:5.1f}%] {dt.strftime('%H:%M:%S')} UTC | "
                    f"fills: {n_fills} | orders placed: {self.state.orders_placed}"
                )
                last_progress = i

        # Record final state
        self._record_state(
            int(market_timeline["timestamp_us"].iloc[-1]),
            fair_values if 'fair_values' in dir() else {},
        )

        # Settle positions at market resolution
        self._settle()

        print(
            f"  Done. {self.state.snapshots_processed} snapshots processed, "
            f"{len(self.state.fills)} fills, "
            f"{self.state.orders_placed} orders placed."
        )

        return self.state

    def _process_fill(self, fill: Fill) -> None:
        """Process a fill: update positions and cash."""
        self.state.fills.append(fill)

        if fill.side == Side.BUY:
            self.state.positions[fill.strike] += fill.size
            self.state.cash -= fill.price * fill.size
        else:
            self.state.positions[fill.strike] -= fill.size
            self.state.cash += fill.price * fill.size

    def _record_state(self, timestamp_us: int, fair_values: dict) -> None:
        """Record a snapshot of current state."""
        pos_snapshot = dict(self.state.positions)
        self.state.position_history.append(pos_snapshot)

        # Compute mark-to-market P&L
        mtm = self.state.cash
        for strike, pos in self.state.positions.items():
            if strike in self.state.latest_books:
                mid = self.state.latest_books[strike].get("mid", 0)
                mtm += pos * mid

        self.state.pnl_history.append({
            "timestamp_us": timestamp_us,
            "cash": self.state.cash,
            "mtm_pnl": mtm,
            "positions": dict(self.state.positions),
            "n_fills": len(self.state.fills),
        })

        if fair_values:
            self.state.fair_value_history.append({
                "timestamp_us": timestamp_us,
                **{f"fv_{k}": v for k, v in fair_values.items()},
            })

    def _settle(self) -> None:
        """Settle all positions based on actual market resolutions."""
        print("  Settling positions at market resolution ...")
        for strike, resolved_yes in RESOLUTIONS.items():
            pos = self.state.positions[strike]
            settlement_price = 1.0 if resolved_yes else 0.0
            settlement_value = pos * settlement_price
            self.state.cash += settlement_value
            print(
                f"    Strike {strike}: pos={pos:+.1f}, "
                f"resolved={'YES' if resolved_yes else 'NO'}, "
                f"settlement=${settlement_value:+.2f}"
            )
            self.state.positions[strike] = 0.0

        print(f"    Final cash: ${self.state.cash:.2f}")
