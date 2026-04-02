"""Top-level entry point for running backtests."""

import time
from pathlib import Path

import pandas as pd

from bt_engine.config import EngineConfig
from bt_engine.data.loader import DataLoader
from bt_engine.engine.loop import BacktestEngine, BacktestResult
from bt_engine.strategy.interface import Strategy
from bt_engine.analytics.metrics import compute_metrics, print_metrics
from bt_engine.units import ticks_to_price, cs_to_shares, tc_to_dollars


def run_backtest(config: EngineConfig, strategy: Strategy) -> BacktestResult:
    """Load data, run engine, return result."""
    print("Loading data ...")
    t0 = time.time()
    data = DataLoader(config).load()
    load_time = time.time() - t0
    print(f"  Loaded in {load_time:.1f}s: {data.num_events:,} events, "
          f"{len(data.snapshots):,} snapshots, {len(data.trades):,} trades\n")

    engine = BacktestEngine(config=config, data=data, strategy=strategy)
    t0 = time.time()
    result = engine.run()
    run_time = time.time() - t0
    print(f"  Engine ran in {run_time:.1f}s\n")

    return result


def export_fills_csv(result: BacktestResult, output_dir: Path) -> Path:
    """Export fills to CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "fills.csv"

    rows = []
    for f in result.fills:
        rows.append({
            "order_id": f.order_id,
            "strike": f.strike,
            "token_side": f.token_side.value,
            "side": f.side.value,
            "price": ticks_to_price(f.price_ticks),
            "size": cs_to_shares(f.filled_cs),
            "timestamp_us": f.timestamp_us,
            "is_aggressive": f.is_aggressive,
            "queue_ahead": cs_to_shares(f.queue_ahead_at_fill),
        })

    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    print(f"  Fills exported to {path} ({len(rows)} rows)")
    return path


def export_summary(result: BacktestResult, output_dir: Path) -> Path:
    """Export a text summary."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "summary.txt"

    resolutions = result.config.event.resolutions
    metrics = compute_metrics(
        fills=result.fills,
        portfolio=result.portfolio,
        resolutions={k: v for k, v in resolutions.items() if v is not None},
        settlement_results=result.settlement_results,
    )

    # Capture print output
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_metrics(metrics)
    text = buf.getvalue()

    path.write_text(text)
    print(f"  Summary exported to {path}")
    return path
