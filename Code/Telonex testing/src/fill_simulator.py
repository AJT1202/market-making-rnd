"""
Fill simulation using L2 orderbook depth and a midpoint-only baseline.

Two simulators:
  1. L2FillSimulator  — uses actual orderbook depth for realistic fill modeling
  2. MidpointFillSimulator — uses only midpoint crossing (optimistic baseline)
"""

from dataclasses import dataclass, field
from enum import Enum


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Order:
    """A resting limit order."""
    order_id: int
    strike: int
    side: Side
    price: float
    size: float
    timestamp_us: int  # when the order was placed

    def __repr__(self) -> str:
        return (
            f"Order({self.order_id}, {self.strike}, {self.side.value}, "
            f"px={self.price:.2f}, sz={self.size})"
        )


@dataclass
class Fill:
    """A completed fill."""
    order_id: int
    strike: int
    side: Side
    price: float
    size: float
    fill_timestamp_us: int
    order_timestamp_us: int

    @property
    def signed_size(self) -> float:
        return self.size if self.side == Side.BUY else -self.size


class L2FillSimulator:
    """
    Fill simulator using L2 orderbook depth.

    For aggressive orders (crossing the spread):
      - BUY at price >= best_ask -> immediate fill at best_ask
      - SELL at price <= best_bid -> immediate fill at best_bid

    For resting orders (at or behind BBO):
      - Conservative: assume our order is at the back of the queue
      - Fill probabilistically based on how much size is at our price level
      - Use a fill probability = min(1.0, trade_proxy / (queue_size + our_size))
        where trade_proxy is estimated from changes in book state
      - Simplified: resting orders fill when the BBO moves through our price
    """

    def __init__(self):
        self.name = "L2"
        self._prev_books: dict[int, dict] = {}  # strike -> previous BBO snapshot

    def check_fills(
        self,
        resting_orders: list[Order],
        book_row: dict,
        strike: int,
    ) -> list[Fill]:
        """
        Check which resting orders fill against the current book snapshot.

        Parameters
        ----------
        resting_orders : list[Order]
            Currently resting orders for this strike.
        book_row : dict
            Current orderbook snapshot (single row as dict).

        Returns
        -------
        list[Fill]
            List of fills that occurred.
        """
        fills = []
        best_bid = book_row.get("best_bid", 0)
        best_ask = book_row.get("best_ask", 0)
        timestamp_us = book_row["timestamp_us"]

        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            return fills

        # Get previous BBO for this strike to detect book changes
        prev = self._prev_books.get(strike)

        for order in resting_orders:
            if order.side == Side.BUY:
                filled = self._check_buy_fill(
                    order, best_bid, best_ask, book_row, prev, timestamp_us
                )
            else:
                filled = self._check_sell_fill(
                    order, best_bid, best_ask, book_row, prev, timestamp_us
                )

            if filled is not None:
                fills.append(filled)

        # Update previous state
        self._prev_books[strike] = {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "best_bid_size": book_row.get("best_bid_size", 0),
            "best_ask_size": book_row.get("best_ask_size", 0),
        }

        return fills

    def _check_buy_fill(
        self,
        order: Order,
        best_bid: float,
        best_ask: float,
        book_row: dict,
        prev: dict | None,
        timestamp_us: int,
    ) -> Fill | None:
        """Check if a BUY order fills."""
        # Case 1: Order price crosses the spread (aggressive)
        if order.price >= best_ask:
            return Fill(
                order_id=order.order_id,
                strike=order.strike,
                side=order.side,
                price=order.price,  # fill at our limit price
                size=order.size,
                fill_timestamp_us=timestamp_us,
                order_timestamp_us=order.timestamp_us,
            )

        # Case 2: Resting order — the ask has dropped to our price
        # This means someone sold at our price level
        if prev is not None:
            prev_ask = prev.get("best_ask", 0)
            # Ask moved down through our price -> we got filled
            if prev_ask > order.price >= best_ask:
                return Fill(
                    order_id=order.order_id,
                    strike=order.strike,
                    side=order.side,
                    price=order.price,
                    size=order.size,
                    fill_timestamp_us=timestamp_us,
                    order_timestamp_us=order.timestamp_us,
                )

        # Case 3: Resting at the bid — queue-based fill estimation
        if abs(order.price - best_bid) < 1e-9:
            # We're at the bid. Estimate fill probability based on
            # whether the bid-side liquidity decreased (trades happened).
            if prev is not None and abs(prev.get("best_bid", 0) - best_bid) < 1e-9:
                prev_bid_size = prev.get("best_bid_size", 0)
                curr_bid_size = book_row.get("best_bid_size", 0)
                if prev_bid_size > 0 and curr_bid_size < prev_bid_size:
                    consumed = prev_bid_size - curr_bid_size
                    # Our order is at the back. Fill if enough was consumed.
                    # We assume queue = prev_bid_size (we're at the back)
                    queue_ahead = max(0, prev_bid_size - order.size)
                    if consumed > queue_ahead:
                        return Fill(
                            order_id=order.order_id,
                            strike=order.strike,
                            side=order.side,
                            price=order.price,
                            size=order.size,
                            fill_timestamp_us=timestamp_us,
                            order_timestamp_us=order.timestamp_us,
                        )

        return None

    def _check_sell_fill(
        self,
        order: Order,
        best_bid: float,
        best_ask: float,
        book_row: dict,
        prev: dict | None,
        timestamp_us: int,
    ) -> Fill | None:
        """Check if a SELL order fills."""
        # Case 1: Order price crosses the spread (aggressive)
        if order.price <= best_bid:
            return Fill(
                order_id=order.order_id,
                strike=order.strike,
                side=order.side,
                price=order.price,
                size=order.size,
                fill_timestamp_us=timestamp_us,
                order_timestamp_us=order.timestamp_us,
            )

        # Case 2: Resting order — the bid has risen to our price
        if prev is not None:
            prev_bid = prev.get("best_bid", 0)
            if prev_bid < order.price <= best_bid:
                return Fill(
                    order_id=order.order_id,
                    strike=order.strike,
                    side=order.side,
                    price=order.price,
                    size=order.size,
                    fill_timestamp_us=timestamp_us,
                    order_timestamp_us=order.timestamp_us,
                )

        # Case 3: Resting at the ask — queue-based fill
        if abs(order.price - best_ask) < 1e-9:
            if prev is not None and abs(prev.get("best_ask", 0) - best_ask) < 1e-9:
                prev_ask_size = prev.get("best_ask_size", 0)
                curr_ask_size = book_row.get("best_ask_size", 0)
                if prev_ask_size > 0 and curr_ask_size < prev_ask_size:
                    consumed = prev_ask_size - curr_ask_size
                    queue_ahead = max(0, prev_ask_size - order.size)
                    if consumed > queue_ahead:
                        return Fill(
                            order_id=order.order_id,
                            strike=order.strike,
                            side=order.side,
                            price=order.price,
                            size=order.size,
                            fill_timestamp_us=timestamp_us,
                            order_timestamp_us=order.timestamp_us,
                        )

        return None


