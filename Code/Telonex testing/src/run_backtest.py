"""
Main entry point for the Polymarket market-making backtest.

Loads data, runs the backtest with both L2 and Midpoint fill simulators,
and prints comparative results.
"""

import sys
import time
from pathlib import Path

import pandas as pd

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data_loader import load_all_data
from src.engine import BacktestEngine
from src.fill_simulator import L2FillSimulator, MidpointFillSimulator
from src.metrics import (
    BacktestMetrics,
    compute_metrics,
    compare_simulators,
    print_metrics,
)
from src.strategy import MarketMakingStrategy, StrategyParams


OUTPUT_DIR = PROJECT_ROOT / "output"


def run_single_backtest(
    data,
    fill_simulator,
    params: StrategyParams | None = None,
) -> tuple:
    """Run a single backtest with the given fill simulator."""
    params = params or StrategyParams()
    strategy = MarketMakingStrategy(params)

    engine = BacktestEngine(
        data=data,
        strategy=strategy,
        fill_simulator=fill_simulator,
        sigma=params.sigma,
    )

    t0 = time.time()
    state = engine.run()
    elapsed = time.time() - t0
    print(f"  Elapsed: {elapsed:.1f}s\n")

    # Compute metrics
    metrics = compute_metrics(
        fills=state.fills,
        position_history=state.position_history,
        simulator_name=fill_simulator.name,
    )

    return state, metrics


def save_results(
    state,
    metrics: BacktestMetrics,
    label: str,
) -> None:
    """Save detailed results to CSV files."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Save fills
    if state.fills:
        fills_data = []
        for f in state.fills:
            fills_data.append({
                "order_id": f.order_id,
                "strike": f.strike,
                "side": f.side.value,
                "price": f.price,
                "size": f.size,
                "fill_timestamp_us": f.fill_timestamp_us,
                "order_timestamp_us": f.order_timestamp_us,
            })
        fills_df = pd.DataFrame(fills_data)
        fills_path = OUTPUT_DIR / f"fills_{label}.csv"
        fills_df.to_csv(fills_path, index=False)
        print(f"  Saved fills to {fills_path}")

    # Save P&L history
    if state.pnl_history:
        pnl_df = pd.DataFrame(state.pnl_history)
        pnl_path = OUTPUT_DIR / f"pnl_history_{label}.csv"
        # Flatten positions dict
        if "positions" in pnl_df.columns:
            pos_df = pd.json_normalize(pnl_df["positions"])
            pos_df.columns = [f"pos_{c}" for c in pos_df.columns]
            pnl_df = pd.concat(
                [pnl_df.drop(columns=["positions"]), pos_df], axis=1
            )
        pnl_df.to_csv(pnl_path, index=False)
        print(f"  Saved P&L history to {pnl_path}")

    # Save fair value history
    if state.fair_value_history:
        fv_df = pd.DataFrame(state.fair_value_history)
        fv_path = OUTPUT_DIR / f"fair_values_{label}.csv"
        fv_df.to_csv(fv_path, index=False)
        print(f"  Saved fair values to {fv_path}")


def main():
    print("=" * 70)
    print("  POLYMARKET MARKET-MAKING BACKTEST")
    print("  NVDA Binary Options — March 30, 2026")
    print("=" * 70)
    print()

    # Strategy parameters
    params = StrategyParams(
        half_spread=0.02,
        max_position=50,
        min_edge=0.03,
        order_size=10,
        sigma=0.50,
    )

    print("Strategy Parameters:")
    print(f"  half_spread:   {params.half_spread}")
    print(f"  max_position:  {params.max_position}")
    print(f"  min_edge:      {params.min_edge}")
    print(f"  order_size:    {params.order_size}")
    print(f"  sigma:         {params.sigma}")
    print()

    # Load data
    data = load_all_data()

    # ---- Run 1: L2 Fill Simulator ----
    print("-" * 70)
    print("  RUN 1: L2 Fill Simulator")
    print("-" * 70)
    l2_state, l2_metrics = run_single_backtest(
        data, L2FillSimulator(), params
    )
    print_metrics(l2_metrics)
    save_results(l2_state, l2_metrics, "l2")
    print()

    # ---- Run 2: Midpoint Fill Simulator ----
    print("-" * 70)
    print("  RUN 2: Midpoint Fill Simulator")
    print("-" * 70)
    mid_state, mid_metrics = run_single_backtest(
        data, MidpointFillSimulator(), params
    )
    print_metrics(mid_metrics)
    save_results(mid_state, mid_metrics, "midpoint")
    print()

    # ---- Comparison ----
    compare_simulators(l2_metrics, mid_metrics)

    print("Backtest complete. Results saved to output/\n")


if __name__ == "__main__":
    main()
