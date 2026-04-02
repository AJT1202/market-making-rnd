"""DataStore: holds all loaded data for a single backtest run."""

from dataclasses import dataclass, field

from bt_engine.types import FillMode, TokenSide
from bt_engine.data.schema import (
    BookSnapshot,
    TimelineEvent,
    TradeEvent,
    UnderlyingPrice,
)


@dataclass
class DataStore:
    """Container for all loaded and aligned market data.

    The timeline is the master clock — the engine iterates through it
    in order. Payload stores hold the actual data, indexed by
    TimelineEvent.payload_index.
    """
    # Master clock (pre-sorted)
    timeline: list[TimelineEvent] = field(default_factory=list)

    # Payload stores (indexed by payload_index in TimelineEvent)
    snapshots: list[BookSnapshot] = field(default_factory=list)
    trades: list[TradeEvent] = field(default_factory=list)
    underlying_prices: list[UnderlyingPrice] = field(default_factory=list)

    # Metadata
    strikes: list[int] = field(default_factory=list)
    fill_mode: FillMode = FillMode.SNAPSHOT_ONLY

    # Quick-access indexes built after loading
    # (strike, token_side) -> index of latest snapshot in self.snapshots
    _latest_snapshot_idx: dict[tuple[int, TokenSide], int] = field(
        default_factory=dict, repr=False
    )

    def get_snapshot(self, idx: int) -> BookSnapshot:
        return self.snapshots[idx]

    def get_trade(self, idx: int) -> TradeEvent:
        return self.trades[idx]

    def get_underlying_price(self, idx: int) -> UnderlyingPrice:
        return self.underlying_prices[idx]

    def update_latest_snapshot(self, strike: int, token_side: TokenSide, idx: int) -> None:
        self._latest_snapshot_idx[(strike, token_side)] = idx

    def latest_snapshot(self, strike: int, token_side: TokenSide) -> BookSnapshot | None:
        idx = self._latest_snapshot_idx.get((strike, token_side))
        if idx is not None:
            return self.snapshots[idx]
        return None

    @property
    def num_events(self) -> int:
        return len(self.timeline)

    @property
    def time_range_us(self) -> tuple[int, int]:
        if not self.timeline:
            return (0, 0)
        return (self.timeline[0].timestamp_us, self.timeline[-1].timestamp_us)
