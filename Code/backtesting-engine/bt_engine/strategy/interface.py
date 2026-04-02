"""Strategy protocol and data structures for engine/strategy communication."""

from dataclasses import dataclass, field
from typing import Protocol

from bt_engine.types import Side, TokenSide


@dataclass(frozen=True)
class StrategyAction:
    """An action the strategy wants to take."""

    kind: str                           # "PLACE" or "CANCEL"
    strike: int = 0
    token_side: TokenSide = TokenSide.YES
    side: Side = Side.BUY
    price_ticks: int = 0
    size_cs: int = 0
    order_id: str = ""                  # For CANCEL actions


@dataclass(frozen=True)
class StrategyUpdate:
    """Market state delivered to the strategy per-strike."""

    timestamp_us: int
    strike: int
    token_side: TokenSide
    best_bid_ticks: int
    best_ask_ticks: int
    best_bid_size_cs: int
    best_ask_size_cs: int
    mid_ticks_x2: int           # Double midpoint (avoids float); actual mid = mid_ticks_x2 / 2
    spread_ticks: int
    fair_value_bps: int         # YES fair value in basis points (0-10000)
    underlying_price_cents: int
    position_yes_cs: int
    position_no_cs: int
    available_cash_tc: int


class Strategy(Protocol):
    """Protocol that all backtest strategies must satisfy."""

    def on_market_update(self, update: StrategyUpdate) -> list[StrategyAction]:
        """Called when market state changes. Return actions to take."""
        ...

    def on_fill(
        self,
        strike: int,
        token_side: TokenSide,
        side: Side,
        price_ticks: int,
        size_cs: int,
    ) -> list[StrategyAction]:
        """Called when a fill occurs. Return additional actions if desired."""
        ...
