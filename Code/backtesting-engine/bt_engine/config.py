"""Engine configuration as dataclasses.

All configuration is immutable after construction. No config files —
just construct the dataclasses directly for research iteration speed.
"""

from dataclasses import dataclass, field
from pathlib import Path

from bt_engine.types import FillMode, PositionMode, QueueMode


@dataclass(frozen=True)
class LatencyConfig:
    """Order latency parameters in microseconds."""
    submit_us: int = 200_000       # 200ms: time from decision to order active
    visible_us: int = 800_000      # 800ms: time from active to visible in book
    cancel_us: int = 200_000       # 200ms: time from cancel request to effective


@dataclass(frozen=True)
class FillConfig:
    """Fill simulation parameters."""
    mode: FillMode = FillMode.SNAPSHOT_ONLY
    queue_mode: QueueMode = QueueMode.CONSERVATIVE
    cancel_discount: float = 0.5   # Fraction of depth decrease treated as cancellations (snapshot mode)


@dataclass(frozen=True)
class MarketHoursConfig:
    """US equity market hours in UTC."""
    open_hour: int = 13
    open_minute: int = 30
    close_hour: int = 20
    close_minute: int = 0
    trade_outside_hours: bool = True  # Polymarket trades 24/7, but FV only valid during market hours


@dataclass(frozen=True)
class MarketConfig:
    """Configuration for a single binary market (one strike)."""
    strike: int                    # Strike price in dollars
    resolution: bool | None = None # True=YES, False=NO, None=unresolved
    market_slug: str = ""
    token_side_available: tuple[str, ...] = ("YES",)  # Which token sides have data


@dataclass(frozen=True)
class EventConfig:
    """Configuration for an event (group of strikes on the same underlying)."""
    event_slug: str
    ticker: str
    expiry_utc_us: int             # Microsecond timestamp of market close/resolution
    markets: tuple[MarketConfig, ...] = ()

    @property
    def strikes(self) -> list[int]:
        return sorted(m.strike for m in self.markets)

    @property
    def resolutions(self) -> dict[int, bool | None]:
        return {m.strike: m.resolution for m in self.markets}


@dataclass(frozen=True)
class EngineConfig:
    """Top-level engine configuration."""
    # Event to backtest
    event: EventConfig = field(default_factory=lambda: EventConfig(
        event_slug="",
        ticker="",
        expiry_utc_us=0,
    ))

    # Data paths
    data_dir: Path = Path("data")
    underlying_price_file: Path | None = None

    # Sub-configs
    latency: LatencyConfig = field(default_factory=LatencyConfig)
    fill: FillConfig = field(default_factory=FillConfig)
    market_hours: MarketHoursConfig = field(default_factory=MarketHoursConfig)

    # Portfolio
    position_mode: PositionMode = PositionMode.COLLATERAL_BACKED
    initial_cash_tc: int = 100_000_000  # $10,000 default (100M tick-centishares)

    # Fair value
    sigma: float = 0.50            # Annualized IV for Black-Scholes
    risk_free_rate: float = 0.0

    # Output
    output_dir: Path = Path("output")
    save_fills_csv: bool = True
    save_pnl_csv: bool = True

    # Engine behavior
    seed: int = 42                 # RNG seed for probabilistic queue mode
    only_market_hours: bool = True # Only run strategy during US market hours

    # Underlying price bar configuration
    underlying_bar_duration_us: int = 60_000_000  # Duration of underlying price bars in microseconds (default 60s = 1 minute). Set to 0 for tick data.
    fair_value_staleness_us: int = 0               # Artificial delay on fair value updates to model propagation delay between stock market and Polymarket. 0 = no delay.
