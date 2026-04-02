"""Replicate NVDA POC results with the new backtesting engine.

POC known results (L2 simulator):
  - 188 fills
  - -$18.10 total PnL
  - $165 strike: 163 fills
  - $170 strike: 25 fills
  - Others: 0 fills

The new engine uses integer arithmetic and a proper order lifecycle,
so results will differ slightly but should be in the same ballpark.
"""

import datetime
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bt_engine.config import (
    EngineConfig,
    EventConfig,
    FillConfig,
    LatencyConfig,
    MarketConfig,
    MarketHoursConfig,
)
from bt_engine.types import FillMode
from bt_engine.strategy.probability_quoting import ProbabilityQuotingStrategy
from bt_engine.runner import run_backtest, export_fills_csv, export_summary
from bt_engine.analytics.metrics import compute_metrics, print_metrics
from bt_engine.units import tc_to_dollars

# NVDA March 30, 2026 close at 4:00 PM ET = 20:00 UTC
EXPIRY = datetime.datetime(2026, 3, 30, 20, 0, 0, tzinfo=datetime.timezone.utc)
EXPIRY_US = int(EXPIRY.timestamp() * 1_000_000)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "Telonex testing" / "data" / "telonex" / "nvda-poc"
UNDERLYING_FILE = DATA_DIR / "nvda_prices_1m.parquet"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "nvda-poc"


def main():
    print("=" * 70)
    print("  NVDA POC REPLICATION — New Backtesting Engine")
    print("=" * 70)
    print()

    # Match POC parameters exactly
    # POC: half_spread=0.02 (2 ticks), max_position=50 (5000 cs),
    #       min_edge=0.03 (3 ticks), order_size=10 (1000 cs), sigma=0.50
    strategy = ProbabilityQuotingStrategy(
        half_spread_ticks=2,
        max_position_cs=5000,
        min_edge_ticks=3,
        order_size_cs=1000,
    )

    config = EngineConfig(
        event=EventConfig(
            event_slug="nvda-close-above-on-march-30-2026",
            ticker="NVDA",
            expiry_utc_us=EXPIRY_US,
            markets=tuple(
                MarketConfig(strike=s, resolution=r)
                for s, r in [
                    (160, True), (165, True), (170, False),
                    (175, False), (180, False),
                ]
            ),
        ),
        data_dir=DATA_DIR,
        underlying_price_file=UNDERLYING_FILE,
        fill=FillConfig(
            mode=FillMode.SNAPSHOT_ONLY,
            cancel_discount=0.5,
        ),
        latency=LatencyConfig(
            # No latency for POC comparison (POC had instant orders)
            submit_us=0,
            visible_us=0,
            cancel_us=0,
        ),
        market_hours=MarketHoursConfig(
            open_hour=13,
            open_minute=30,
            close_hour=20,
            close_minute=0,
        ),
        sigma=0.50,
        initial_cash_tc=100_000_000,  # $10,000
        output_dir=OUTPUT_DIR,
        only_market_hours=True,
    )

    print(f"Data dir: {DATA_DIR}")
    print(f"Strategy: half_spread=2t, max_pos=5000cs, min_edge=3t, order_size=1000cs")
    print(f"Fill mode: SNAPSHOT_ONLY (no latency, matching POC)")
    print()

    result = run_backtest(config, strategy)

    # Compute and print metrics
    resolutions = {k: v for k, v in config.event.resolutions.items() if v is not None}
    metrics = compute_metrics(
        fills=result.fills,
        portfolio=result.portfolio,
        resolutions=resolutions,
        settlement_results=result.settlement_results,
    )
    print_metrics(metrics)

    # Export
    print("\nExporting results ...")
    export_fills_csv(result, OUTPUT_DIR)
    export_summary(result, OUTPUT_DIR)

    # Compare with POC
    print("\n" + "=" * 70)
    print("  COMPARISON WITH POC")
    print("=" * 70)
    print(f"  {'Metric':<25} {'POC (L2)':>12} {'New Engine':>12}")
    print("  " + "-" * 52)
    print(f"  {'Total fills':<25} {'188':>12} {result.total_fills:>12}")

    # Per-strike fill counts
    strike_fills = {}
    for f in result.fills:
        strike_fills[f.strike] = strike_fills.get(f.strike, 0) + 1

    poc_fills = {160: 0, 165: 163, 170: 25, 175: 0, 180: 0}
    for strike in [160, 165, 170, 175, 180]:
        new_count = strike_fills.get(strike, 0)
        poc_count = poc_fills[strike]
        print(f"  {'Strike ' + str(strike) + ' fills':<25} {poc_count:>12} {new_count:>12}")

    pnl = tc_to_dollars(result.portfolio.cash_tc - config.initial_cash_tc)
    print(f"  {'Total PnL':<25} {'$-18.10':>12} ${pnl:>11.2f}")
    print("  " + "-" * 52)
    print()


if __name__ == "__main__":
    main()
