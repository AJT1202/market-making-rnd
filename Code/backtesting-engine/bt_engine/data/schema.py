"""Data structures for the unified timeline.

BookSnapshot is a lightweight view into numpy arrays, not a materialized object.
TimelineEvent, TradeEvent, and UnderlyingPrice remain frozen dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bt_engine.types import EventKind, Side, TokenSide


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """A single event in the unified, chronologically-sorted timeline."""
    timestamp_us: int
    kind: EventKind
    strike: int
    token_side: TokenSide
    payload_index: int
    sequence: int

    def __lt__(self, other: TimelineEvent) -> bool:
        if self.timestamp_us != other.timestamp_us:
            return self.timestamp_us < other.timestamp_us
        if self.kind.value != other.kind.value:
            return self.kind.value < other.kind.value
        return self.sequence < other.sequence


class BookSnapshot:
    """Lightweight view into book data arrays.

    Instead of materializing tuples of BookLevel objects for each of
    140K+ snapshots, this stores references to numpy arrays and an
    index into them.
    """
    __slots__ = (
        "timestamp_us", "local_timestamp_us", "strike", "token_side",
        "_bid_prices", "_bid_sizes", "_ask_prices", "_ask_sizes",
        "_idx", "_max_levels",
    )

    def __init__(
        self,
        timestamp_us: int,
        local_timestamp_us: int,
        strike: int,
        token_side: TokenSide,
        bid_prices: np.ndarray,  # shape (max_levels,), ticks
        bid_sizes: np.ndarray,   # shape (max_levels,), centishares
        ask_prices: np.ndarray,
        ask_sizes: np.ndarray,
        idx: int = 0,
        max_levels: int = 25,
    ):
        self.timestamp_us = timestamp_us
        self.local_timestamp_us = local_timestamp_us
        self.strike = strike
        self.token_side = token_side
        self._bid_prices = bid_prices
        self._bid_sizes = bid_sizes
        self._ask_prices = ask_prices
        self._ask_sizes = ask_sizes
        self._idx = idx
        self._max_levels = max_levels

    @property
    def best_bid_ticks(self) -> int:
        v = self._bid_prices[0]
        return int(v) if v > 0 else 0

    @property
    def best_ask_ticks(self) -> int:
        v = self._ask_prices[0]
        return int(v) if v > 0 else 0

    @property
    def best_bid_size_cs(self) -> int:
        if self._bid_prices[0] > 0:
            return int(self._bid_sizes[0])
        return 0

    @property
    def best_ask_size_cs(self) -> int:
        if self._ask_prices[0] > 0:
            return int(self._ask_sizes[0])
        return 0

    @property
    def mid_ticks_x2(self) -> int:
        bb = self.best_bid_ticks
        ba = self.best_ask_ticks
        if bb > 0 and ba > 0:
            return bb + ba
        return 0

    @property
    def spread_ticks(self) -> int:
        bb = self.best_bid_ticks
        ba = self.best_ask_ticks
        if bb > 0 and ba > 0:
            return ba - bb
        return 0

    @property
    def is_valid(self) -> bool:
        bb = self.best_bid_ticks
        ba = self.best_ask_ticks
        return bb > 0 and ba > 0 and ba > bb

    def depth_at_price(self, price_ticks: int) -> int:
        for i in range(self._max_levels):
            if self._bid_prices[i] == price_ticks:
                return int(self._bid_sizes[i])
            if self._ask_prices[i] == price_ticks:
                return int(self._ask_sizes[i])
            if self._bid_prices[i] <= 0 and self._ask_prices[i] <= 0:
                break
        return 0

    def total_bid_depth_cs(self) -> int:
        total = 0
        for i in range(self._max_levels):
            if self._bid_prices[i] <= 0:
                break
            total += int(self._bid_sizes[i])
        return total

    def total_ask_depth_cs(self) -> int:
        total = 0
        for i in range(self._max_levels):
            if self._ask_prices[i] <= 0:
                break
            total += int(self._ask_sizes[i])
        return total


@dataclass(frozen=True, slots=True)
class TradeEvent:
    """A single trade from Telonex trades channel."""
    timestamp_us: int
    strike: int
    token_side: TokenSide
    price_ticks: int
    size_cs: int
    taker_side: Side


@dataclass(frozen=True, slots=True)
class UnderlyingPrice:
    """Stock/index price update."""
    timestamp_us: int
    price_cents: int


# Keep BookLevel for compatibility (used by fill engine internals)
@dataclass(frozen=True, slots=True)
class BookLevel:
    """A single price level in the orderbook."""
    price_ticks: int
    size_cs: int