class MidpointFillSimulator:
    """
    Simplified fill simulator using only midpoint price.

    An order fills if our price crosses the midpoint:
      - BUY fills if order_price >= mid
      - SELL fills if order_price <= mid

    This is the optimistic baseline — it ignores spread and depth entirely.
    """

    def __init__(self):
        self.name = "Midpoint"

    def check_fills(
        self,
        resting_orders: list[Order],
        book_row: dict,
        strike: int,
    ) -> list[Fill]:
        fills = []
        mid = book_row.get("mid", 0)
        timestamp_us = book_row["timestamp_us"]

        if mid <= 0:
            return fills

        for order in resting_orders:
            if order.side == Side.BUY and order.price >= mid:
                fills.append(
                    Fill(
                        order_id=order.order_id,
                        strike=order.strike,
                        side=order.side,
                        price=order.price,
                        size=order.size,
                        fill_timestamp_us=timestamp_us,
                        order_timestamp_us=order.timestamp_us,
                    )
                )
            elif order.side == Side.SELL and order.price <= mid:
                fills.append(
                    Fill(
                        order_id=order.order_id,
                        strike=order.strike,
                        side=order.side,
                        price=order.price,
                        size=order.size,
                        fill_timestamp_us=timestamp_us,
                        order_timestamp_us=order.timestamp_us,
                    )
                )

        return fills
